"""词汇量定级测试（Yes/No 词汇量测试）。

为什么需要它：这个产品要算的一切 —— 覆盖率、考试缺口、i+1 选词 —— 都建立在
「用户已经掌握哪些词」之上。没有这个初始集合，新用户的统计页永远是
「2000 + 学过的几个词」，反馈循环根本启动不了。

方法学（Meara & Buxton 1987 的 Yes/No 测试，也是 TestYourVocab 的做法）：

1. 按 **COCA 词频排名分档**抽样，每档若干词
2. 混入约 10% 的**伪词**（形似真词、实际不存在）测量「虚报率」
3. 逐档校正：`真实认识率 = 声称认识率 − 虚报率`
4. 词汇量 = Σ 每档校正认识率 × 该档词数

第 2 步是关键：没有伪词校正，自评式测试会系统性高估（人会"觉得眼熟"就选认识）。

只呈现单词本身、不呈现释义 —— 这是**识别**测试，给释义就变成考翻译了。
"""
from __future__ import annotations

import random
import time
from typing import Optional

from app.database import get_db

# COCA 词频档位：**等宽 1,000**，1–20,000 共 20 档。
#
# ⚠️ 这里踩过一个代价很大的坑：最初用的是不等宽档位（末尾 14001–20000 一档
# 就有 6,000 词），每档只抽 15 题。于是「5/15 样本恰好蒙对」这种噪声会被
# 档位宽度放大 —— 0.33 × 6,000 = **2,000 个凭空的词**，估计值系统性高出
# 真实值 2,700 词。等宽分档让每档贡献的噪声量级一致，总误差随档数开方增长
# 而不是随最大档宽线性增长。
BANDS: list[tuple[int, int]] = [(i * 1000 + 1, (i + 1) * 1000) for i in range(20)]

ITEMS_PER_BAND = 8
PSEUDO_COUNT = 34

# 伪词：由常见词缀拼装、并**逐个校验过不在 ECDICT 77 万词条中**（见 tests）。
# 它们只用于测虚报率，绝不能是真实存在的词。
PSEUDO_WORDS: list[str] = [
    "klimile", "clovment", "treltion", "dwarine",
    "bantid", "wrandism", "trelsome", "kelmate",
    "slorsome", "glintward", "quorible", "quorance",
    "trandish", "fermid", "plendism", "driner",
    "drovance", "dwarible", "narpile", "trandal",
    "cralful", "clandic", "clandsome", "flomal",
    "narpist", "noltful", "yornory", "sloring",
    "blenid", "slorist", "mispid", "gormence",
    "lumbrory", "narpment", "quorine", "rendward",
    "plendish", "fennish", "trandsome", "grastal",
]

# 抽题只取「有释义、有词频、纯小写单词」的词 —— 库里还有 7 万个
# 无释义的生僻词（bloubiskop 之类），它们不能当题目。
_ITEM_SQL = """
    SELECT id, text, COALESCE(NULLIF(frq, 0), bnc, 0) AS rank FROM words
    WHERE COALESCE(NULLIF(frq, 0), bnc, 0) BETWEEN ? AND ?
      AND meaning IS NOT NULL AND meaning != ''
      AND text GLOB '[a-z]*' AND text NOT LIKE '% %'
      AND length(text) BETWEEN 3 AND 16
    ORDER BY rank
"""


def _band_size(band: tuple[int, int]) -> int:
    lo, hi = band
    return hi - lo + 1


def build_items(items_per_band: int = ITEMS_PER_BAND,
                pseudo_count: int = PSEUDO_COUNT,
                seed: Optional[int] = None) -> dict:
    """生成一份完整的测试卷（无状态：客户端持有题目，最后一次性提交）。

    ⚠️ 下发的题目**只含单词本身**：不带词频档位、不带「是否为伪词」标记。
    否则用户翻一眼网络面板就知道哪些是假词，虚报率校正会失效；
    档位也会暗示「这词很生僻」，干扰判断。评分时服务端自己按文本重新判定。
    """
    rng = random.Random(seed)
    items: list[dict] = []

    with get_db() as db:
        for lo, hi in BANDS:
            rows = db.execute(_ITEM_SQL, (lo, hi)).fetchall()
            if not rows:
                continue
            picked = rng.sample(rows, min(items_per_band, len(rows)))
            for r in picked:
                items.append({"text": r["text"]})

    for w in rng.sample(PSEUDO_WORDS, min(pseudo_count, len(PSEUDO_WORDS))):
        items.append({"text": w})

    rng.shuffle(items)
    return {
        "items": items,
        "bands": [{"lo": lo, "hi": hi, "size": _band_size((lo, hi))} for lo, hi in BANDS],
    }


