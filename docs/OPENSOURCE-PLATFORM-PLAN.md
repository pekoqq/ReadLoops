# ReadLoops 开源适用性预案

> 版本：草案 v1（2026-09-16）
> 目标：让 ReadLoops 从「开发者本机能跑」变成「任何人任何系统都能装、能用、数据可信」
> 所有「现状」都来自实测，不是推测

---

## 〇、先讲清楚一个硬约束：真题不能打包

你提到「四六级词汇的来源和真题的来源……打包给到用户」。这里有一条**必须先说清的法律边界**：

| 数据 | 来源 | 许可 | 能否随开源包分发 |
|---|---|---|---|
| **CET4/6/考研/雅思/托福词表 + 释义** | ECDICT | **MIT** | ✅ **可以** |
| **真题原文**（547 篇） | 爬自 english-exam.lazynote.cn | 无授权；且原文多摘自外刊（The Economist 等） | ❌ **不可以** |
| **短语库**（1037 条） | 从真题语料统计提取 | 短语本身是语言事实，不具版权 | ✅ 可以（但释义需按来源署名） |
| 短语释义 | ECDICT / Wikidata / Wiktionary | MIT / **CC0** / CC BY-SA | ✅（Wiktionary 需署名 + 相同方式共享） |
| **派生统计**（覆盖率曲线、风格参数、分档词频） | 从真题语料算出 | 聚合统计量，非原文 | ✅ **可以** |
| 用户自己的数据（掌握度、遇见记录、生词本） | 用户本机产生 | — | 留在本地，不上传 |

**所以预案的核心思路是：不打包真题原文，改打包「从真题算出来的结论」。**

具体做法：
1. 覆盖率计算目前需要读 `exam_corpus.json`。改为**预计算一份聚合画像**
   `exam_profile.json`：每个词频档位有多少 token，多少总 token。
   **几 KB、纯数字、不含任何原文** —— 覆盖率、缺口、i+1 判定全部照常工作。
2. 风格基线（平均句长 19 词、复合句 65%……）**已经**是硬编码在 `ai.py` 里的数字，
   本来就是可分发的事实。
3. 真题原文仍由 `tools/crawl_exam_corpus.py` 让用户**自己抓**（现有能力），
   用于「文章相似度匹配」这个可选功能。**默认不抓也能用。**

这样既做到「开箱即用、数据可信」，又不把别人的版权内容塞进开源包。

> ⚠️ 需要你确认这个取舍。若你坚持打包真题原文，那这个仓库就不能作为公开开源项目分发，
> 只能作为**私有自用包**（GitHub 上应设为 private）。

---

## 一、跨平台无 bug（每个系统都能装能用）

### 现状实测

| 项 | 结果 |
|---|---|
| `app/` 里硬编码 macOS 路径 | ✅ **0 处** |
| `app/` 里平台分支 | ✅ 仅 `config.py` 2 处（数据目录，正确处理了 win32/darwin/linux） |
| 路径处理 | ✅ 全部 `pathlib` |
| 文本编码 | ⚠️ 10 处显式 `encoding=`，但 Windows 控制台需 `_force_utf8_io()`（已有） |
| **开机自启** | ❌ **只有 macOS 的 launchd**（`install-service.sh`），Windows/Linux 没有 |
| **`tools/` 里的 macOS 硬编码** | ❌ 3 个脚本写死 `/opt/homebrew/bin/python3`（`build_app.sh`/`daemon_start.py`/`setup_mineru.sh`） |
| **前端字体** | ⚠️ 用了 `"Songti SC"`（macOS 专有）、`--mono` 变量需确认有 Windows/Linux 回退 |
| CI | ✅ 已覆盖 ubuntu / macos / windows × quality/package/docker |

### 要做的事

1. **消除 `tools/` 里的解释器硬编码**
   → 统一为 `sys.executable` 或 `shutil.which("python3")`；`setup_mineru.sh` 明确标注为
   「macOS/Linux 专用，Windows 用户请用 WSL 或跳过（普通 PDF 用 pypdf 已够）」。

2. **开机自启按平台实现**（或**取消自启**）
   - macOS：`launchd`（已有）
   - Linux：`systemd --user` unit
   - Windows：注册表 `Run` 键或「启动」文件夹快捷方式
   - **但我倾向：桌面应用版根本不需要自启**——用户点图标就是开。自启只在「当服务用」时需要。
   → 建议：**自启降级为可选**，桌面版不做，Web 版保留 macOS 并提供 Linux/Windows 脚本。

3. **字体栈补全**
   ```css
   --mono: ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono",
           Menlo, monospace;
   --serif: "Georgia", "Songti SC", "SimSun", "Source Han Serif SC", serif;
   ```
   Windows 没有 Songti SC → 回退到 SimSun（宋体）。

