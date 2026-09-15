# 约读阅读器 — 项目记忆文件

> 本文件是项目的核心记忆，每次会话开始时应读取，结束时更新。
> 最后更新：2026-09-14（v2.3.0，UI/动效打磨 + 划词翻译；动效已定稿；已开源发布到 github.com/pekoqq/ReadLoops）

## 项目定位

AI 驱动的英语阅读训练器「ReadLoops」（原名约读 → ReadForge → ReadLoops），服务于大学英语四级考试（进阶六级）。
核心理论：可理解输入（i+1，95–98% 词汇覆盖率）+ 阅读驱动的 FSRS 间隔重复。
改名原因：GitHub 上已有同名项目 readforge-backend 和多个 ReadmeForge，避免混淆。

## 知识库工作台（2026-09-15）

阅读器仍是本体，知识库是围绕阅读的增强层：

- **书架**：搜索/下载 Project Gutenberg 公版书，文件落在 `data/library/`，不进入版本库。
- **材料**：支持粘贴、`.txt`、`.md`、`.epub`、`.pdf`；内容只在用户本机，用于蒸馏和生成参考。
- **风格画像**：`style_profiles` 保存句长、句法复杂度、词汇分布等统计参数；生效画像会作为统计提示进入 `app/services/ai.py::generate_article()`。
- **图谱**：`graph_nodes` / `graph_edges` 同时服务用户可视化和结构化检索；前端为 canvas 力导向图。
- **PDF 链路**：`pypdf` 是普通 PDF 的运行时依赖；复杂版式可运行 `bash tools/setup_mineru.sh` 按需安装 MinerU。MinerU 使用 `tools/mineru-env/` 独立 Python 3.13 环境和 `data/mineru-*` 模型状态目录；解析不走其 CLI，而走 `tools/mineru_direct_parse.py` 直连 pipeline 接口。
- **批量词汇识别**：`POST /api/words/batch-recognize` 调 `ai.py::recognize_batch_vocabulary`，返回结构化 `items`；AI 异常自动回退 `_local_vocab_parse`。前端必须保留「预览可编辑后确认」而不是直接入库。`/batch-add` 的已有词典项要改为 `learning`，不能当重复跳过。

## ⚠️ 双仓库结构（2026-09-13 起）

| 仓库 | 路径 | 分支 | 用途 |
|------|------|------|------|
| **私有库** | `~/约读/` | `master` | 个人自用，含真实数据 `data/yuedu.db`。**永不推送** |
| **公开库** | `~/readloops/` | `main` | 开源独立项目，历史从零，不含数据/语料。**远程：https://github.com/pekoqq/ReadLoops（2026-09-14 公开发布，Release v2.3.0，CI 8 作业全绿；同日发布 PyPI https://pypi.org/project/readloops/，`pip install readloops` 已可用，PyPI 账号 Fangyuan2711，已开 2FA）** |

- 两库**代码手动同步**。建议：先改私有库验证 → 再同步到公开库 → `git -C ~/readloops push`。
- 公开库初始提交 `5fea0aa`，许可证 MIT；远程通过 `gh`（gh 2.100，HTTPS，账号 pekoqq，凭据在 keyring）推送。
- 公开库排除：`data/`、`语料库/`、内部文档（PROJECT_MEMORY/HANDOFF/decisions/superpowers）。
- 本机 SSH key（id_ed25519）**未**绑定 GitHub 账号；远程操作走 gh/HTTPS，不要改用 SSH remote。

## 当前状态

### v2.3.1 — 专注计时联动 + 整句翻译健壮性 + 知识库工作台 + 顶光涟漪熄灭（已完成，2026-09-16；PyPI 已发布 2.3.1）