def _pava(values: list[float]) -> list[float]:
    """保序回归（Pool Adjacent Violators），约束结果单调不增。

    词汇量认识率随词频档位必然下降；实测数据的噪声会让它上下跳动。
    PAVA 给出该约束下的最小二乘拟合，等价于把违反单调的相邻档合并取均值。
    """
    blocks = [[v, 1] for v in values]          # [加权和, 权重]
    i = 1
    while i < len(blocks):                     # 注意：不能用 range(len(blocks))，
        left, right = blocks[i - 1], blocks[i]  # 合并会让列表变短，range 的界不会更新
        if left[0] / left[1] < right[0] / right[1]:
            blocks[i - 1] = [left[0] + right[0], left[1] + right[1]]
            blocks.pop(i)
            if i > 1:
                i -= 1
        else:
            i += 1
    out = []
    for s0, w0 in blocks:
        out.extend([s0 / w0] * w0)
    return out


def score(answers: list[dict]) -> dict:
    """根据作答计算分档掌握率与词汇量估计。

    answers: [{text, known(bool)}]，`is_pseudo` 由服务端**重新判定**，
    不信任客户端传来的标记。
    """
    pseudo_set = set(PSEUDO_WORDS)
    per_band: dict[tuple[int, int], dict] = {
        b: {"sampled": 0, "known": 0} for b in BANDS
    }
    pseudo_sampled = pseudo_known = 0

    with get_db() as db:
        for a in answers:
            text = str(a.get("text", "")).strip().lower()
            known = bool(a.get("known"))
            if not text:
                continue
            if text in pseudo_set:
                pseudo_sampled += 1
                pseudo_known += int(known)
                continue
            row = db.execute(
                """SELECT COALESCE(NULLIF(frq, 0), bnc, 0) AS rank
                   FROM words WHERE lower(text) = ? LIMIT 1""", (text,)
            ).fetchone()
            if not row or not row["rank"]:
                continue
            frq = row["rank"]
            for lo, hi in BANDS:
                if lo <= frq <= hi:
                    per_band[(lo, hi)]["sampled"] += 1
                    per_band[(lo, hi)]["known"] += int(known)
                    break

    # 虚报率是**从每一档里都要减掉**的量，所以它的估计误差会被放大 20 倍。
    # 34 个伪词在真实虚报率 5% 时只期望 1.7 个「是」，一次 5/24 的波动就会
    # 被当成 20% 从全线扣掉，导致整体低估 30%。这里加一个弱收缩先验
    # （等价于先验上 8 个伪词里有 0.5 个被认成认识），把极端值拉回合理区间。
    false_alarm = ((pseudo_known + 0.5) / (pseudo_sampled + 8)) if pseudo_sampled else 0.0

    rows = []
    for lo, hi in BANDS:
        st = per_band[(lo, hi)]
        if not st["sampled"]:
            continue
        claimed = st["known"] / st["sampled"]
        rows.append({
            "lo": lo, "hi": hi, "size": _band_size((lo, hi)),
            "sampled": st["sampled"], "known": st["known"], "claimed": claimed,
        })

    # 单调化：词汇量随词频必然单调不增。若不约束，高尾档的抽样噪声会凭空
    # 造出词汇量（某档 5/8 蒙对 → 12.5% 的错误率 × 1,000 词）。用 PAVA
    # 做等权保序回归，把噪声摊平到相邻档。
    fitted = _pava([r["claimed"] for r in rows])

    bands_out, vocab = [], 0.0
    for r, f in zip(rows, fitted):
        rate = max(0.0, min(1.0, f - false_alarm))
        vocab += rate * r["size"]
        bands_out.append({
            "lo": r["lo"], "hi": r["hi"], "size": r["size"],
            "sampled": r["sampled"], "known": r["known"],
            "claimed": round(r["claimed"], 4), "rate": round(rate, 4),
        })

    vocab_est = int(round(vocab))
    # 最高档都掌握得很好 → 真实词汇量已超出本测试的范围，只能给下界
    top_open = bool(bands_out) and bands_out[-1]["hi"] == BANDS[-1][1] and bands_out[-1]["rate"] >= 0.8

    return {
        "vocab_estimate": vocab_est,
        "is_lower_bound": top_open,
        "false_alarm": round(false_alarm, 4),
        "pseudo_sampled": pseudo_sampled,
        "pseudo_known": pseudo_known,
        "bands": bands_out,
        "answered": sum(b["sampled"] for b in bands_out) + pseudo_sampled,
    }


