# 贡献指南

感谢参与 ReadLoops。本文档定义开发流程与质量标准——**请先读完再提 PR**。

## 1. 环境搭建

```bash
git clone https://github.com/pekoqq/ReadLoops.git && cd ReadLoops
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pre-commit install          # 安装提交前钩子
```

运行应用：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 2. 分支与提交

- 主分支：`main`（历史仓库可能为 `master`）
- 功能分支命名：`feat/简短描述`、`fix/简短描述`、`docs/简短描述`
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: 新增语法测试题型
fix: 修复专注模式下计时条不隐藏
docs: 补充架构说明
refactor: 抽取 FSRS 更新逻辑
test: 补充相似度边界用例
```

- **一次提交只做一件事**。重构与功能改动分开提交。

## 3. 代码规范

工具链：`ruff`（lint + format）。

```bash
ruff check .        # 检查
ruff check --fix .  # 自动修复
ruff format .       # 格式化
```

约定：

- 行宽 120；字符串优先单引号（由 ruff 统一）
- **禁止裸 `except:`** —— 必须写 `except Exception:` 或更具体
- 不在代码里硬编码绝对路径；路径一律经 `app/config.py`
- 新增配置项时，同步更新 `README.md` 的配置表

## 4. 测试

```bash
pytest
```

要求：

- **新功能必须带测试**；修 bug 时先补一个能复现的测试
- 测试必须跑在隔离的数据目录（`tests/conftest.py` 已处理），**绝不允许碰真实 `data/yuedu.db`**
- 不引入需要网络或真实 API Key 的测试

## 5. 提交前检查

`pre-commit` 会自动跑：尾随空格、文件末尾换行、大文件拦截、私钥检测、ruff。

手动确认：

```bash
ruff check . && pytest
```

## 6. 严禁提交的内容

- ❌ `data/`、任何 `*.db`（含个人数据与 API Key）
- ❌ 真题语料、词典数据文件
- ❌ `.env`、密钥、token
- ❌ `.DS_Store`、`__pycache__/`、编辑器配置

> 这些已在 `.gitignore` 中排除。**不要用 `git add -f` 强行绕过。**

## 7. PR 流程

1. 从 `main` 拉分支
2. 完成改动 + 测试，本地跑通 `ruff check . && pytest`
3. 提交 PR，描述里写清楚：**改了什么 / 为什么 / 怎么验证**
4. 至少 1 人 review 通过后合并

## 8. 代码审查清单

Review 时逐条对照：

**正确性**
- [ ] 逻辑与需求一致，边界条件（空值/空表/首次运行）有处理
- [ ] 异常处理具体，无裸 `except`
- [ ] SQL 用参数化查询，无字符串拼接注入风险

**数据安全**
- [ ] 没有引入会写入/删除 `data/` 的代码路径
- [ ] 没有硬编码密钥、token、个人路径
- [ ] 新增数据库字段时，同步更新 `app/database.py` 的 `SCHEMA`

**可维护性**
- [ ] 命名清晰；函数职责单一
- [ ] 复杂逻辑有注释说明"为什么"，而非"做了什么"
- [ ] 未引入不必要的第三方依赖

**测试**
- [ ] 新功能/修复带测试，且测试能真正失败（不是永真断言）

**文档**
- [ ] 影响用户使用方式的改动，同步更新 README / ARCHITECTURE

## 9. 行为准则

参与本项目即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