- **专注计时严格挂钩**：新不变量「计时器只在专注模式下运行」（`startTimer()` 首行 `if(!focusMode) return`）；空格/计时按钮统一走 `toggleTimerWithFocus()`（非专注 = 进专注并开始计时，专注中 = 暂停/继续）；修复第一个 keydown 处理器调用未定义 `toggleTimer()` 的坏分支
- **整句翻译健壮性**：deepseek-flash 思考泄漏（content 空、答案在 reasoning_content）判定为可重试，`_chat()` 最多重试 2 次；`/api/words/translate` 改 `asyncio.to_thread` + 502 带原因；长度上限 60→280；前端失败给「点击重试」
- **logo hover 动效**：hover 0.85s 转一圈 + 微放大（animation 避免离开整圈倒转）、:active 回弹 0.86，currentColor 单色，reduced-motion 兜底
- **引导箭头**：右侧查词抽屉浮动小玻璃箭头，触发一次抽屉后 localStorage 记住并消失
- **专注顶光涟漪熄灭（9/15–16 定稿）**：光斑扩 120vw、中心推出视口外（日常只见柔弧、不是完整圆）；进入专注单团顶光用 `focusGlow` **animation**「先亮起(brightness 2.6)再熄灭」，与涟漪同 0.3s 延迟、3s 时长一起落定；第二团光 1s 先淡出；`--content-shift` 对齐阅读栏（侧栏折叠归 0）；`.focus-shade` 聚焦暗角四主题 token
- **知识库工作台（9/15）**：书架（Gutenberg 搜索下载）、材料（粘贴/TXT/MD/EPUB/PDF）、风格蒸馏画像接入文章生成、力导向知识图谱；PDF 走 pypdf + MinerU 独立环境（**直连 pipeline，勿用官方 CLI**——3.4.5 本地轮询 404）；批量识词「识别 → 可编辑预览 → 确认」
- **质量基线**：41 pytest passed；CI 全绿（三 OS × 3 作业：quality/package/docker）；两库 app 目录已逐文件核对一致（本次交接补齐私有库 `dict_import.py` + `cli.py` 词库导入命令）
- 详细变更见 `docs/CHANGELOG.md` 的 `[2.3.1]`；**交接要点见 `docs/HANDOFF.md` 文首「最新进展」**

### v2.3.0 — UI/动效打磨 + 划词翻译（已完成，2026-09-14）

- **建立设计 token 体系**：按钮（`--btn-*`）、动效（`--page-*` / `--enter-*`）、缓动（`--ease-*`）、玻璃（`--glass*`）、氛围光（`--ambient-*`）全部收敛到 `:root`
  - ⚠️ **改 UI 铁律：新增元素必须复用既有 token，不要手写一套**（此前正是因为各写各的，才出现 3 套圆角、4 种 hover 语言、2 套删除按钮）
- **按钮系统**：`.toolbar-btn` 成为共享基类（`.primary` / `.danger` / `.icon` / `.large`）；两个删除按钮合并为同一套 icon-danger；移除「整颗按钮反色」的 hover
- **玻璃质感**：表面改为「比背景亮」的浅色半透明；新增 `body::before` 氛围光层（**纯色背景下 `backdrop-filter` 无从发挥，必须有可折射的内容**）；设置弹窗/删除框/侧边栏输入框由实色改为玻璃
- **动效原则（血泪教训）**：容器 `#reader` **只做 opacity、绝不位移**；内容允许小位移 + 错峰
- **页面切换残影（两层根因）**：① `switchPage` 调用的渲染函数是 async 但未 await；② `page-fading` 用 `transition`，移除类时反向补间与淡入动画打架 → 已分别改为 `await` 与 `animation`
- **自动专注失效（回归）**：`tryAutoFocus()` 被非阅读页点击「消耗」掉一次性机会 → 加视图判断
- **统一入口**：`staggerIn(selector, step, max)`（`app.js`）；热力图改为容器整块淡入（188 格同时缩放会「炸开」）
- **新增功能**：① 划词翻译整句（`POST /api/words/translate`）② 侧边栏 logo 跳转项目主页（地址已填为 `https://github.com/pekoqq/ReadLoops`，2026-09-14）
- **品牌 logo（2026-09-14）**：定稿「明度轨迹记忆循环环」；设计原则——**带底徽标用渐变、应用内一律 currentColor 单色跟随 `--text`（四主题不突兀的关键，禁止在 UI 内引入彩色/写死颜色）**；全套位图在 `app/web/`（favicon.ico/16/32、apple-touch 180、PWA 192/512、maskable 512），由 `main.py` 显式根路由服务；公开库 `assets/` 存 README logo 与 1280×640 social-preview（后者需 GitHub 仓库设置手动上传，API 不支持）；源生成脚本逻辑见 CHANGELOG；PyPI 2.3.0 不可覆盖，品牌改动随下一版发布
- **动效已定稿（2026-09-14）**：用户确认手感通过（评价「还可以」），当前 token/时长/缓动参数即最终版，**非用户主动提出不要再调**；再被反馈时先按 `HANDOFF-UI-MOTION.md` 第一节归因表定位
- 详细变更见 `docs/CHANGELOG.md` 的 `[2.3.0]`；**UI 交接要点见 `docs/HANDOFF.md` 文首「最新进展」**

