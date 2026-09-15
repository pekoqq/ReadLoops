# ReadLoops 项目交接文档

> 交接时间：2026-09-16
> 项目版本：v2.3.2（功能完整，可正常运行；PyPI 已发布 2.3.1，v2.3.2 未发布）
> 本文档面向接手的新 Agent / 开发者，包含功能状态、已知问题、待办、文件位置索引与快速上手清单。

---

## 🚩 最新进展（2026-09-16，接手请先读这一节）

**2.3.2（最新）：知识图谱重建 + 全量蒸馏。** 用户反馈图谱「比例和动画有问题」，实测定位到五层
根因（多 rAF 循环叠加 / 物理极限环 / 画布被阅读栏卡住 / 150 个短语节点 100% 孤立 / `quot` 垃圾
数据）。修复要点与验收数据见 `docs/CHANGELOG.md` 的 `[2.3.2]` 与 `docs/PROJECT_MEMORY.md`。
**改图谱前必读**：物理必须保留 **alpha 退火**（只靠阻尼在数学上收不了，会停在永久极限环）；
构图必须**不产生孤立节点**（孤立节点没有弹簧牵引，会被斥力推到画布边缘排成硬边）。

**本日（9/15 晚–9/16）定稿了专注模式的顶光动效**，此前（9/15 凌晨）完成知识库工作台：书架、材料导入、统计画像蒸馏、知识图谱和 PDF 解析。阅读器仍是主功能，知识库是围绕阅读的增强层。

### 0.5 专注模式顶光（最新，2026-09-16 定稿）

用户要求「顶光在水纹涟漪的时间内**逐渐熄灭**」，当前已实现并验证：

| 项 | 当前实现 |
|---|---|
| 日常氛围光 | 光斑扩到 120vw、中心推出视口外，只露柔弧，**不再是完整可见的圆**；四主题 `--ambient-1/2` 不变 |
| 进入专注 | 单团顶光用 `focusGlow` **animation**（transition 表达不了「先亮起再熄灭」）：0.3s 延迟起，38% 处汇聚并增亮到 brightness(2.6)，随后降到 0 —— **与涟漪同 3s 时长、同 0.3s 延迟，一起亮起又一起熄灭** |
| 第二团光 | 1s 淡出（早于顶光峰值 1.44s，避免视觉重心被拉偏） |
| 对齐阅读栏 | 汇聚位置用 `--content-shift`（= 侧边栏宽/2）对齐阅读列而非视口中心；侧边栏折叠时 `body:has(.app.auto-sidebar-mode)` 归 0 |
| 暗角 | `.focus-shade` 聚焦暗角（中央通透、四周收暗），与顶光同节奏 3s 落定 |
| reduced-motion | 跳过 focusGlow 动画与光斑过渡 |

**⚠️ 交接时点的重要状态：**
- **本地领先远程 10 个提交未推送**（3e4dba5 → 498981a，含上述 focus 定稿 + 知识库/PDF/MinerU/批量识词）——接手后**第一件事**是 `git -C ~/readloops push origin main` 并确认 CI 绿（本次交接已推送，若交接发生在旧快照上需重新确认）
- **PyPI 已发布 2.3.1**（2026-09-15，版本号 2.3.1；同版本不可覆盖，再发需 bump 版本）
- 两库 app 目录已逐文件核对一致（本次交接补齐了私有库缺失的 `dict_import.py` 与 `cli.py` 词库导入命令）

### 0. 知识库（接手优先看）

