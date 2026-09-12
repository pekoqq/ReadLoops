---
name: cet4-style-distiller
description: 四级真题阅读风格蒸馏器。从205篇四级真题阅读（2015-2026）中提取量化风格参数，用于指导AI生成符合四级真题风格的阅读文章。包含语料爬取、风格分析、参数生成完整流程。
version: 1.0
license: MIT
---

# 四级真题阅读风格蒸馏器

## 概述

本 skill 从 205 篇大学英语四级真题阅读文章（135篇仔细阅读 + 70篇长篇阅读，2015-2026年）中提取量化风格参数，用于指导 AI 生成符合四级真题风格的阅读文章。

## 数据来源

- **语料规模**：205篇文章，122,566词，6,024句
- **时间范围**：2015年6月 - 2026年6月
- **来源网站**：懒笔记 (english-exam.lazynote.cn)
- **语料文件**：`data/all_passages_lazynote.json`

> ⚠️ **语料不随本仓库分发**。真题阅读属版权材料，请自行确认目标站点的使用条款后，
> 用 `scripts/crawl_lazynote.py` 抓取（输出目录可用环境变量 `READLOOPS_CORPUS_DIR` 指定）。
> 本文档中的风格参数已固化在 `tools/style_analysis/cet4_style_params.json`，**无需语料即可直接使用**。

## 核心风格参数

### 文本特征
- 平均句长：20.3词（中位数18词）
- 句长分布：短句18.4%，中句40.0%，长句41.6%
- 平均词长：4.79字母
- 词长分布：短词36.2%，中词40.1%，长词23.7%

### 句式特征
- 复合句（and/but/or/so）：51.9%
- 定语从句（which/that/who）：32.0%
- 状语从句（because/although/if等）：30.8%
- 高频连接词：and, that, as, but, or, when, who, if, so, which, because, while

### 结构特征
- 开头方式：冠词开头12%，从句开头6%，名词短语开头40%，疑问句8%，介词短语10%
- 结尾方式：陈述事实52.7%，情态动词36.1%，转折9.8%，强调重要性1.5%

### 题材分布
- 科技/互联网（最高频）
- 社会/生活（高频）
- 环境/气候（高频）
- 教育/学习（高频）
- 健康/医学（高频）
- 商业/经济（中频）
- 心理/行为（中频）

### 高频词汇
- 实词：people, time, new, work, students, food, study, research, school, women, life, way
- 短语：high school, years ago, long term, young people, social media, climate change, work life, health care

### 词族与词形变化
- ECDICT 词典提供 exchange 字段，记录动词时态、名词复数、形容词比较级等词形变化
- 文章生成时应自然使用目标词的派生词和词形变化，而不仅是原型
- 常见转换：动词→名词(-tion/-ment)、形容词→副词(-ly)、名词→形容词(-al/-ive)
- 例子：develop → development, developing, developed; act → action, active, activity

### 短语库
- 从 205 篇真题中提取 1238 个高频短语（频率≥3）
- 文章生成时每次选 2-3 个目标短语自然融入
- 高频短语：high school, years ago, long term, young people, social media, climate change, work life, fast food, united states, last year

### 长难句
- 平均句长 20.3 词，长句（>20词）占 41.6%
- 每篇文章应包含至少 2 个 >25 词的复杂长句
- 典型结构：定语从句嵌套、状语从句+名词性从句、非谓语动词短语、插入语
- 长句应自然，不为了长而长

## 使用方法

### 1. 重新爬取语料（可选）

```bash
python3 scripts/crawl_lazynote.py
```

会从懒笔记网站爬取全部仔细阅读和长篇阅读文章，保存到 `data/all_passages_lazynote.json`。

### 2. 运行风格分析

```bash
python3 scripts/analyze_style.py
```

会分析语料文件，生成：
- `data/cet4_style_params.json` — 量化风格参数
- `docs/style_analysis_report.md` — 人类可读分析报告

### 3. 在文章生成中使用风格参数

风格参数已融入 `app/services/ai.py` 的 `generate_article()` 函数。prompt 中包含：
- 详细的句长、句式、词汇要求
- 开头和结尾方式的随机选择
- 明确的禁止事项（AI套话）
- 题材加权随机选择

## 文件结构

```
cet4-style-distiller/
├── SKILL.md                          # 本文件
├── scripts/
│   ├── crawl_lazynote.py            # 语料爬取脚本
│   └── analyze_style.py             # 风格分析脚本
├── data/
│   ├── all_passages_lazynote.json   # 205篇真题语料
│   └── cet4_style_params.json       # 量化风格参数
└── docs/
    └── style_analysis_report.md     # 分析报告
```

## 写作风格规则

### DO（要做）
- 用客观、信息性的语气写作
- 从具体场景、事实或研究发现开头
- 包含具体数字、数据和研究结果
- 使用 and, but, that, which, when, if 等连接词
- 混合使用简单句、复合句和复杂句（平均句长20词）
- 用情态动词(may/could/should)结尾表达推测或建议
- 引用研究、专家或数据来支撑观点
- 讨论社会现象、科技影响、健康、教育等话题

### DON'T（不要做）
- 不要用 In today's fast-paced world 开头
- 不要用 In conclusion, Therefore, As we all know 结尾
- 不要用 It is important to note that 这类套话
- 不要写过于口语化或随意的表达
- 不要用第一人称 I/we 表达个人观点
- 不要写过于情绪化或主观的语言
- 不要用感叹号
- 不要写过于简短的句子（少于5词的句子要少）

## 扩展

### 添加六级真题
修改 `scripts/crawl_lazynote.py`，将 URL 从 `/cet4/` 改为 `/cet6/`，即可爬取六级真题。

### 更新风格参数
当有新真题发布时，重新运行爬取和分析脚本即可更新风格参数。

## 参考

- 懒笔记英语四级真题：https://english-exam.lazynote.cn/cet4/
- 四级考试大纲：教育部考试中心
