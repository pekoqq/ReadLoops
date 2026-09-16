"""词汇差距：把「离四级/六级还差多少词」变成可计算的数字。

## 第一性原理

    「词汇量」必须有分母才可测 —— 而四六级/考研/雅思恰好是边界明确的考试
    可理解输入（i+1，95–98% 覆盖率）是手段 —— 而覆盖率只有知道「你的边界」才算得出

所以这个模块只做两件事：

    ① 覆盖率：给定你的分档掌握率，**你在某份语料上能读懂百分之多少**
    ② 缺口：给定一份考试词表，**你还有多少词不认识**

## 为什么覆盖率要按词频档位加权，而不是「认识多少个词表里的词」

实测：**CET4 大纲词表（3,849 词）只覆盖四级真题语料 15.8% 的 token。**
因为词表装的是内容词/考点词，剩下 84% 全是基础词与功能词（the / of / is …）。
拿「词表掌握百分比」当「能读懂多少」的代理会严重失真。

正确的做法是把语料的每个 token 映射到它所在词频档位的掌握率再求和 ——
这样「认识多少」直接换算成「读懂多少」，也就是 i+1 判据本身。

## 缺口的算法

缺口不设「掌握率 < 0.5 就算不认识」这种硬阈值，而是取期望值：

    缺口(考试) = Σ_{w ∈ 词表} (1 − 掌握率(w))

这样一份「掌握了 70%」的词表给出的缺口是连续的，而不是被阈值一刀切成整数。
"""
from __future__ import annotations

import json
import time
from collections import Counter
from typing import Optional

from app.config import CORPUS_DIR
from app.database import get_db
from app.services import lexicon, placement

# 考试词表的显示名与顺序（按难度递进）
EXAMS: list[tuple[str, str]] = [
    ("cet4", "四级"),
    ("cet6", "六级"),
    ("ky", "考研"),
    ("ielts", "雅思"),
    ("toefl", "托福"),
]

# 语料文件（可由 tools/crawl_exam_corpus.py 重建）
CORPUS_FILE = CORPUS_DIR / "真题阅读纯文本" / "exam_corpus.json"

# i+1 区间（可理解输入的门槛）
I1_LOW, I1_HIGH = 0.95, 0.98

_cache: dict = {}


# ---------------------------------------------------------------- 掌握率

def band_rates() -> list[dict]:
    """当前生效的分档掌握率（来自最近一次定级；没有则用默认假设）。"""
    return placement.latest_bands()


def rate_of_frq(frq: Optional[int], bands: list[dict]) -> float:
    """某个词频对应的掌握率。无词频（生僻词/专名）按 0 处理。

    传入的应当是「有效词频」= COALESCE(frq, bnc) —— ECDICT 里有一批常见词
    的 COCA 排名为 0 但 BNC 有排名，只看 COCA 会把它们当生僻词。
    """
    if not frq:
        return 0.0
    for b in bands:
        if b["band_lo"] <= frq <= b["band_hi"]:
            return b["rate"]
    return 0.0


# ---------------------------------------------------------------- 覆盖率

def _corpus_band_profile(level: str) -> dict:
    """把某级别真题语料压成「每个词频档位有多少 token」。

    只算一次并缓存 —— 547 篇 / 30 万 token 的词形还原要跑几秒，
    而分档掌握率一变就要重算覆盖率，不能每次都重做分词。
    """
    key = f"profile:{level}"
    if key in _cache:
        return _cache[key]

    if not CORPUS_FILE.exists():
        return {"available": False}

    rows = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))
    if level != "all":
        rows = [r for r in rows if r.get("level") == level.upper()]

    counts: Counter = Counter()      # band_lo -> token 数
    unresolved = 0
    total = 0

    for r in rows:
        for token in lexicon.tokenize(r.get("text", "")):
            total += 1
            lemma = lexicon.normalize(token)
            if lemma is None:
                unresolved += 1
                continue
            counts[lemma] = counts.get(lemma, 0) + 1

    # 把词形计数搬到档位上。
    # ⚠️ 必须批量查：这里有一万多个不同词元，逐个 SELECT 要跑 150 秒
    # （实测），分批 IN 查询不到 1 秒。覆盖率一变就要重算，这个代价付不起。
    band_tokens: Counter = Counter()
    lemmas = list(counts)
    frq_of: dict[str, int] = {}
    with get_db() as db:
        for i in range(0, len(lemmas), 800):
            chunk = lemmas[i:i + 800]
            ph = ",".join("?" * len(chunk))
            for r in db.execute(
                f"""SELECT lower(text) t,
                           COALESCE(NULLIF(frq, 0), bnc, 0) AS rank
                    FROM words WHERE lower(text) IN ({ph})""", tuple(chunk)
            ):
                frq_of[r["t"]] = r["rank"] or 0

    band_of = {lo: lo for lo, _ in placement.BANDS}
    for lemma, c in counts.items():
        frq = frq_of.get(lemma, 0)
        for lo, hi in placement.BANDS:
            if frq and lo <= frq <= hi:
                band_tokens[band_of[lo]] += c
                break
        else:
            unresolved += c

    out = {"available": True, "total": total, "band_tokens": dict(band_tokens),
           "unresolved": unresolved, "passages": len(rows)}
    _cache[key] = out
    return out