| 功能 | 关键文件 | 状态 |
|---|---|---|
| 书架 | `app/services/bookshelf.py`、`app/api/books.py` | Project Gutenberg 搜索、下载、本地入库、导入材料 |
| 材料 | `app/services/materials.py`、`app/api/materials.py` | 粘贴/TXT/MD/EPUB/PDF；同路径重复导入更新不复制 |
| 蒸馏 | `app/services/distill.py` | 统计句长、从句密度、TTR、词频/CET 覆盖；生效画像接入文章生成 |
| 图谱 | `app/services/graph.py`、`app/api/graph.py` | 节点/边幂等重建；Canvas 力导向 UI |
| MinerU | `tools/setup_mineru.sh`、`tools/mineru_direct_parse.py` | 独立 Python 3.13 环境；**直连 pipeline，勿使用官方 CLI**（CLI 本机任务轮询会 404） |
| 批量识词 | `app/services/ai.py::recognize_batch_vocabulary`、`app/api/words.py` | AI 结构化识别 + 本地保底；前端预览可编辑、确认后才入库 |

MinerU 模型缓存/状态在 `data/mineru-cache`、`data/mineru-home`，均不进版本库；普通 PDF 由 `pypdf` 处理。

本日最早做的是一轮完整的 UI/动效打磨，共 13 个代码提交（04:52–06:20，含 1 次 revert）。

### 一、建立了「设计 token 体系」（重要，改 UI 前必读）

以前的问题是**每个元素各写各的样式**，导致同一个东西有 3~4 种实现。现已收敛为 token：

| 体系 | 位置 | 内容 |
|---|---|---|
| **按钮** | `:root` 的 `--btn-*` | `--btn-h`(32) / `--btn-h-sm`(26) / `--btn-radius`(999px 胶囊) / `--btn-radius-sq`(9px) / `--btn-pad` |
| **动效** | `:root` 的 `--page-*` / `--enter-*` | `--page-out`(0.1s) / `--page-in`(0.52s) / `--enter-dur`(0.78s) / `--enter-shift`(14px) / `--enter-stagger`(0.055s) |
| **缓动** | `:root` 的 `--ease-*` | `--ease-out` / `--ease-spring` / `--ease-silk` / `--ease-damped` |
| **玻璃** | `:root` 的 `--glass*` | `--glass` / `--glass-strong` / `--glass-blur` / `--glass-liquid`(SVG 折射) / `--glass-inner-shadow` |
| **氛围光** | `:root` 的 `--ambient-*` | 见下文「玻璃质感」 |

> ⚠️ **铁律：新增 UI 元素必须复用既有 token，不要再手写一套。**

### 二、按钮系统（`.toolbar-btn` 是基类）

- `.toolbar-btn` = **全站共享按钮基类**（胶囊 + 玻璃 + 内阴影 + 扫光 + 上浮反馈）。变体：`.primary` / `.danger` / `.icon` / `.large`
- 跟随基类语言的有：`.timer-btn`（小号）、`.lookup-panel .action-btn`
- `.article-delete-btn` 与 `.vocab-delete-btn` 已**合并为同一套** icon-danger（此前是两套不同设计）
- 已**移除**「整颗按钮反色成白底黑字」的 hover 反馈

### 三、玻璃质感

- **表面必须比背景亮**：深色主题曾用 `rgba(32,29,29,0.45)`（比背景只亮一点点），肉眼几乎看不出玻璃 → 现改为 `rgba(253,252,252,0.085)`
- **`body::before` 有一层氛围光**（四主题各配 `--ambient-1/2`）。这不是装饰 —— **纯色背景下 `backdrop-filter` 模糊纯色仍是纯色，玻璃「没有东西可折射」**，加了光斑才有层次
- 设置弹窗、删除确认框、侧边栏下拉框此前是**实色**，现已统一为玻璃

### 四、动效系统（这块反复最多，务必读完）

**分工原则（血泪教训）：**

| 层 | 规则 |
|---|---|
| **容器** `#reader` | **只做 opacity 淡入淡出，绝不位移/缩放**。容器一移，整块内容就"跳" |
| **内容**（卡片/列表/选项） | 允许**小位移**（14px）+ 错峰，但不能和容器同时位移 |

