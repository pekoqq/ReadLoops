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

# ---- 1. 找一个 3.10–3.13 的 Python
find_python() {
  for cand in \
    "$(command -v python3.13 || true)" \
    "$(command -v python3.12 || true)" \
    "$(command -v python3.11 || true)" \
    "$(command -v python3.10 || true)" \
    "/opt/homebrew/bin/python3.13" \
    "/usr/local/bin/python3.13" \
    "$(command -v python3 || true)"
  do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
    case "$ver" in
      3.10|3.11|3.12|3.13) echo "$cand"; return 0 ;;
    esac
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [ -z "${PYTHON_BIN:-}" ]; then
  echo "✗ 找不到 Python 3.10–3.13。MinerU 不支持 3.14 及以上。" >&2
  echo "  请先安装，例如：brew install python@3.13" >&2
  exit 1
fi
echo "    使用解释器: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

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

echo "==> 升级 pip"
"$ENV_DIR/bin/pip" install -q --upgrade pip -i "$MIRROR"

echo "==> 安装 mineru[all]（依赖较多，请耐心等待）"
"$ENV_DIR/bin/pip" install -U "mineru[all]" -i "$MIRROR"

# ---- 4. 验证
echo "==> 验证"
if "$ENV_DIR/bin/mineru" --help >/dev/null 2>&1; then
  echo "✓ MinerU 安装完成"
  echo ""
  echo "  首次解析 PDF 时会自动下载模型（数 GB），之后走本地缓存。"
  echo "  如果下载慢，可切换国内镜像："
  echo "      export MINERU_MODEL_SOURCE=modelscope"
else
  echo "✗ 安装后 mineru 命令仍不可用，请检查上面的报错。" >&2
  exit 1
fi