### v2.2.2 — 开源拆分 + 安全审计（已完成）
- **P0 安全发现**：私有库历史提交 `096ac91` 曾把 `data/yuedu.db`（87MB）入库，含**真实 API Key**（`sk-acc7417d5...`，与当前活跃 key 相同）+ 个人阅读记录。提交 `65b1380` 后移出工作区，但 blob 仍在历史。
  - **处置（2026-09-13 复核后修正）**：私有库无远程、从未推送 → 密钥未外泄；用户决定继续使用该 Key。已加 `pre-push` 钩子防止误推送；不重写历史。
- **公开库落地** `~/readloops/`：脱敏导出，历史从零（`.git` 344K，物理不含旧 blob）。
- **顺手修复的真实缺陷**：
  - `app/database.py` schema 仅 8 表，缺 `phrases`/`test_questions`；`words` 缺 5 列（含 `srs_interval`）→ **全新安装必崩**。已补为完整单一事实源。
  - 9 处硬编码 `/Users/wangzhiming/` 绝对路径 → 改相对路径 + `READLOOPS_*` 环境变量。
  - 4 处裸 `except:`、1 处重复字典键。
- **质量基线**：ruff 0 error + pytest 19 passed + pre-commit + GitHub Actions CI + CONTRIBUTING（含 8 项审查清单）+ docs/ARCHITECTURE.md。
- **文档失真修正**：风格参数实为**硬编码在 `ai.py` 的 prompt 中**，并非运行时加载 `cet4_style_params.json`。

### v2.2.1 — 交接文档与文件索引校正（已完成）
- 新增 `docs/HANDOFF.md` 交接文档（功能状态/已知问题/待办/快速上手清单）
- **校正文档与磁盘实际状态的不一致**（交接前核查发现）：
  - `语料库/词表/` 目录实际为空（CET4/CET6 词表文件已不在磁盘，词数据已在 DB 中：level=CET4 共 4530 词）
  - `语料库/真题/` 目录实际为空（42 套真题文件被 `.gitignore` 排除且已不在磁盘，仅剩空目录占位）
  - 现存唯一的真题语料为 `语料库/真题阅读纯文本/all_passages_lazynote.json`（205 篇四级真题阅读）
  - 数据库 `data/yuedu.db` 实际 83MB（非此前记录的 87MB），words 表 175,777 行
- 统一「项目文件位置索引」，便于后续 agent 快速接手

