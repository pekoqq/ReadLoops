#!/usr/bin/env bash
# 为 ReadLoops 安装 MinerU 文档解析环境（可选组件）。
#
# 为什么单独建环境：
#   MinerU 要求 Python 3.10–3.13、磁盘 20GB+、内存 16GB+，
#   直接装进主环境既可能版本不兼容，也会把主环境拖得很重。
#   所以这里建一个独立 venv，ReadLoops 用子进程调用它。
#
# 用法：bash tools/setup_mineru.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$PROJECT_ROOT/tools/mineru-env"
MIRROR="https://mirrors.aliyun.com/pypi/simple"

echo "==> ReadLoops · MinerU 环境安装"
echo "    目标目录: $ENV_DIR"

# ---- 1. 找一个 3.10–3.13、且架构与主机一致的 Python
#
# 为什么强调架构：Apple Silicon 机器上若解释器是 x86_64（Rosetta / Intel 版），
# PyTorch 装不上 —— 官方早已停止发布 macOS x86 的 wheel，pip 只会报
# "No matching distribution found for torch"，很难看出是架构问题。
# 实测踩过这个坑：x86_64 的 3.13 环境里怎么装都失败。
HOST_ARCH="$(uname -m)"

check_python() {
  local cand="$1"
  [ -n "$cand" ] && [ -x "$cand" ] || return 1
  local ver arch
  ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
  case "$ver" in 3.10|3.11|3.12|3.13) ;; *) return 1 ;; esac
  arch="$("$cand" -c 'import platform; print(platform.machine())' 2>/dev/null || echo "unknown")"
  if [ "$HOST_ARCH" = "arm64" ] && [ "$arch" != "arm64" ]; then
    echo "    (跳过 $cand：架构为 $arch，与主机的 arm64 不符)" >&2
    return 1
  fi
  echo "$cand"
}

find_python() {
  for cand in \
    "${READLOOPS_MINERU_PYTHON:-}" \
    "$(command -v python3.13 || true)" \
    "$(command -v python3.12 || true)" \
    "$(command -v python3.11 || true)" \
    "$(command -v python3.10 || true)" \
    "/opt/homebrew/bin/python3.13" \
    "/usr/local/bin/python3.13" \
    "$(command -v python3 || true)"
  do
    local found; found="$(check_python "$cand" 2>/dev/null || true)"
    [ -n "$found" ] && { echo "$found"; return 0; }
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"

# ---- 1.5 找不到就尝试用 uv 下载一个（无需 brew 权限，且能拿到 arm64 版本）
if [ -z "${PYTHON_BIN:-}" ] && command -v uv >/dev/null 2>&1; then
  echo "==> 本地没有合适的 Python，用 uv 下载一个 3.13"
  if uv python install 3.13 >/dev/null 2>&1; then
    PYTHON_BIN="$(uv python find 3.13 2>/dev/null || true)"
    echo "    uv 提供: $PYTHON_BIN"
  fi
fi

if [ -z "${PYTHON_BIN:-}" ]; then
  echo "✗ 找不到可用的 Python（需 3.10–3.13，且架构与主机一致）。" >&2
  echo "  MinerU 不支持 3.14 及以上；Apple Silicon 上也不能用 x86_64 的解释器。" >&2
  echo "  推荐做法（不需要管理员权限）：" >&2
  echo "      curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "      uv python install 3.13" >&2
  echo "      bash tools/setup_mineru.sh" >&2
  echo "  或手动指定：READLOOPS_MINERU_PYTHON=/path/to/python3.13 bash tools/setup_mineru.sh" >&2
  exit 1
fi
echo "    使用解释器: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1), $("$PYTHON_BIN" -c 'import platform;print(platform.machine())'))"

# ---- 2. 磁盘检查（MinerU 官方建议 20GB 以上）
AVAIL_GB=$(df -g "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2 {print $4}' || echo "0")
if [ "${AVAIL_GB:-0}" -lt 20 ] 2>/dev/null; then
  echo "⚠️  可用磁盘约 ${AVAIL_GB}GB，MinerU 官方建议 20GB 以上，可能不够。"
  read -r -p "    仍要继续？[y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || exit 1
fi

# ---- 3. 建环境并安装
if [ -d "$ENV_DIR" ]; then
  echo "==> 已存在环境，直接更新依赖"
else
  echo "==> 创建虚拟环境"
  "$PYTHON_BIN" -m venv "$ENV_DIR"
fi

# 有些 IDE/Agent 环境会通过 PYTHONPATH 注入 sitecustomize，干扰 pip 的
# 临时文件清理。安装过程与 MinerU 本身都不需要它，因此显式清空。
CLEAN_ENV=(env -u PYTHONPATH -u PYTHONSTARTUP -u PYTHONHOME)

echo "==> 升级 pip"
"${CLEAN_ENV[@]}" "$ENV_DIR/bin/pip" install -q --upgrade pip -i "$MIRROR"

echo "==> 安装 mineru[all]（依赖较多，请耐心等待）"
"${CLEAN_ENV[@]}" "$ENV_DIR/bin/pip" install -U "mineru[all]" -i "$MIRROR"

# ---- 4. 验证
echo "==> 验证"
if "${CLEAN_ENV[@]}" "$ENV_DIR/bin/mineru" --help >/dev/null 2>&1; then
  echo "✓ MinerU 安装完成"
  echo ""
  echo "  首次解析 PDF 时会自动下载模型（数 GB），之后走本地缓存。"
  echo "  如果下载慢，可切换国内镜像："
  echo "      export MINERU_MODEL_SOURCE=modelscope"
else
  echo "✗ 安装后 mineru 命令仍不可用，请检查上面的报错。" >&2
  exit 1
fi