4. **Windows 专项复查（我目前无法在本机实测）**
   - 文件锁：SQLite WAL 在 Windows 上偶发锁冲突 → 需加 `timeout` 参数
   - 路径含中文（`语料库/`）：Windows 默认 GBK，需确认全链路 UTF-8
   - `os.replace` / `Path.rename` 的跨盘行为
   - **这一块必须靠 CI + 真实 Windows 机器验证**

5. **建立跨平台冒烟测试**
   现在 CI 只跑 pytest（不启动真实服务）。要加：
   - 三个 OS 上真实 `readloops init` → `readloops serve` → `curl /api/health` → `curl /`（首屏 HTML）
   - 覆盖「全新安装、无任何数据」的路径

**工作量：2–3 天**

---

## 二、数据保真与分发（见第〇节的边界）

### 目标

用户装完就能用，**不需要自己去搞词库、也不需要联网抓半天**。

### 要做的事

1. **预计算并打包 `exam_profile.json`**（替代真题原文去算覆盖率）
   - 内容：`{level: {band_lo: token_count, "total": N, "passages": M}}`
   - 体积：< 20KB
   - 让「覆盖率 / 考试缺口 / i+1 判定」在**没有真题文本**的情况下也能工作
   - 生成脚本：`tools/build_exam_profile.py`

2. **打包词表**（可选，体积换体验）
   - 现状：用户要自己下载 63MB 的 ECDICT CSV 再导入
   - 方案 A：打包 **ECDICT 的 CET4/6/考研/雅思/托福子集**（约 1.6 万词 + 释义），
     体积约 1–2MB → **开箱即用**
   - 方案 B：保持现状，首次启动提供引导下载
   - **建议 A**：1–2MB 换来「装完就能生成文章」，值得。完整 63MB 词库仍作为可选下载。
   - 生成脚本：`tools/build_bundled_dict.py`

3. **署名与许可（合规必需）**
   - 仓库加 `THIRD_PARTY_NOTICES.md`，逐条列出：ECDICT(MIT) / Wikidata(CC0) /
     Wiktionary(CC BY-SA，需注明) / OpenCC 繁简表(Apache-2.0) / 真题语料(不随包分发，
     由用户自行抓取)
   - README 的「数据来源」章节同步
   - **Wiktionary 的 CC BY-SA 要求「相同方式共享」**——如果释义表混入了 Wiktionary 内容，
     整个衍生数据文件需以 CC BY-SA 分发。这是真实约束，要么接受，要么把 Wiktionary
     来源的 21 条剔除（只留 ECDICT + Wikidata，都是宽松许可）。

4. **「沉淀吸收」的用户数据可携带**
   - 现状：全部在本机 `data/yuedu.db`
   - 加**导出 / 导入**（JSON 或 db 文件），让用户换机时能带走掌握度与学习记录
   - 这也是「数据保真」的一部分：**用户的学习成果不能丢**

**工作量：2–3 天**

---

## 三、移动端适配

### 现状实测

| 项 | 结果 |
|---|---|
| PWA 清单 | ✅ 已有（`standalone` + 全套图标 192/512/maskable） |
| **服务绑定** | ❌ 默认 `127.0.0.1` → **手机根本连不上**（CLI 有 `--host` 参数但没暴露） |
| **响应式断点** | ❌ 全站只有 1 个 `@media (max-width: 650px)`，而且只改了「批量预览」的栅格 |
| **侧边栏** | ❌ 固定 `var(--sidebar-w)`，手机上会挤掉正文 |
| 触控目标尺寸 | ❌ 未按 44px 最小可点区域处理 |
| Service Worker | ❌ 无（装了 PWA 也不能离线） |

### 要做的事

1. **让手机能连上**
   - 新增 `readloops serve --lan`：绑 `0.0.0.0`，启动时打印局域网地址
   - **加访问令牌**（`?token=xxx` 或二维码），否则同网段任何人都能看你的学习数据
   - 二维码显示在终端 + 首页，手机扫码即用

2. **响应式重做**（这是主要工作量）
   - `≤ 900px`：侧边栏折叠为抽屉（已有 `.collapsed` 机制可复用）
   - `≤ 650px`：
     - 侧边栏全屏抽屉 + 遮罩
     - 正文单栏、字号 16px、行高放宽
     - 工具栏按钮换行或收进「更多」
     - 所有可点元素 ≥ 44px
     - 知识图谱：单指拖拽、双指缩放（当前是鼠标事件，需补 touch）
   - **重点难点**：知识图谱的 canvas 交互目前只绑了 mouse 事件，触屏完全不可用

3. **PWA 补齐**
   - 加 service worker 缓存静态资源（app.js / style.css / 图标）
   - 离线时至少能打开、能看已生成的文章
   - `manifest.json` 补 `display_override`、`scope`