### v2.2.0 — UI/UX 深度进化 + 液态玻璃 + 水波纹引导（已完成）
- **液态玻璃质感**：参考 shuding/liquid-glass，SVG feTurbulence+feDisplacementMap 边缘折射，应用于工具栏/计时条/按钮/卡片/弹窗
- **水波纹引导效果**：专注模式开启后，从顶部中心扩散涟漪，feTurbulence+feDisplacementMap 真正像素扭曲文字，3秒动画，主题适配颜色
- **PWA 支持**：manifest.json + SVG 图标，可安装为独立 Mac 应用，无浏览器标题栏
- **高亮系统完善**：划词高亮 + 点击已高亮文本取消高亮 + 文章渲染后自动恢复高亮记录
- **专注模式导航修复**：切换到非阅读页自动退出专注模式，+生词按钮自动退出，生词本页面添加「← 返回阅读」按钮
- **专注模式触发区域扩大**：顶部 10px→30px，左侧 10px→20px，添加顶部边缘光效提示
- **查词与生词本分离**：查词记录放右侧边缘抽屉（鼠标移入展开/移出缩回），查词算法：1次记录→2次target→3次自动加入生词本
- **生词本增强**：点击展开完整释义卡片、单个删除、多选 checkbox + 批量操作栏
- **智能词汇测试**：基于用户数据权重抽样，优先出不会的词，多题型（单词释义/中文选单词/词形变化）
- **真题风格蒸馏**：从懒笔记爬取 205 篇四级真题阅读，量化风格分析（平均句长20.3词，复合句51.9%），风格参数融入生成 prompt
- **文章相似度去重**：TF-IDF + 余弦相似度（纯Python零依赖），生成后与最近10篇检测，阈值0.4，超过换主题重试最多3次
- **FSRS 深度整合**：文章选词 FSRS 驱动、测试结果更新 FSRS、复习队列 API
- **阅读统计面板**：8概览卡片 + 词汇状态分布 + FSRS统计 + 查词热词TOP10 + GitHub式热力图
- **三主题**：深色/明亮/纸质护眼（旧书羊皮纸质感）
- **动画系统**：全局 iOS 缓动曲线，页面切换/侧边栏/右侧面板/弹窗/打字机/生成等待话术全部丝滑化
- **自动专注模式**：文章互动（点击/滚动/划词）自动开启专注模式
- **自动侧边栏**：生成完成后自动收起，文章互动收起，鼠标移到左边缘展开

### ReadLoops 智能学习系统升级 — 已完成
- 项目改名：约读 → ReadLoops
- 每日测试弹窗：当天首次进入提示
- 词族融入：文章生成时自然使用目标词的派生词/词形变化
- 短语库：从真题提取1238个高频短语，每次生成选2-3个融入
- 长难句：每篇至少2个>25词的复杂句
- 数据层：words表加exchange/wrong_count/correct_count/last_test_at，建phrases/test_questions表
- ECDICT词形变化：导入28282个词的exchange数据

## 项目路径

- **项目根目录**：`~/约读/`（2026-09-10 从桌面移出，避免 iCloud 文件锁定）
- **代码目录**：`~/约读/app/`
- **数据目录**：`~/约读/data/`（`yuedu.db`，83MB，含 ECDICT 释义 104,150 条；该目录被 `.gitignore` 排除，不入版本库）
- **语料库**：`~/约读/语料库/`
  - `真题阅读纯文本/all_passages_lazynote.json` — 205 篇四级真题阅读（唯一现存真题语料，69KB/764KB）
  - `词表/`、`真题/` — **目录为空**（词表文件已不在磁盘，词数据已入库；42 套真题被 gitignore 排除）
- **文档**：`~/约读/docs/`（PROJECT_MEMORY.md / CHANGELOG.md / HANDOFF.md / UI_UX_EVOLUTION_PLAN.md）
- **公开仓库**：`~/readloops/`（开源独立项目，详见上方「双仓库结构」）

## 技术栈

- 后端：FastAPI + SQLite（无 ORM，原生 sqlite3）
- 前端：原生 HTML/CSS/JS（单页应用，零构建）
- AI：DeepSeek V4.1 Flash（`deepseek-flash`），Base URL `https://api.deepseek.com`
- 算法：**自研** FSRS 简化版（`app/services/srs.py`，仅依赖 time/math，**不是 py-fsrs**）+ 自研 TF-IDF 余弦相似度
- 设计风格：Apple Books 风格（无分割线、整体统一、玻璃悬浮按钮、三主题）+ 等宽字体 UI

## 启动方式

**方式 0：双击应用（推荐，最像"软件"）**

```
~/Applications/ReadLoops.app
```