def save_run(result: dict, answers: list[dict]) -> int:
    """落库：测试结果 + 逐档掌握率 + 被明确测过的词的掌握标记。"""
    now = int(time.time())
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO placement_runs
                   (vocab_estimate, is_lower_bound, false_alarm, answered, created_at)
               VALUES (?,?,?,?,?)""",
            (result["vocab_estimate"], int(result["is_lower_bound"]),
             result["false_alarm"], result["answered"], now),
        )
        run_id = cur.lastrowid
        db.executemany(
            """INSERT INTO placement_bands
                   (run_id, band_lo, band_hi, band_size, sampled, known, rate)
               VALUES (?,?,?,?,?,?,?)""",
            [(run_id, b["lo"], b["hi"], b["size"], b["sampled"], b["known"], b["rate"])
             for b in result["bands"]],
        )
        _mark_mastery(db, answers, now)
    return run_id


def _mark_mastery(db, answers: list[dict], now: int) -> None:
    """把被明确测过的词标成 掌握(1) / 未掌握(2)。未测到的词保持 0（由档位推断）。"""
    pseudo_set = set(PSEUDO_WORDS)
    for a in answers:
        text = str(a.get("text", "")).strip().lower()
        if not text or text in pseudo_set:
            continue
        db.execute(
            "UPDATE words SET mastered=?, updated_at=? WHERE lower(text)=? AND mastered=0",
            (1 if a.get("known") else 2, now, text),
        )


def latest_bands() -> list[dict]:
    """最近一次定级测试的分档掌握率（供覆盖率与缺口计算使用）。"""
    with get_db() as db:
        row = db.execute(
            "SELECT id, vocab_estimate, is_lower_bound, false_alarm, source, created_at "
            "FROM placement_runs ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        bands = db.execute(
            "SELECT band_lo, band_hi, band_size, sampled, known, rate "
            "FROM placement_bands WHERE run_id=? ORDER BY band_lo", (row["id"],)
        ).fetchall()
    return [dict(b) for b in bands]


def rate_for_frq(frq: int, bands: Optional[list[dict]] = None) -> Optional[float]:
    """给定一个词的 COCA 排名，返回它所在档位的掌握率（无数据时 None）。"""
    if not frq:
        return None
    bands = bands if bands is not None else latest_bands()
    for b in bands:
        if b["band_lo"] <= frq <= b["band_hi"]:
            return b["rate"]
    return None


DEFAULT_VOCAB = 2500


def ensure_default_run(vocab: int = DEFAULT_VOCAB) -> int:
    """没有定级结果时，铺一条「默认假设」的分档掌握率。

    为什么需要：覆盖率、考试缺口、i+1 选词都要一个起点。没有它，
    新用户的统计页只能显示「2000 + 学过的几个词」这种假数字。
    这里按「掌握 COCA 最高频的前 N 词」铺一条单调的阶梯，
    并标记 source='default' —— 前端要如实告诉用户这是假设不是测量。

    真实定级测试完成后，这条会被覆盖（latest_bands 取最新一条）。
    """
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM placement_runs ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row:
            return row["id"]

        now = int(time.time())
        cur = db.execute(
            """INSERT INTO placement_runs
                   (vocab_estimate, is_lower_bound, false_alarm, answered, source, created_at)
               VALUES (?,0,0,0,'default',?)""",
            (vocab, now),
        )
        run_id = cur.lastrowid

        rows = []
        remaining = float(vocab)
        for lo, hi in BANDS:
            size = hi - lo + 1
            rate = max(0.0, min(1.0, remaining / size))
            remaining -= rate * size
            rows.append((run_id, lo, hi, size, 0, 0, round(rate, 4)))
        db.executemany(
            """INSERT INTO placement_bands
                   (run_id, band_lo, band_hi, band_size, sampled, known, rate)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )
    return run_id


def summary() -> dict:
    """最近一次测试的概览（给前端展示）。"""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM placement_runs ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"has_result": False}
    d = dict(row)
    d["has_result"] = True
    d["bands"] = latest_bands()
    return d
