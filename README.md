# ReadLoops

[![PyPI](https://img.shields.io/pypi/v/readloops)](https://pypi.org/project/readloops/)
[![Python versions](https://img.shields.io/pypi/pyversions/readloops)](https://pypi.org/project/readloops/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Release](https://img.shields.io/github/v/release/pekoqq/ReadLoops)](https://github.com/pekoqq/ReadLoops/releases)

> AI 驱动的英语阅读训练器。基于「可理解输入（i+1）」+ 阅读驱动的 FSRS 间隔重复，面向大学英语四级（进阶六级）。

## 它解决什么问题

背单词软件的问题是「脱离语境」。ReadLoops 反过来：**先读文章，在语境里反复遇见生词，再用 FSRS 安排复习**。文章由 AI 生成，用词受控（95–98% 词汇覆盖率），风格对齐四级真题。

## 功能

- **AI 文章生成** — 受控生词量（每篇 6–15 个），真题风格蒸馏，TF-IDF 相似度去重
- **划词查词** — 本地词典优先，AI 兜底；查词 1 次记录 → 2 次标记 → 3 次自动进生词本
- **生词本** — 增删改查、批量操作、词形变化、FSRS 记忆强度
- **高亮标记** — 划词高亮、点击取消、重渲染后自动恢复
- **专注模式** — 自动进入、边缘触发工具栏、水波纹引导
- **智能词汇测试** — 按用户数据权重抽样，多题型
- **FSRS 间隔重复** — 文章选词驱动，测试结果回写
- **阅读统计** — 概览卡片、词汇分布、查词热词、GitHub 式热力图
- **PWA** — 可安装为独立桌面应用
- **多主题** — 深色 / 明亮 / 纸质护眼 / 纯黑

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | FastAPI + SQLite（无 ORM，原生 `sqlite3`） |
| 前端 | 原生 HTML / CSS / JS 单页应用（零构建、零依赖） |
| AI | DeepSeek（`deepseek-flash`），强制关闭思考模式 |
| 算法 | 自研 FSRS（`app/services/srs.py`）+ 自研 TF-IDF 余弦相似度 |

设计上刻意保持轻量：**后端零 ORM、前端零框架、算法零第三方依赖**。

## 快速开始

支持 **Windows / macOS / Linux**。需要 Python 3.10 或更高版本。

### 方式一：pip 安装（推荐）

```bash
pip install readloops

readloops init     # 初始化数据库
readloops          # 启动，并自动打开浏览器
```

> 若提示 `readloops: command not found`，说明 Python 的脚本目录不在 PATH 中。
> 改用 `python -m app.cli` 或参考下方「方式二」。

### 方式二：从源码安装

```bash
git clone https://github.com/pekoqq/ReadLoops.git && cd ReadLoops

# 创建虚拟环境
python3 -m venv .venv

# 激活（按系统选择）
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows (PowerShell / CMD)

pip install -e .                 # 以可编辑模式安装
readloops                        # 启动
```

### 方式三：Docker（推荐用于服务器 / 长期运行）

```bash
docker compose up -d
```

或不用 compose：

```bash
docker build -t readloops .
docker run -d --name readloops \
  -p 127.0.0.1:8000:8000 \
  -v readloops-data:/data \
  --restart unless-stopped \
  readloops
```

- **三大系统命令完全一致**，不依赖本机 Python 环境
- 数据库持久化在 `readloops-data` 卷中，容器重建不丢数据
- 默认只绑定本机（安全）。要让局域网 / 其他设备访问，把 `docker-compose.yml` 的端口改成 `"8000:8000"`
- 查看日志：`docker logs -f readloops`

### 命令行说明

| 命令 | 作用 |
|------|------|
| `readloops` | 启动服务并打开浏览器（默认命令） |
| `readloops serve` | 只启动服务 |
| `readloops serve --port 9000 --no-browser` | 自定义端口、不打开浏览器 |
| `readloops init` | 初始化数据库 |
| `readloops doctor` | 检查运行环境是否就绪 |

启动后访问 <http://127.0.0.1:8000>，进入「设置」填入 AI API Key，即可生成第一篇文章。

> 首次启动会自动创建数据库并建表。但**词库为空时无法生成文章**，见下一节。

## 数据准备（重要）

仓库**不包含任何数据文件**。原因见「版权说明」。

### 1. 词典 / 词表

文章生成需要一份词表来挑选目标生词。推荐使用开源词典 [ECDICT](https://github.com/skywind3000/ECDICT)：

- 下载 `ecdict.csv`（或使用项目提供的 `tools/` 脚本）
- 词表需导入 `words` 表，字段：`lemma / text / meaning / phonetic / level`

`app/services/corpus.py` 提供了 `import_word_list(filepath, level)` 用于导入纯文本词表（格式：`word [音标] 词性.释义`）。

### 2. 真题语料（可选）

用于「文章相似度匹配」。**受版权保护，需自行准备**：

- 项目提供抓取脚本 `tools/style_analysis/crawl_lazynote.py`，请自行确认目标站点的使用条款
- 语料文件默认路径：`语料库/真题阅读纯文本/all_passages_lazynote.json`
- 可用环境变量 `READLOOPS_CORPUS_FILE` 指定其他路径
- **未提供语料时程序照常运行**，仅跳过相似度匹配

> 注：生成用的「真题风格参数」已固化在 `app/services/ai.py` 的 prompt 模板中，**不依赖语料文件**。
> `tools/style_analysis/cet4_style_params.json` 是分析产物，仅在你想重新蒸馏风格时才需要语料。

## 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `AI_BASE_URL` | `https://api.deepseek.com` | AI 接口地址 |
| `AI_API_KEY` | 空 | API Key（也可在网页「设置」中填写，存于数据库） |
| `AI_MODEL` | `deepseek-flash` | 模型名 |
| `READLOOPS_DATA_DIR` | 见下 | 数据目录（数据库存放位置） |
| `READLOOPS_DB_PATH` | `<数据目录>/yuedu.db` | 数据库文件路径 |
| `READLOOPS_CORPUS_FILE` | `语料库/真题阅读纯文本/all_passages_lazynote.json` | 真题语料路径 |
| `READLOOPS_CORPUS_DIR` | `语料库/真题阅读纯文本` | 抓取脚本输出目录 |

### 数据目录在哪

程序会自动选择合适的位置，无需手动配置：

| 运行方式 | 数据目录 |
|----------|----------|
| 源码模式（项目根有 `pyproject.toml`） | `<项目根>/data` |
| pip 安装 · Windows | `%LOCALAPPDATA%\ReadLoops` |
| pip 安装 · macOS | `~/Library/Application Support/ReadLoops` |
| pip 安装 · Linux | `~/.local/share/readloops` |

用 `readloops doctor` 可随时查看当前实际路径。想自定义就设 `READLOOPS_DATA_DIR`。

> 数据库**不会**写入 Python 安装目录（site-packages），升级或卸载包不会影响你的数据。

## 在其他设备上访问

默认只绑定 `127.0.0.1`——即只有本机能访问。要让手机或别的电脑也能用：

### 同一局域网

```bash
readloops serve --host 0.0.0.0
```

Docker 用户把 `docker-compose.yml` 的端口改成 `"8000:8000"`。然后访问 `http://<电脑局域网IP>:8000`。

> ⚠️ 局域网内任何人都能访问，且**没有密码保护**。仅在可信网络下这样做。

### 通过 Tailscale（可在外网使用）

若你的设备都在同一 Tailscale 网络中：

1. 在常开的机器上跑服务，监听 `0.0.0.0`
2. 手机安装 Tailscale 并登录同一账号
3. 访问 `http://<机器的Tailscale IP>:8000`

只有你自己的设备能访问，且不受网络位置限制。

> 说明：界面目前未做手机小屏的专项适配，能打开使用但体验一般；响应式优化在路线图中。

## 项目结构

```
readloops/
├── app/
│   ├── cli.py               # 命令行入口（readloops serve / init / doctor）
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 路径与全局配置（跨平台数据目录）
│   ├── database.py          # SQLite schema（10 表）+ init_db
│   ├── models.py            # 数据模型
│   ├── api/                 # 路由：articles / words / reading / stats
│   ├── services/
│   │   ├── ai.py            # AI 调用 + 文章生成（含去重循环）
│   │   ├── srs.py           # FSRS 间隔重复（自研）
│   │   ├── similarity.py    # TF-IDF + 余弦相似度（自研）
│   │   ├── smart_test.py    # 智能出题
│   │   ├── scheduler.py     # 生词调度
│   │   ├── level.py         # 难度自适应
│   │   └── corpus.py        # 词表导入
│   └── web/                 # 前端（index.html / css / js / PWA）
├── tools/                   # 数据构建脚本
├── skills/                  # 真题风格蒸馏 Skill
├── tests/                   # 测试
└── docs/                    # 架构与变更文档
```

## 开发与质量

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 代码检查与格式化
ruff check .
ruff format .

# 运行测试
pytest

# 安装 git 钩子（提交前自动检查）
pre-commit install
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 版权说明

- **本仓库不含词典与真题语料**。词表请使用开源词典（如 ECDICT）；真题语料版权归原作者/出版方所有，本项目不分发，仅提供自用抓取脚本。
- 生成的文章内容由 AI 产生，请自行判断其准确性与适用性。
- 本项目仅供个人学习使用。

## 许可证

[MIT](LICENSE)