**两条已修复的「残影」根因（都很隐蔽）：**
1. `switchPage(renderFn)` 里的 `renderFn()` 是 **async**（要先 fetch 才写 `innerHTML`），但**没 await** → 旧内容先淡回来，数据到了再被换掉 = 残影。→ 已改 `async` + `await`
2. `#reader.page-fading` 用 `transition`。**移除该类时浏览器会反向补间一次**，与淡入动画打架 → 旧帧再闪一下。→ 两段都改用 `animation`

**⚠️ 另一个坑：CSS 动画时长必须与 JS 里 `setTimeout` 的计时对齐**，否则淡出结束到渲染开始之间留空档，观感是「闪一下」。

**节奏参数（当前值）：**
- 页面离开 **0.10s**（必须极快，否则旧页面残留被看成残影）
- 页面进入 **0.52s** + `--ease-damped`
- 内容入场 **0.78s** + `--ease-damped` + 位移 14px + 错峰 0.055s
- `--ease-damped: cubic-bezier(0.34, 1.28, 0.42, 1)` —— **会过冲再回落的弹簧曲线**，是「阻尼感」的来源；普通 ease-out 只会"滑到停下"，显得生硬

**统一入口函数：`staggerIn(selector, step, max)`**（`app.js`）
- 给成组元素按序号设置 `animationDelay`（带 max 上限）
- 已接入：`.article-item` / `.vocab-item` / `.test-type-card` / `.test-option-btn` / `.test-option` / `.srs-review-banner`
- ⚠️ **加新机制前先搜一遍旧实现**（搜 `animationDelay`、`animation =`）—— 曾经存在**两套错峰逻辑互相覆盖**，调半天没效果

### 五、本轮新增的两个功能

1. **划词翻译整句**：选中多个单词 → 走 AI 翻译（`POST /api/words/translate`）；单个单词仍查词典。句子模式下隐藏「加入生词本」
2. **侧边栏 logo 跳转项目主页**：`.sidebar-header` 已改为 `<a id="projectHomeLink">`，地址已填为 `https://github.com/pekoqq/ReadLoops`（2026-09-14）

### 六、本轮待办（交给下一个智能体）

- [x] **logo 跳转地址已填（2026-09-14）**：指向 `https://github.com/pekoqq/ReadLoops`，两库 `app/web/index.html` 的 `#projectHomeLink` 均已更新
- [x] **动效手感已经用户确认定稿（2026-09-14，评价「还可以」；顶光涟漪熄灭 09-16 定稿）**：当前参数即最终版，**非用户主动提出不要改动**。若将来用户再反馈不对，**先问清楚是「跳/抖」「太快」「太平淡」还是「有残影」**，按 `HANDOFF-UI-MOTION.md` 第一节归因表处理，不要直接调参数
- [x] **PyPI 2.3.1 已发布（2026-09-15）**
- [ ] 语法测试题库（长期遗留）
- [ ] 移动端适配（已暂缓，前置死结是服务绑 `127.0.0.1`）

### 七、专项交接文档

本轮 UI/动效工作单独写了一份**自包含**的交接文档，内容更聚焦、包含踩坑清单与归因表：

📄 **`docs/HANDOFF-UI-MOTION.md`** ← 若你的任务是继续做 UI，优先读这份

### 八、同步状态

- 私有库 `~/约读/`（master）与公开库 `~/readloops/`（main）**已逐文件核对一致**（app 目录 diff 干净；内部文档 HANDOFF-UI-MOTION/UI_UX_EVOLUTION_PLAN/decisions/superpowers 按约定不进公开库；公开库另持有 ARCHITECTURE.md、ROADMAP.md）
- **公开库已公开发布**：https://github.com/pekoqq/ReadLoops（Public，默认分支 main，gh/HTTPS 推送，账号 pekoqq；本机 SSH key 未绑定该账号，勿改 SSH remote）。后续同步流程：改私有库 → cp 到公开库 → 公开库提交 → `git -C ~/readloops push`
- **提交链（2026-09-14 → 09-16）**：`b93405e`（2.3.1 changelog）→ `3e4dba5`（顶光涟漪熄灭）→ `258218f`（光斑不再像球）→ `379775b`（第二团光先淡出）→ `b0f12e1`（顶光对齐阅读栏）→ `4bacdb0`（知识库工作台）→ `99dd31b`（MinerU 后端）→ `c15ca39`（本地 PDF 栈）→ `de1cacd`（隔离 ModelScope 状态）→ `05f3a39`（绕过坏 CLI 直连 pipeline）→ `498981a`（批量识词，当前 HEAD）
- ⚠️ 若接手时公开库远程落后本地，先 push 再继续（见文首「最新进展」）