4. **真机验证**
   - 至少 iPhone Safari + Android Chrome 各一台
   - iOS PWA 的限制要实测：`standalone` 模式下 viewport、安全区、下拉刷新

**工作量：3–5 天**（不含真机等待时间）

---

## 四、桌面应用打包（Mac / Windows）

### 技术选型

| 方案 | 体积 | 说明 |
|---|---|---|
| **pywebview + PyInstaller** | ~40–60MB | **推荐**：直接复用现有 FastAPI + 前端，加一层原生 webview 窗口，改动最小 |
| Tauri | ~10MB | 但要把后端从 Python 重写或打包成 sidecar，工作量大 |
| Electron | ~150MB | 太重，且和现有 Python 后端要 IPC |

**选 pywebview + PyInstaller**：现有代码几乎不动，只在 `cli.py` 加一个 `readloops desktop` 子命令，
启动 uvicorn（随机端口）→ 开 pywebview 窗口指向 `http://127.0.0.1:{port}`。

### 要做的事

1. **打包骨架**
   - `readloops desktop`：起后端 + 开窗口，退出时干净关停
   - 单实例锁（避免重复启动起两个服务）
   - 随机端口（避免和用户已有的 8000 冲突）
   - 打包后路径要用 `sys._MEIPASS` 处理（PyInstaller 的临时解包目录）

2. **macOS**
   - `.app` bundle（PyInstaller `--windowed`）
   - **代码签名 + 公证**：不签名的话用户下载后会被 Gatekeeper 拦（"已损坏"）
     → **需要 Apple 开发者账号（$99/年）**
   - 分发：`.dmg`
   - 可选：`create-dmg` 做安装界面

3. **Windows**
   - `--onefile` 或 `--onedir` + Inno Setup 做安装包
   - **代码签名**：不签的话 SmartScreen 会警告
     → 需要 Windows 代码签名证书（OV 约 $200–400/年，EV 更贵）
   - 分发：`.exe` 安装器

4. **Linux**（你说的是 mac/win，但顺手提）
   - AppImage 或 `.deb`，无需签名

5. **首次启动引导**
   - 桌面版第一次打开要引导：填 AI Key → 选目标考试 → 做定级测试
   - 现状是配置项散落在「设置」里，桌面用户需要一个向导

6. **自动更新**（可选，后置）
   - 最简单：启动时查 GitHub Releases 有无新版本，提示用户手动下载

**工作量：macOS 3–5 天（含签名公证流程）、Windows 2–4 天**

---

## 五、实施顺序与总体工作量

我建议的顺序（每一步都能独立交付价值）：

| 阶段 | 内容 | 工作量 | 依赖 |
|---|---|---|---|
| **1** | 数据边界落地：`exam_profile.json` + `build_bundled_dict.py` + `THIRD_PARTY_NOTICES.md` + 数据导出导入 | 2–3 天 | 需你先定第〇节的取舍 |
| **2** | 跨平台修复 + CI 真实启动冒烟 | 2–3 天 | — |
| **3** | 移动端：`--lan` + 令牌 + 响应式重做 + PWA 离线 | 3–5 天 | — |
| **4** | 桌面打包：pywebview 骨架 + macOS `.app` | 3–5 天 | 需 Apple 开发者账号 |
| **5** | Windows 打包 + 安装器 | 2–4 天 | 需 Windows 签名证书 |
| **6** | 首次启动向导 + 真机/真系统回归 | 2–3 天 | — |

**合计约 3 周**（不含等待签名证书、Apple 审核的时间）。

---

## 六、需要你先定的事

1. **真题打包**：接受「不打包原文，改打包派生统计」吗？
   若不接受（坚持打包真题），那这个仓库**只能私有自用**，不能开源分发 —— 这是法律问题，不是技术问题。

2. **Wiktionary 的 21 条释义**：CC BY-SA 要求「相同方式共享」。
   为避免整个释义表被传染成 CC BY-SA，建议**只保留 ECDICT(MIT) + Wikidata(CC0)**，
   剔除 Wiktionary 来源的 21 条。同意吗？

3. **Apple 开发者账号**（$99/年）和 **Windows 代码签名证书**（$200–400/年）：
   有没有？没有的话，macOS 用户下载 .app 会被 Gatekeeper 拦，Windows 会有 SmartScreen 警告。
   替代方案：只在 GitHub Releases 提供**未签名**包 + 详细的手动放行说明（体验差但免费）。

4. **移动端形态**：PWA（复用现有前端，3–5 天）还是原生 App（重写，成本高得多）？
   我强烈建议 PWA —— 现有 manifest 和图标已经就位。

5. **优先级**：上面 6 个阶段，你希望先做哪个？
   我的建议是 **1 → 2 → 3**（数据可信 + 跨平台稳 + 手机能用），桌面打包放后面 ——
   因为「网页版 + PWA」已经能覆盖绝大多数使用场景，桌面应用的边际价值在最后。