双击即用：自动拉起后台服务 → 以 Chrome「应用模式」打开界面（独立窗口、无地址栏）。
可在「启动台」搜索 ReadLoops。重建命令：`bash ~/约读/tools/build_app.sh`

**关键**：必须用 `/opt/homebrew/bin/python3`（3.14.6，依赖装在这里）。**不要**用裸 `python3`——它可能指向别的解释器（无 fastapi）。

**方式 1：一键脚本**

```bash
~/约读/start.sh          # 前台运行，Ctrl+C 停止
~/约读/start.sh -d       # 后台运行，日志在 logs/server.log
```

**方式 2：手动命令**

```bash
cd ~/约读
/opt/homebrew/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**方式 3：开机自启（launchd）**

`~/Library/LaunchAgents/com.readloops.server.plist` 已就位（`KeepAlive` + `RunAtLoad`），登录时自动加载。

手动加载 / 卸载：

```bash
launchctl load -w ~/Library/LaunchAgents/com.readloops.server.plist   # 加载
launchctl unload -w ~/Library/LaunchAgents/com.readloops.server.plist # 卸载
```

访问 http://127.0.0.1:8000 ；健康检查 `curl http://127.0.0.1:8000/api/health`

> ⚠️ 不要同时用方式 1/2 和方式 3 —— 会抢占 8000 端口。

## 项目结构

```
~/约读/
├── app/
│   ├── main.py          # FastAPI 入口（含 /manifest.json 和 /icon.svg 路由）
│   ├── config.py        # 配置（路径、AI、FSRS）
│   ├── database.py      # SQLite 10表 schema + init_db
│   ├── models.py        # Article/Word/WordEncounter dataclass
│   ├── services/
│   │   ├── ai.py        # AI 接口层 + 文章生成（含去重检测循环）+ 查词
│   │   ├── srs.py       # FSRS 阅读驱动记忆
│   │   ├── scheduler.py # 生词调度器
│   │   ├── level.py     # 难度自适应
│   │   ├── corpus.py    # 词表导入
│   │   ├── similarity.py # TF-IDF + 余弦相似度文章去重
│   │   └── smart_test.py # 智能测试出题（权重抽样）
│   ├── api/
│   │   ├── articles.py  # 文章路由
│   │   ├── words.py     # 单词/词典/测试路由（含 /test/generate）
│   │   ├── reading.py   # 阅读行为路由（查词/高亮/会话，含 DELETE /highlight）
│   │   └── stats.py     # 统计+测试+设置路由
│   └── web/
│       ├── index.html   # 前端 HTML（含液态玻璃+水波纹 SVG filter）
│       ├── manifest.json # PWA 配置
│       ├── icon.svg     # PWA 应用图标
│       ├── css/style.css # 液态玻璃+动画+三主题样式
│       └── js/app.js    # 前端交互（导航/生成/打字机/查词/高亮/测试/统计/专注模式）
├── data/                # ⚠️ 被 .gitignore 排除，不入版本库
│   └── yuedu.db         # SQLite 数据库（83MB：words 175,777 行、ECDICT 释义 104,150、CET4级 4,530、exchange 28,282、phrases 1,238）
├── 语料库/
│   ├── 真题阅读纯文本/  # ✅ all_passages_lazynote.json（205篇四级真题阅读，唯一现存真题语料）
│   ├── 词表/            # ⚠️ 空目录（词表文件已不在磁盘，词数据已入 DB）
│   └── 真题/            # ⚠️ 空目录（42套真题被 .gitignore 排除）
├── tools/
│   ├── build_phrase_library.py  # 短语库构建
│   ├── import_exchange.py       # ECDICT 词形变化导入
│   ├── migrate_db_v2.py         # DB v2 迁移脚本
│   └── style_analysis/          # cet4_style_params.json（真题风格量化参数）
├── skills/
│   └── cet4-style-distiller/    # 真题风格蒸馏 Skill（含语料副本）
├── docs/                # PROJECT_MEMORY.md / CHANGELOG.md / HANDOFF.md / UI_UX_EVOLUTION_PLAN.md / decisions/
├── tests/               # ⚠️ 当前为空
└── .git/                # Git 仓库（master 分支）
```