---

## ⚠️ 必读：安全与仓库结构

**1. 私有库 git 历史含 API Key（风险已受控）**
- 历史提交 `096ac91` 曾将 `data/yuedu.db`（87MB）入库，内含 API Key + 个人阅读记录。
- **现状**：私有库**无远程、从未推送**；公开库已确认不含密钥。用户决定**继续使用该 Key**，无需撤销。
- **保护**：已装 `pre-push` 钩子，默认阻止推送私有库（豁免：`READLOOPS_ALLOW_PUSH=1`）。
- **铁律**：`~/约读/` 不推送到任何远端、不外发文件夹。若将来确需分享，**先清除历史 blob**（`git filter-repo`）。

**2. 双仓库结构**

| 仓库 | 路径 | 分支 | 用途 |
|------|------|------|------|
| 私有库 | `~/约读/` | `master` | 个人自用，含真实数据 |
| 公开库 | `~/readloops/` | `main` | 开源独立项目，历史从零，不含数据/语料 |

两库代码**手动同步**：先改私有库验证 → 再同步到公开库。

---

## 一、项目概览

| 项目 | 内容 |
|------|------|
| **项目名称** | ReadLoops（历史名：约读 → ReadForge → ReadLoops） |
| **项目定位** | AI 驱动的英语阅读训练器，服务于大学英语四级（进阶六级） |
| **核心理论** | 可理解输入（i+1，95–98% 词汇覆盖率）+ 阅读驱动的 FSRS 间隔重复 |
| **技术栈** | FastAPI + SQLite + 原生 HTML/CSS/JS（单页应用） |
| **AI 服务商** | DeepSeek V4.1 Flash（`deepseek-flash`），强制关闭思考模式 |
| **项目根目录** | `~/约读/` |
| **Git 分支** | `master` |

---

## 二、启动方式

**方式 0：双击应用（推荐）**

```
~/Applications/ReadLoops.app
```

双击自动起服务并以 Chrome 应用模式打开界面。重建：`bash ~/约读/tools/build_app.sh`

**关键**：必须用 `/opt/homebrew/bin/python3`（依赖装在这个解释器里）。裸 `python3` 可能指向无依赖的解释器。

```bash
# 方式 1：一键脚本
~/约读/start.sh          # 前台运行
~/约读/start.sh -d       # 后台运行，日志 logs/server.log

# 方式 2：手动
cd ~/约读
/opt/homebrew/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 方式 3：开机自启（launchd，plist 已就位，登录时自动加载）
launchctl load -w ~/Library/LaunchAgents/com.readloops.server.plist
```

- 访问地址：**http://127.0.0.1:8000**
- 健康检查：`curl http://127.0.0.1:8000/api/health`
- 依赖：`fastapi`、`uvicorn`、`httpx`、`pydantic`（FSRS 与相似度均为自研，无额外依赖）

**注意**：
- 项目已从桌面移到 `~/约读/`，避免 iCloud 文件锁定（桌面存在符号链接 `~/Desktop/约读 -> ~/约读`）
- launchd 已配置 `KeepAlive`：进程崩溃会自动重启；`RunAtLoad`：登录即启动
- ⚠️ 不要同时用方式 1/2 和方式 3，会抢占 8000 端口
- 数据库 `data/yuedu.db` 被 `.gitignore` 排除，克隆仓库后需另行获取/重建才能运行

---

## 三、AI 配置

