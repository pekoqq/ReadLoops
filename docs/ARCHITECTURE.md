# 架构说明

> 面向接手代码的开发者。读完这一份，你应该能定位任何功能对应的文件。

## 1. 分层总览

```
┌─────────────────────────────────────────────────────┐
│  前端（原生 HTML/CSS/JS 单页应用，零构建）              │
│  app/web/index.html · css/style.css · js/app.js      │
└───────────────────────┬─────────────────────────────┘
                        │ HTTP / JSON（REST）
┌───────────────────────▼─────────────────────────────┐
│  API 层（FastAPI 路由）                                │
│  app/api/articles.py · words.py · reading.py · stats.py │
└───────────────────────┬─────────────────────────────┘
                        │ 函数调用
┌───────────────────────▼─────────────────────────────┐
│  服务层（业务逻辑）                                     │
│  ai · srs · similarity · smart_test · scheduler ·      │
│  level · corpus                                        │
└───────────────────────┬─────────────────────────────┘
                        │ sqlite3（无 ORM）
┌───────────────────────▼─────────────────────────────┐
│  数据层：app/database.py（schema 单一事实源）           │
│  SQLite：data/yuedu.db（10 表）                        │
└─────────────────────────────────────────────────────┘
```

## 2. 目录职责

| 路径 | 职责 | 不该做的事 |
|------|------|-----------|
| `app/main.py` | 应用装配：注册路由、挂载静态文件、PWA 路由 | 不放业务逻辑 |
| `app/config.py` | 所有路径与常量；支持环境变量覆盖 | 不读数据库 |
| `app/database.py` | 连接管理 + schema + 初始化 | 不写业务查询 |
| `app/models.py` | 数据模型 | — |
| `app/api/*` | 参数校验、调用服务、组装响应 | 不写 SQL、不写算法 |
| `app/services/*` | 业务逻辑与算法 | 不碰 HTTP 层 |
| `app/web/*` | 前端全部资源 | — |
| `tools/*` | 一次性数据构建脚本（建库、导入、迁移） | 不被运行时导入 |
| `tests/*` | 测试 | — |

**依赖方向单向向下**：`api → services → database`。服务之间可互相调用，但不得反向依赖 api 层。

## 3. 关键数据流

### 3.1 文章生成

```
前端点击"生成"
  → POST /api/articles/generate
  → services/ai.py: generate_article()
      1. 选目标生词（services/level.py + services/srs.py）
         · 优先级：到期复习 > 快到期 > 低稳定性 > 生词本新词
      2. 取词族（words.exchange）+ 短语库（phrases）
      3. 拼 prompt（风格要求已**硬编码**在 `ai.py:_generate_once()` 的 prompt 模板中；
         `tools/style_analysis/cet4_style_params.json` 是分析产物与文档，运行时不加载）
      4. 调用 AI（DeepSeek，强制关闭思考模式）
      5. 去重检查（services/similarity.py，阈值 0.4）
         · 超阈值 → 换主题重试，最多 3 次
      6. 落库 articles
  → 返回文章
```

### 3.2 划词查词

```
划词 → POST /api/reading/lookup
  → 本地词典命中？→ 直接返回
  → 未命中 → AI 兜底
  → 记录 word_encounters
  → 查词计数：1 次仅记录 → 2 次标 target → 3 次自动进生词本
```

### 3.3 测试与复习

```
出题：services/smart_test.py（按用户数据权重抽样，优先不会的词）
判分：POST /api/words/test/{id}/submit
  → 更新 words.wrong_count / correct_count
  → services/srs.py: update_after_review() 回写 FSRS
  → 答错自动加入生词本

复习队列：GET /api/words/review-due
  → services/srs.py: get_due_words()（status ∈ learning/target 且 srs_due ≤ now）
```

## 4. 数据库设计要点

- **10 张表**：`users` `words` `word_encounters` `articles` `highlights`
  `reading_sessions` `tests` `settings` `phrases` `test_questions`
- **`app/database.py` 的 `SCHEMA` 是结构的单一事实源**。新增字段/表时直接改 SCHEMA；
  `tools/migrate_db_v2.py` 只用于升级历史遗留的旧库，不承担"补全结构"职责。
  > 历史教训：曾因 schema 与迁移脚本不同步，导致全新安装时缺列报错。
- **无 ORM**：直接用 `sqlite3`，连接通过 `get_db()` 上下文管理器获取（自动 commit/close）。
- 数据库文件被 `.gitignore` 排除，**永不入库**（可能含个人数据与 API Key）。

## 5. 关键设计决策

| 决策 | 理由 |
|------|------|
| 前端零框架、零构建 | 单人维护，降低工具链负担；单页应用体量可控 |
| 后端无 ORM | 查询简单；避免 ORM 抽象成本 |
| 相似度算法自研（纯 Python） | 零依赖；TF-IDF + 余弦足够解决去重 |
| FSRS 自研简化版 | 可控、可读；不引入 py-fsrs 依赖 |
| API Key 存数据库而非配置文件 | 用户可在界面配置；配合 `.gitignore` 防止误提交 |
| 语料不随仓库分发 | 真题受版权保护 |

## 6. 扩展指南

**加一个新测试题型**：
1. `app/services/smart_test.py` 增加出题函数
2. `app/api/words.py` 的生成逻辑登记新题型
3. 前端 `app/web/js/app.js` 增加渲染分支
4. `tests/` 补一个出题函数单测

**加一个新服务**：
1. 在 `app/services/` 建模块，只依赖 `database` 与 `config`
2. 在 `app/api/` 建路由，只做参数校验与响应组装
3. 在 `app/main.py` 注册路由
4. 补测试

## 7. 相关文档

- [ROADMAP.md](ROADMAP.md) — 功能路线图与已知缺口
- [CHANGELOG.md](CHANGELOG.md) — 版本变更历史
- [../CONTRIBUTING.md](../CONTRIBUTING.md) — 开发与提交流程