## 数据库表结构（10表）

> **schema 单一事实源** = `app/database.py` 的 `SCHEMA`。
> `init_db()` 除建表外还会调用 `_ensure_columns()`，**启动时自动补齐历史库缺失的列**（幂等）。
> 新增字段时：改 `SCHEMA` + 在 `COLUMN_MIGRATIONS` 里登记，旧库即可自愈——不要再写一次性迁移脚本补列。

1. **users** — 用户（默认用户1）
2. **words** — 单词表（17.5万词，含 ECDICT 释义、exchange词形变化、FSRS字段、lookup_count、wrong_count、correct_count）
3. **word_encounters** — 单词遇见记录（查词/高亮）
4. **articles** — 文章（含 target_words JSON、word_count、new_word_count、reading_time_seconds）
5. **highlights** — 高亮记录（article_id、text、word_id、color、created_at）
6. **reading_sessions** — 阅读会话（start_time/end_time/duration_seconds/lookups/highlights）
7. **tests** — 测试记录（type、status、score、total、completed_at）
8. **settings** — 设置（key-value，含 AI API Key）
9. **phrases** — 短语库（1238个真题高频短语）
10. **test_questions** — 测试题目缓存

## 核心功能状态

| 功能 | 状态 | 说明 |
|------|------|------|
| 文章生成 | ✅ | DeepSeek V4.1 Flash，关闭思考模式，~300词，真题风格蒸馏，相似度去重 |
| 真题风格蒸馏 | ✅ | 205篇四级真题量化参数（cet4_style_params.json）融入生成 prompt |
| 相似度去重 | ✅ | TF-IDF+余弦（app/services/similarity.py），阈值0.4，超阈值换主题重试≤3次 |
| 划词查词 | ✅ | 本地 ECDICT 词典优先，查词记录放右侧抽屉，查词算法1→2→3次 |
| 生词本 | ✅ | 单个/批量添加、点击展开释义、单个删除、多选批量删除/标记已掌握 |
| 阅读计时 | ✅ | 空格控制，鼠标离开自动暂停，专注模式下底部玻璃计时条 |
| 高亮标记 | ✅ | 划词面板高亮，点击取消高亮，文章渲染后自动恢复高亮记录 |
| 历史文章 | ✅ | 列表+详情，显示日期，删除按钮，悬停显示详情（高频词/相似度） |
| 统计面板 | ✅ | 8概览卡片 + 词汇状态分布 + FSRS统计 + 查词热词 + GitHub式热力图 |
| 设置面板 | ✅ | AI 配置+测试连接+主题 |
| 四主题 | ✅ | 深色/纸质护眼/明亮/纯黑 |
| 单词测试 | ✅ | 智能出题（权重抽样优先不会的词），多题型，测试首页+答题页+结果页 |
| FSRS 记忆 | ✅ | 文章选词驱动，测试结果更新，复习队列 API，py-fsrs 算法 |
| 专注模式 | ✅ | 自动开启，水波纹引导，边缘触发UI，Esc/F/Space快捷键 |
| PWA | ✅ | 可安装为独立 Mac 应用，无浏览器标题栏 |
| 液态玻璃 | ✅ | SVG feTurbulence+feDisplacementMap 边缘折射，所有玻璃组件 |
| 语法测试 | ❌ | 未实现（测试首页显示"即将上线"占位） |
| 移动端适配 | ⏸️ | **暂缓**（2026-09-13 决定）。前置死结：服务绑 `127.0.0.1`，手机访问不到，需先解决远程访问 |

## AI 配置

- **服务商**：DeepSeek
- **Base URL**：`https://api.deepseek.com`
- **模型**：`deepseek-flash`（V4.1 Flash，2026-09-10 发布）
- **认证**：`Authorization: Bearer <api_key>`
- **思考模式**：强制关闭（`thinking: {"type": "disabled"}`）
- **API Key**：存在 settings 表，用户自行配置

## 关键理论数值