| 项 | 值 |
|----|----|
| 服务商 | DeepSeek |
| Base URL | `https://api.deepseek.com` |
| 模型 | `deepseek-flash`（V4.1 Flash） |
| 认证 | `Authorization: Bearer <api_key>` |
| **关键** | 必须加 `thinking: {"type": "disabled"}` 关闭思考模式，否则 `content` 为空、JSON 解析失败 |
| API Key | 存于数据库 `settings` 表，用户在「设置面板」配置，可点「测试连接」验证 |

---

## 四、核心功能状态

| 功能 | 状态 | 说明 |
|------|------|------|
| 文章生成 | ✅ | DeepSeek V4.1 Flash，关闭思考模式，~300 词，真题风格蒸馏 + 相似度去重 |
| 真题风格蒸馏 | ✅ | 205 篇四级真题量化参数融入生成 prompt |
| 相似度去重 | ✅ | TF-IDF + 余弦，阈值 0.4，超阈值换主题重试 ≤3 次 |
| 划词查词 | ✅ | 本地 ECDICT 词典优先，AI 兜底；查词记录放右侧抽屉 |
| 生词本 | ✅ | 增删改查、多选批量操作、点击展开释义 |
| 高亮标记 | ✅ | 划词高亮、点击取消、渲染后自动恢复 |
| 阅读计时 | ✅ | 空格控制，鼠标离开自动暂停 |
| 专注模式 | ✅ | 自动开启 + 水波纹引导 + 边缘触发，Esc/F/Space 快捷键 |
| 智能词汇测试 | ✅ | 权重抽样出题，多题型（释义/中文选词/词形变化） |
| FSRS 间隔重复 | ✅ | 文章选词驱动，测试结果更新，复习队列 API |
| 阅读统计 | ✅ | 8 概览卡片 + 词汇分布 + FSRS 统计 + 热词 TOP10 + GitHub 式热力图 |
| PWA | ✅ | 可安装为独立 Mac 应用，无浏览器标题栏 |
| 液态玻璃质感 | ✅ | SVG feTurbulence + feDisplacementMap 边缘折射 |
| 主题 | ✅ | 深色 / 明亮 / 纸质护眼 / 纯黑 |
| 语法测试 | ❌ | 未实现（测试首页显示「即将上线」占位） |
| 移动端适配 | ❌ | 未实现（规划见 `docs/UI_UX_EVOLUTION_PLAN.md` 任务 7） |

---

## 五、数据库表结构（15 表）

`data/yuedu.db`（SQLite，83MB）

| # | 表 | 说明 |
|---|----|----|
| 1 | `users` | 用户（默认用户 1） |
| 2 | `words` | 单词表（175,777 行；含 ECDICT 释义 104,150、exchange 词形 28,282、FSRS 字段、lookup/wrong/correct_count） |
| 3 | `word_encounters` | 单词遇见记录（查词/高亮） |
| 4 | `articles` | 文章（含 target_words JSON、word_count、reading_time_seconds） |
| 5 | `highlights` | 高亮记录 |
| 6 | `reading_sessions` | 阅读会话（时长/查词数/高亮数） |
| 7 | `tests` | 测试记录 |
| 8 | `settings` | 设置（key-value，含 AI API Key） |
| 9 | `phrases` | 短语库（1,238 个真题高频短语） |
| 10 | `test_questions` | 测试题目缓存 |
| 11–15 | `books` / `materials` / `style_profiles` / `graph_nodes` / `graph_edges` | 知识库：书架、材料、风格画像、图谱节点与边 |

---

## 六、已知问题与待办

**已知问题：**
1. **语法测试**未实现（测试首页占位）
2. **移动端未适配**（规划见 UI_UX_EVOLUTION_PLAN.md 任务 7）
3. ~~服务不自启~~ → **已解决**：launchd `com.readloops.server.plist` 已就位（KeepAlive + RunAtLoad），登录即启动、崩溃自动重启
4. **命名历史遗留**：文件夹名「约读」、数据库名 `yuedu.db`、应用名 ReadLoops（不影响功能）
5. **`语料库/词表/` 为空**：CET4/CET6 词表源文件已不在磁盘（词数据已入库，level=CET4 共 4,530 词）
6. **`语料库/真题/` 为空**：42 套真题被 `.gitignore` 排除且已不在磁盘；现存唯一真题语料为 205 篇 JSON
7. **`data/`、`*.db` 被 gitignore**：数据库不入版本库，需另行获取

