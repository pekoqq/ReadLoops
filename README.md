# ReadLoops

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

```bash
# 1. 克隆
git clone <repo-url> && cd readloops

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. 启动
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，进入「设置」填入 AI API Key，即可生成第一篇文章。

> 首次启动会自动创建 `data/yuedu.db` 并建表。但**词库为空时无法生成文章**，见下一节。

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
| `READLOOPS_CORPUS_FILE` | `语料库/真题阅读纯文本/all_passages_lazynote.json` | 真题语料路径 |
| `READLOOPS_CORPUS_DIR` | `语料库/真题阅读纯文本` | 抓取脚本输出目录 |

## 项目结构

```
readloops/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 路径与全局配置
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