- 词汇覆盖率 95–98%（300词 6–15 个生词）
- 生词变化语境重遇 ≥12 次
- FSRS 期望保留率 90%，最大间隔 365 天
- 前 5 篇为校准期
- 查词率 >5% 减2个生词，<2% 加2个生词

## 已知限制

1. 语法测试题库未构建（测试首页显示"即将上线"占位）
2. 移动端**暂缓**（2026-09-13 决定）：这不是"做个 App"（项目是网页应用，无安装包），而是响应式改造。**前置死结**：服务绑 `127.0.0.1`，手机即使同 WiFi 也访问不到，需先解决远程访问方案
3. 真题语料仅用于「文章相似度匹配」；生成用的风格参数**已硬编码在 `ai.py` 的 prompt 中**，不依赖语料文件
4. 服务需要手动启动（没有 launchd 开机自启），电脑重启后需重新运行启动命令
5. 项目文件夹名仍是「约读」，数据库名 yuedu.db，应用名 ReadLoops（历史遗留，不影响功能）
6. **`语料库/词表/` 目录为空**：CET4/CET6 词表源文件已不在磁盘；词数据已导入 DB（level=CET4 共 4530 词），重新导入需自备词表文件
7. **`语料库/真题/` 目录为空**：42 套真题文件被 `.gitignore` 排除且已不在磁盘，仅剩空目录；现存真题语料只有 205 篇提取后的 JSON
8. **`data/` 与 `*.db` 被 gitignore 排除**：数据库不入版本库，克隆仓库后需另行获取/重建 yuedu.db 才能运行
9. **私有库 git 历史含 API Key**（提交 `096ac91` 的 DB blob）——**风险已受控，无需撤销 Key**：
   - 私有库**无远程仓库、从未推送**；公开库 `~/readloops/` 已确认 0 密钥
   - 已装 `pre-push` 钩子，**默认阻止推送私有库**（豁免：`READLOOPS_ALLOW_PUSH=1`）
   - 用户决定**继续使用该 Key**。前提：私有库永不推送、文件夹不外发
   - 若将来确需分享私有库，**先清除历史 blob**（`git filter-repo`），否则密钥随历史外泄
10. **双仓库需手动同步**：`~/约读/`（私有）与 `~/readloops/`（公开）是两份代码，改动不会自动同步

## 历史决策记录

详见 `docs/decisions/2026-09-10-requirements-log.md`

## 变更日志

### 2026-09-13（打包为 macOS 应用）

- 新增 `~/Applications/ReadLoops.app`：**双击即用**——自动拉起后台服务，再以 Chrome「应用模式」打开界面（独立窗口、无地址栏、无标签页），体验等同普通 Mac 软件。
- 新增 `tools/build_app.sh`：可复现构建脚本。图标由 `app/web/icon.svg` 经 Chrome 无头渲染 → `sips` 多尺寸 → `iconutil` 打包为 `.icns`。
- **定位澄清**：ReadLoops 本质是「本地服务 + 浏览器界面」的网页应用，不是原生程序。`.app` 的作用是把"起服务 + 开浏览器"这两步自动化，不是把 Python 编译成原生代码。
- 启动器行为：优先用 launchd 起服务（持久 + 崩溃自启），失败则回退 `nohup` 直接拉起；服务已在跑时只开窗口。

### 2026-09-13（密钥风险复核 + 推送保护）

- **复核结论：API Key 未泄露到外部**。私有库无远程仓库、从未推送；公开库经扫描 0 密钥匹配。
- **决定：继续使用该 Key，不撤销**（此前"必须撤销"的建议已修正——那是基于"会推送"的假设，实际不成立）。
- 新增 `.git/hooks/pre-push`：**默认阻止推送私有库**，从机制上堵死误推送。豁免：`READLOOPS_ALLOW_PUSH=1`。
- 遗留前提：私有库永不推送、文件夹不外发；将来若需分享，先清除历史 blob。

### 2026-09-13（v2.2.3 — 修复单词测试 500 + schema 自愈）