**待办（按推荐优先级）：**
- [ ] **撤销泄露的 API Key**（P0，见文首）
- [ ] 配置 launchd 开机自启（小活，天天受益）
- [ ] 补回 CET4/CET6 词表源文件（如需重新导入）
- [ ] 实现语法测试题库（唯一"半成品"功能）
- [ ] 真题语料直接用于文章内容生成（当前仅用于相似度匹配）
- [~] ~~移动端响应式适配~~ —— **暂缓**（2026-09-13 决定）：前置死结是服务绑 `127.0.0.1`，手机访问不到，需先解决远程访问

---

## 七、Git 状态

**私有库**
- 仓库：`~/约读/.git/`，分支 `master`
- 工作区：干净
- 排除项（`.gitignore`）：`data/`、`*.db`、`语料库/真题/`、`__pycache__/`、`.DS_Store`、`.pytest_cache/`
- ⚠️ 历史含 DB blob（见文首安全说明），**不得推送**

**公开库**
- 仓库：`~/readloops/`，分支 `main`
- 初始提交：`5fea0aa`
- 许可证：MIT
- 提交身份为占位符 `readloops-dev`，**推送前需改成自己的 GitHub 身份**：
  ```bash
  cd ~/readloops
  git config user.name "你的GitHub用户名"
  git config user.email "你的ID@users.noreply.github.com"
  git commit --amend --reset-author --no-edit
  ```

---

## 八、快速上手检查清单

接手后按顺序验证：

```bash
# 1. 检查 Git 状态与最近提交
cd ~/约读 && git status && git log --oneline -5

# 2. 启动服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# 3. 健康检查
curl http://127.0.0.1:8000/api/health

# 4. 打开页面
open http://127.0.0.1:8000
```

阅读顺序（推荐）：
1. 本文档 `docs/HANDOFF.md`
2. 项目记忆 `docs/PROJECT_MEMORY.md`
3. 变更日志 `docs/CHANGELOG.md`
4. 入口代码 `app/main.py` → `app/services/ai.py`

---

## 九、项目文件位置索引（完整）

### 核心文档
| 文件 | 路径 |
|------|------|
| 交接文档 | `~/约读/docs/HANDOFF.md` |
| 项目记忆 | `~/约读/docs/PROJECT_MEMORY.md` |
| 变更日志 | `~/约读/docs/CHANGELOG.md` |
| UI/UX 计划 | `~/约读/docs/UI_UX_EVOLUTION_PLAN.md` |
| 需求记录 | `~/约读/docs/decisions/2026-09-10-requirements-log.md` |
| 设计文档 | `~/约读/docs/superpowers/specs/2026-09-10-约读阅读器-design.md` |

### 后端代码
| 模块 | 路径 |
|------|------|
| 后端入口 | `~/约读/app/main.py` |
| 配置 | `~/约读/app/config.py` |
| 数据库 schema | `~/约读/app/database.py` |
| 数据模型 | `~/约读/app/models.py` |
| AI 服务（生成/查词） | `~/约读/app/services/ai.py` |
| FSRS 服务 | `~/约读/app/services/srs.py` |
| 相似度去重 | `~/约读/app/services/similarity.py` |
| 智能测试出题 | `~/约读/app/services/smart_test.py` |
| 生词调度器 | `~/约读/app/services/scheduler.py` |
| 难度自适应 | `~/约读/app/services/level.py` |
| 词表导入 | `~/约读/app/services/corpus.py` |
| API — 文章 | `~/约读/app/api/articles.py` |
| API — 单词/词典/测试 | `~/约读/app/api/words.py` |
| API — 阅读行为 | `~/约读/app/api/reading.py` |
| API — 统计/测试/设置 | `~/约读/app/api/stats.py` |

