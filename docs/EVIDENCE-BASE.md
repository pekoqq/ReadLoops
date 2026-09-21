# ReadLoops 的文献依据

> 版本：v1（2026-09-16）
> 目的：项目每一项设计决策，都要能追溯到真实文献。**不靠印象，不编数字。**
> 所有条目均给出可核验的来源链接。

---

## 一、核心命题：阅读能同时提升词汇与各项语言能力

### 证据 1 — Nakanishi (2015)，TESOL Quarterly 49(1): 6–37

[*A Meta-Analysis of Extensive Reading Research*](https://onlinelibrary.wiley.com/doi/full/10.1002/tesq.157)

- 纳入 **34 组前后测比较**，其中 **18 组为实验组 vs 对照组**
- 结论：泛读能促进二语学习者的 **阅读速度、阅读理解、词汇、语法**
- 效应量：前后测 **d = 0.71**；对比对照组 **d = 0.46**

### 证据 2 — 2025 年元分析，Educational Psychology Review

[*Learning a Language Through Reading: A Meta-analysis of Studies on the Effects of Extensive Reading on Second and Foreign Language Learning*](https://link.springer.com/article/10.1007/s10648-025-10068-6)

- 结论：泛读的效果**在全部语言领域上都为正** ——
  **阅读理解、词汇、解码/流畅度、动机、写作、口语、整体语言水平**
- 效应量：小到中等（总体 **d = 0.39**，子集 **d = 0.41**）
- 覆盖各年龄段与各水平学习者

> **这一条直接回答了「阅读能否提升各项能力」：能，且不止词汇。**
> 但效应量是「小到中等」—— 这很重要，说明**光靠读、随便读，效果有限**。
> 效果大小取决于怎么读（见下节）。

---

## 二、关键：什么条件下效果更大（本项目设计的核心依据）

同一份 2025 元分析给出了**两个显著调节变量**：

> "when learners' **text choice was limited** and when some form of **accountability
> was included**, effects were larger than when these elements were absent."
>
> 另外：「adult readers seemed to benefit most」（成年学习者获益最大）

| 调节变量 | 含义 | ReadLoops 的对应 |
|---|---|---|
| **文本选择受限** | 不是随便读，而是**有针对性地选文本** | 按缺口选词 → 定向生成文章 |
| **有问责机制** | 有某种检验/追踪，不能读了就算 | 掌握度模型 + 单词测试 + 重遇追踪 |
| **成年学习者** | 成人比儿童获益更大 | 目标用户正是大学生/成人 |

**这是整个项目最重要的一条依据**：它说明
**「泛读 + 定向 + 追踪」的效果显著优于「泛读」本身** ——
而 ReadLoops 做的正是前者。

> ⚠️ 注意：如果只做成「随便生成一篇文章给你读」，就落到了那两个调节变量的**反面**，
> 效果会明显更差。这两条设计不是锦上添花，是效果开关。

---

## 三、覆盖率阈值：95% 还是 98%

这是最容易被误解的地方，需要把脉络讲清楚。

### 原始研究

| 研究 | 发现 |
|---|---|
| [Laufer (1989)](https://onlinelibrary.wiley.com/doi/full/10.1111/lang.12622) | 首次研究覆盖率阈值；**95% 覆盖率下更多学习者达到 55% 的阅读理解正确率**；建议 95% 可能足够 |
| [Hu & Nation (2000)](https://onlinelibrary.wiley.com/doi/full/10.1111/lang.12622) | 80% → 理解不足；90% → 少数达标；**95% → 多数仍未达「充分理解」**；由回归推断 **98% 才够** |

Hu & Nation 的 98% 后来成了**领域惯例**，[Nation (2006)](https://www.lextutor.ca/cover/papers/nation_2006.pdf) 据此推出
「98% 覆盖需 8,000–9,000 词族」。

### 但对 98% 的重要修正

| 研究 | 发现 |
|---|---|
| **Laufer & Ravenhorst-Kalovski (2010)** | **95% 覆盖率在有支持（with support）时足以支撑阅读理解；98% 时才不需要支持** |
| [Kremmel et al. 复制研究](https://onlinelibrary.wiley.com/doi/full/10.1111/lang.12622) | 90% 与 98% 覆盖率下的理解程度**相近**（86% vs 89%）；95% 与 98% 条件下约 82% 的参与者达标。**98% 这个数字并不像通常认为的那样牢固** |
| Schmitt et al. (2011) | 98% 覆盖率 → 约 70% 理解度 |
| Van Zeeland & Schmitt (2013) | 听力：90% 与 95% 覆盖率下多数学习者已达标 |

### 本项目的取值：**95%，且必须配「支持」**

依据 **Laufer & Ravenhorst-Kalovski (2010)**：95% 覆盖率**配上支持**即可。

「支持」在 ReadLoops 里是具体功能：
- 超出已知集的词**自动标注**，点按即时看释义
- 长难句结构解析
- 目标词在语境中附带提示

> **这条修正非常关键**：它把「必须 98% 才敢读」的门槛降到了「95% + 辅助」。
> 而 98% 的代价是——一篇文章只能埋 1–2 个生词，学习密度太低。
> **95% + 支持 = 可读性与学习密度的最优解。**

---

## 四、遇见次数：为什么是 12 次

| 来源 | 结论 |
|---|---|
| [Pellicer-Sánchez (2015)](https://doi.org/10.1017/S0272263115000076)，*SSLA* 38(1): 97–130 | 8 次遇见 → 词形识别 86%、意义识别 75%，但**回忆仅 55%** |
| [What can readers read after graded readers?](https://scholarspace.manoa.hawaii.edu/bitstreams/c6d886be-2937-46d3-8296-dd6d26e65729/download) | **习得一个词族需在文中遇见 ≥12 次** |
| [Webb (2007)](https://doi.org/10.1093/applin/aml045)，*Applied Linguistics* 28(1): 46–65 | **不存在能保证习得的最少遇见次数**；不同知识维度需要不同次数 |
| [Brown, Waring & Donkaewbua (2008)](https://eric.ed.gov/?id=EJ815119) | 分级读物实验：每本约 5,500 词、28 个目标生词，出现 10–13 / 7–9 / 2–3 次三组；**次数越多习得越好** |

**综合**：12 次是「有较好机会习得」的经验阈值，
但 Webb (2007) 提醒**没有任何次数能保证习得** —— 所以不能把它当硬保证。

> **对项目的含义**：320 词的一篇文章**根本放不下 12 次遇见**。
> 所以 12 次必须**跨文章累积**，由 `word_encounters` 承担。
> 一篇文章只负责建立第一次印象，**系统负责把它推到 12 次** —— 这是产品设计的根本约束。

---

## 五、复现必须「换语境」

| 来源 | 结论 |
|---|---|
| Hulstijn (2001)；Webb & Nation (2017)；Kang (2016)；Shin et al. (2019)（见 [综述](https://www.researchgate.net/publication/290511665)） | 词汇是**逐步**发展的：学习者需在**重复遇见、有意义的任务、不同学习语境**中接触词汇 |
| [The Effect of Narrow Reading on L2 Learners' Vocabulary Acquisition](https://journals.sagepub.com/doi/abs/10.1177/0033688219871387) | 同一作者或随机文本比同标题文本带来更多词汇习得 —— **语境多样性有效** |
| [Context and repetition in word learning](https://pmc.ncbi.nlm.nih.gov/articles/PMC3619249/) | 重复 + 语境变化共同促进词义学习 |

**对项目的含义**：重遇不能只是「同一个词再出现一次」，
而要**换题材、换句型、换搭配**。系统的「题材轮转」正是为这条服务。

---

## 六、提取练习与间隔重复：为什么要有测试和 FSRS

| 来源 | 结论 |
|---|---|
| [Roediger & Karpicke (2006)](https://pubmed.ncbi.nlm.nih.gov/16507066/)，*Psychological Science* | 提取练习组一周后**只遗忘 13%**，重读组遗忘显著更多 |
| [Karpicke & Roediger (2008)](https://doi.org/10.1126/science.1152408)，*Science* 319(5865): 966–968 | 《The critical importance of retrieval for learning》 |
| [Cepeda et al. (2006)](https://pubmed.ncbi.nlm.nih.gov/17073523/)，*Psychological Bulletin* | **254 项研究 / 14,000+ 观测**：分散练习显著优于集中练习 |
| Karpicke & Blunt (2011) | 提取练习优于精加工（概念图），即使终测要求画概念图 |
| [Smith & Roediger (2013)](https://doi.org/10.1037/a0030833) | 间隔效应双过程解释：增强编码强度与提取强度 |

**对项目的含义**：
- **单词测试不是附属功能，是习得机制本身** —— 没有提取练习，光读的效率显著更低
- **FSRS 间隔调度有实证基础**，不是自创
- 这也解释了为什么「读后理解题」和「单词测试」应当做，而不是可选

---

## 七、理论与争议（诚实说明）

### 理论根基

- **Krashen (1982)** 输入假说：语言通过理解「略高于当前水平（i+1）」的输入而习得 ——
  [The Case for Comprehensible Input](https://www.sdkrashen.com/content/articles/case_for_comprehensible_input.pdf)
- 本项目采用的 95–98% 覆盖率，就是 i+1 的可操作定义

### 争议与边界（不能只讲有利证据）

1. **Krashen 的假说本身受到批评** ——
   [Beyond comprehensible input: a neuro-ecological critique](https://pmc.ncbi.nlm.nih.gov/articles/PMC12577063/)：
   该假说长期主导 SLA 理论，但「仅靠理解输入即可习得」的强版本受到神经生态学视角的质疑。
   **输入是必要条件，未必是充分条件。**

2. **98% 阈值并不牢固** ——
   Hu & Nation (2000) 的结论建立在 **66 名大学生**的回归分析上，且样本来自
   WEIRD（西方、受过教育、工业化、富裕、民主）背景。
   复制研究（Kremmel et al.）发现 90% 与 98% 的理解度差异不大。

3. **Webb (2007)：没有保证习得的最少遇见次数** ——
   所以「12 次」是经验参考，不是定理。

4. **AI 生成文本的可读性风险** ——
   [Pangram](https://www.pangram.com/blog/why-perplexity-and-burstiness-fail-to-detect-ai) 指出，
   基于困惑度/突发性的「AI 检测」**对非母语写作者误判严重**。
   本项目因此**不引入 AI 检测器**，只做可测量的风格指标（见生成方案第五节）。

### 证据的总体强度

| 命题 | 强度 |
|---|---|
| 阅读能习得词汇 | **强**（多个元分析一致） |
| 阅读能提升阅读能力 | **强** |
| 阅读能提升语法/写作/口语 | **中**（效应量小到中等，2025 元分析支持但异质性大） |
| 「定向 + 问责」放大效果 | **中偏强**（2025 元分析的显著调节变量） |
| 覆盖率 95% 配支持即可 | **中**（Laufer & Ravenhorst-Kalovski 2010；有争议） |
| 12 次遇见 | **中**（经验阈值，非保证） |
| 提取练习与间隔重复 | **强**（数十年、上千项研究） |

---

## 八、设计决策 → 文献依据 对照表

这是本文档最有用的部分：**每一项设计，指向哪条证据**。

| 设计决策 | 依据 | 强度 |
|---|---|---|
| 用生成文章做定向输入，而非自由选材 | 2025 元分析：文本选择受限 → 效应更大 | 中偏强 |
| 掌握度追踪 + 单词测试（问责机制） | 同上：有问责 → 效应更大 | 中偏强 |
| 目标覆盖率 **95% ± 2** | Laufer & Ravenhorst-Kalovski (2010)：95% 配支持即可 | 中 |
| 提供即时查词/长难句解析（支持） | 同上：95% 的前提是「有支持」 | 中 |
| 一篇埋 3–5 个生词 | Brown et al. (2008) 反推：5,500 词 28 个词 ≈ 9.8 次/词 | 中 |
| 12 次遇见驱动选词 | Pellicer-Sánchez (2015)、graded readers 研究 | 中 |
| 重遇必须换语境 | Hulstijn (2001)、Webb & Nation (2017)、窄读研究 | 中 |
| 单词测试（提取练习） | Roediger & Karpicke (2006)、Karpicke & Roediger (2008) | **强** |
| FSRS 间隔调度 | Cepeda et al. (2006)，254 项研究 | **强** |
| 生词密度均匀、避免扎堆 | Hu & Nation (2000)：未知词密度直接影响理解 | 中 |
| 不做 AI 检测器，只做风格指标 | Pangram：检测器对非母语者误判严重 | 中 |

---

## 九、还缺什么证据（诚实列出）

1. **中文母语者、备考四六级** 场景下的泛读实证研究**很少**。
   现有元分析以英语母语环境和其他外语环境为主。
   → 这是本项目**可以自己产生证据**的地方：积累真实使用数据，验证提效。

2. **AI 生成分级文本 vs 人工分级读物** 的效果对比，目前没有文献。
   这是一块空白，也意味着风险 —— 所以生成质量必须靠**可测量的指标**兜住，
   不能用「看起来还行」来判断。

3. **长难句解析对阅读能力的增益**，缺直接证据。

**建议**：本项目应当内置**效果测量**（学习速率、词汇增长曲线、测试正确率），
在积累 2–3 个月数据后，自己产出这份缺失的证据。
文档 [`docs/MASTERY-MODEL.md`](MASTERY-MODEL.md) 第 5 节已列出三个校准指标。