def coverage(level: str = "CET4", bands: Optional[list[dict]] = None) -> dict:
    """你在某级别真题语料上的词汇覆盖率。

    「覆盖率」= 随机取一个 token，你认识它的概率。这正是 i+1 的判据：
    95–98% 表示一篇 300 词的文章里有 6–15 个生词。
    """
    bands = bands if bands is not None else band_rates()
    prof = _corpus_band_profile(level)
    if not prof.get("available"):
        return {"available": False, "reason": f"找不到语料文件：{CORPUS_FILE}"}

    rate_by_lo = {b["band_lo"]: b["rate"] for b in bands}
    known = sum(c * rate_by_lo.get(lo, 0.0) for lo, c in prof["band_tokens"].items())
    total = prof["total"] or 1
    cov = known / total

    return {
        "available": True,
        "level": level,
        "coverage": round(cov, 4),
        "passages": prof["passages"],
        "tokens": prof["total"],
        "unknown_per_300": round((1 - cov) * 300, 1),
        "in_i1": I1_LOW <= cov <= I1_HIGH,
        "i1_low": I1_LOW,
        "i1_high": I1_HIGH,
        "unresolved_tokens": prof["unresolved"],
    }


def coverage_all(bands: Optional[list[dict]] = None) -> dict:
    bands = bands if bands is not None else band_rates()
    return {lv: coverage(lv, bands) for lv in ("CET4", "CET6")}


# ---------------------------------------------------------------- 考试缺口

def gap_by_exam(bands: Optional[list[dict]] = None) -> list[dict]:
    """每场考试还差多少词（期望值，不设硬阈值）。"""
    bands = bands if bands is not None else band_rates()
    rate_by_lo = {b["band_lo"]: b["rate"] for b in bands}

    out = []
    with get_db() as db:
        rows = db.execute(
            """SELECT tags, COALESCE(NULLIF(frq, 0), bnc, 0) AS rank FROM words
               WHERE tags IS NOT NULL AND tags != ''
                 AND meaning IS NOT NULL AND meaning != ''"""
        ).fetchall()

    by_exam: dict[str, list[float]] = {k: [] for k, _ in EXAMS}
    for r in rows:
        tags = set((r["tags"] or "").split())
        rate = 0.0
        frq = r["rank"] or 0
        for lo, hi in placement.BANDS:
            if frq and lo <= frq <= hi:
                rate = rate_by_lo.get(lo, 0.0)
                break
        for k, _ in EXAMS:
            if k in tags:
                by_exam[k].append(rate)

    for k, name in EXAMS:
        rates = by_exam[k]
        size = len(rates)
        if not size:
            continue
        known = sum(rates)
        gap = size - known
        out.append({
            "key": k,
            "name": name,
            "size": size,
            "known": int(round(known)),
            "gap": int(round(gap)),
            "progress": round(known / size, 4),
        })
    return out


# ---------------------------------------------------------------- 进度预测

def learning_rate(days: int = 30) -> dict:
    """近 N 天每天新掌握多少词。

    口径是「被标记为已掌握（mastered=1）且在该窗口内更新过」的词数。
    刻意不用「学过多少词」—— 学过不等于记住。
    """
    since = int(time.time()) - days * 86400
    with get_db() as db:
        n = db.execute(
            "SELECT COUNT(*) FROM words WHERE mastered = 1 AND updated_at >= ?",
            (since,),
        ).fetchone()[0]
    return {
        "days": days,
        "learned": n,
        "per_day": round(n / days, 2),
        "enough_data": n >= 10,   # 样本太少时的估计没有意义，前端要如实说明
    }


def eta(gap_words: int, per_day: float) -> Optional[str]:
    """按当前速度，多久补上这个缺口。"""
    if per_day <= 0 or gap_words <= 0:
        return None
    days = gap_words / per_day
    if days > 3650:
        return None
    return time.strftime("%Y-%m-%d", time.localtime(time.time() + days * 86400))


# ---------------------------------------------------------------- 汇总

def overview() -> dict:
    """统计页要用的全部词汇差距数据。"""
    from app.services import placement as pl

    run = pl.summary()
    bands = run.get("bands") or []
    is_default = run.get("source") == "default" if run.get("has_result") else True

    rate = learning_rate()
    exams = gap_by_exam(bands)
    cov = coverage_all(bands)

    for e in exams:
        e["eta"] = eta(e["gap"], rate["per_day"]) if rate["enough_data"] else None

    return {
        "has_placement": bool(run.get("has_result")) and not is_default,
        "using_default": is_default,
        "vocab_estimate": run.get("vocab_estimate", 0),
        "is_lower_bound": bool(run.get("is_lower_bound")),
        "false_alarm": run.get("false_alarm", 0),
        "coverage": cov,
        "exams": exams,
        "rate": rate,
        "i1": {"low": I1_LOW, "high": I1_HIGH},
    }


def invalidate() -> None:
    """词库或分档掌握率变化后清缓存。"""
    _cache.clear()
    lexicon.invalidate()