**Bug 修复（用户报告「单词测试 → 生成题目失败」）：**
- 根因 1：`app/api/words.py` 的 `POST /api/words/test/start` 往 `tests` 表插 `user_id`，但该列**不存在** → 500。前端吞掉了这个错误，导致**测试成绩永不入库**（`testId` 为 null，FSRS/统计全不更新）
- 根因 2：`tests` 表 schema 漂移——`database.py` SCHEMA（含 status/total/completed_at）与真实数据库（含 level_estimate/questions）**三份定义打架**
- 修复：SQL 对齐为 `(type, status, created_at)`；`init_db()` 新增 `_ensure_columns()` **启动时自动补齐缺失列**，从机制上杜绝此类漂移

**数据操作：**
- 迁移前已备份 `backups/yuedu.db.<时间戳>.bak`
- 迁移为**纯增量**（ALTER TABLE ADD COLUMN），数据零丢失（integrity_check: ok，各表行数一致）

**测试：**
- 新增 `tests/test_api_endpoints.py`（15 个接口级用例），覆盖所有只读端点 + 测试记录流程
- 测试数 19 → **34**，全部通过

### 2026-09-13（v2.2.2 — 开源拆分 + 安全审计）

**安全（P0）：**
- 发现私有库历史提交 `096ac91` 含 `data/yuedu.db`（87MB），内有真实 API Key 与个人阅读记录
- 处置：撤销 Key（用户操作）+ 保持私有库不推送；不重写历史

**开源：**
- 新建公开仓库 `~/readloops/`（MIT，历史从零，终检 0 泄露，`.git` 344K）
- 脱敏：剔除数据/语料/内部文档；修复 9 处硬编码绝对路径
- 新增开源三件套 + 质量基线（ruff / pytest 19 用例 / pre-commit / CI / CONTRIBUTING / ARCHITECTURE）

**缺陷修复：**
- `database.py` schema 补全（缺 `phrases`/`test_questions` 表 + `srs_interval` 等 5 列）——修复"全新安装必崩"
- 4 处裸 `except`、1 处重复字典键

**文档校正：**
- 技术栈：`py-fsrs` → **自研 FSRS**（实际只依赖 time/math）
- 风格参数：并非运行时加载 JSON，而是**硬编码在 `ai.py` prompt 中**

### 2026-09-13（交接文档 v2.2.1 — 文件索引校正）

**新增：**
- `docs/HANDOFF.md` 交接文档：功能状态、已知问题、待办、快速上手检查清单、完整文件位置索引

**文档校正（核查磁盘实际状态后修正）：**
- `语料库/词表/` 标注为**空目录**（此前误记为含 CET4/CET6 词表文件）
- `语料库/真题/` 标注为**空目录**（此前误记为含 42 套真题）
- 数据库大小校正为 **83MB**，words 表精确行数 **175,777**（CET4 级 4,530 / 有释义 104,150 / exchange 28,282）
- 明确 `data/`、`*.db`、`语料库/真题/` 被 `.gitignore` 排除，不入版本库
- 补齐 `tools/` 脚本清单与 `tests/` 空目录说明

**Git：**
- 本次文档变更提交，记录 commit hash，工作区保持干净

### 2026-09-10（项目创建日）

**重大变更：**
- 项目从 `~/Desktop/约读/` 移动到 `~/约读/`，彻底解决 iCloud 文件锁定问题
- 前端全面重写为 OpenCode 终端风格（等宽字体、深色高对比、无 emoji）
- 所有 API 路由重写（articles/words/reading/stats）
- 导入 ECDICT 完整英汉词典（76万词条，10.4万词有释义）
- AI 切换到 DeepSeek V4.1 Flash（`deepseek-flash`），强制关闭思考模式
- 查词改为本地优先，AI 兜底
- 鼠标离开窗口自动暂停计时
- 页面加载自动显示最新文章
- 历史文章列表显示日期时间

**问题修复：**
- 修复文章列表 API 500 错误
- 修复 JSON 解析失败（DeepSeek 思考模式导致）
- 修复词表为空导致生成失败
- 修复浏览器缓存旧 JS 导致查词显示"未找到释义"