### 前端
| 模块 | 路径 |
|------|------|
| HTML | `~/约读/app/web/index.html` |
| CSS | `~/约读/app/web/css/style.css` |
| JS | `~/约读/app/web/js/app.js` |
| PWA 配置 | `~/约读/app/web/manifest.json` |
| 应用图标 | `~/约读/app/web/icon.svg` |

### 数据与语料
| 内容 | 路径 / 规模 |
|------|------|
| SQLite 数据库 | `~/约读/data/yuedu.db`（83MB；words 175,777 行，有释义 104,150，exchange 28,282，phrases 1,238） |
| 真题语料（唯一现存） | `~/约读/语料库/真题阅读纯文本/all_passages_lazynote.json`（**205 篇**四级真题阅读） |
| 风格参数 | `~/约读/tools/style_analysis/cet4_style_params.json` |
| 词表目录 | `~/约读/语料库/词表/`（⚠️ 空目录） |
| 真题目录 | `~/约读/语料库/真题/`（⚠️ 空目录，被 gitignore） |

### 脚本与技能
| 内容 | 路径 |
|------|------|
| 短语库构建 | `~/约读/tools/build_phrase_library.py` |
| ECDICT 词形导入 | `~/约读/tools/import_exchange.py` |
| DB v2 迁移 | `~/约读/tools/migrate_db_v2.py` |
| 风格分析工具 | `~/约读/tools/style_analysis/` |
| 真题风格蒸馏 Skill | `~/约读/skills/cet4-style-distiller/` |

---

**交接人**：阿约（ReadLoops 常驻维护 Agent）
**接手人**：新 Agent
**交接时间**：2026-09-14（本轮为 UI/动效打磨，**请先读文首「最新进展」**）

---

## 十、给接手 Agent 的实操建议

**环境相关的坑（都踩过）：**
1. **必须用 `/opt/homebrew/bin/python3`** 启动 —— 依赖装在这个解释器里，裸 `python3` 可能指向无依赖的解释器
2. **agent 沙箱内起后台服务会被回合回收**。有效解法：`/opt/homebrew/bin/python3 tools/daemon_start.py`（双 fork + setsid，已验证跨回合存活）
3. **本环境 Bash 的 `grep` 不可靠**（会误报空结果），搜索代码请用 Grep 工具或 Python
4. **`curl` 访问 localhost 要加 `--noproxy '*'`** —— 环境设了 `HTTP_PROXY`，否则被代理劫持返回 `upstream connect failed`
5. **`curl` 对 502 也返回退出码 0**，用 `&&` 判断"服务是否可用"会误判，应检查响应体或加 `-f`
6. **前端改动无需重启服务**：静态文件实时读盘；`index.html` 的 `?v=` 版本号由 `app/main.py` 按文件 mtime 动态注入。用户报「界面没变」→ 先怀疑浏览器缓存

**改 UI 前的心态建议：**
- 这个项目的 UI 改动**用户会逐帧感受**，且描述往往用的是感受词（"挤""跳""生硬""丝滑""阻尼感"），**这些词对应的技术原因是完全不同的**：
  - 「挤」→ 内容先出现、后到的元素把它撑开（或位移过大）
  - 「跳」→ 容器在位移，或多层 transform 叠加
  - 「生硬」→ 缓动曲线是匀速/无惯性
  - 「闪一下」→ CSS 时长与 JS 计时不对齐，中间留了空档
  - 「残影」→ 旧内容在切换过程中被看见（async 未 await / transition 反向补间）
- **听到感受词先归因到具体技术原因，不要直接调参数**。本轮前几次就是没归因对，白改了好几轮。
- **不确定时先问，不要连续叠加改动**。用户明确说过「改的还没有原来好了」—— 那次正确的做法是先 `git revert` 再问。
