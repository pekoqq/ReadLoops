"""蒸馏：从材料中提取「风格画像」。

产出的是**统计特征**（句长分布、词汇难度分布、句法复杂度……），
不是原文摘录。这类统计量属于事实性数据，用于让文章生成贴近目标文体。

画像可以增量更新：材料越多，统计越稳。
"""
from __future__ import annotations

import json
import re
import statistics
import time
from typing import Optional

from app.database import get_db
from app.services.materials import count_words, split_sentences

# 从句 / 复合结构标记词（用于估算句子复杂度）
_SUBORDINATORS = {
    "although", "though", "because", "since", "unless", "until", "while",
    "whereas", "whether", "if", "when", "whenever", "where", "wherever",
    "after", "before", "as", "once", "that", "which", "who", "whom", "whose",
    "what", "why", "how", "even though", "as if", "so that", "in order that",
}
# 并列 / 过渡连接词
_CONJUNCTS = {
    "however", "moreover", "furthermore", "therefore", "thus", "consequently",
    "nevertheless", "nonetheless", "meanwhile", "besides", "accordingly",
    "in addition", "for instance", "for example", "on the other hand",
    "as a result", "in contrast", "in conclusion",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _sentence_stats(sentences: list[str]) -> dict:
    lengths = [count_words(s) for s in sentences]
    lengths = [n for n in lengths if n > 0]
    if not lengths:
        return {}
    lengths_sorted = sorted(lengths)

    def pct(p: float) -> float:
        idx = min(len(lengths_sorted) - 1, max(0, int(round(p * (len(lengths_sorted) - 1)))))
        return float(lengths_sorted[idx])

    return {
        "sentence_count": len(lengths),
        "avg_len": round(statistics.mean(lengths), 2),
        "median_len": float(statistics.median(lengths)),
        "stdev_len": round(statistics.pstdev(lengths), 2) if len(lengths) > 1 else 0.0,
        "p10_len": pct(0.10),
        "p90_len": pct(0.90),
        "max_len": float(max(lengths)),
        "long_ratio": round(sum(1 for n in lengths if n > 25) / len(lengths), 4),
        "short_ratio": round(sum(1 for n in lengths if n < 8) / len(lengths), 4),
    }


def _complexity_stats(sentences: list[str]) -> dict:
    """估算从句/复合句占比与连接词密度。"""
    if not sentences:
        return {}
    compound = 0
    subord_total = 0
    conjunct_total = 0
    for s in sentences:
        low = " " + s.lower() + " "
        has_sub = any(f" {w} " in low for w in _SUBORDINATORS)
        has_conj = any(f" {w} " in low for w in _CONJUNCTS)
        if has_sub or has_conj or s.count(",") >= 2:
            compound += 1
        subord_total += sum(low.count(f" {w} ") for w in _SUBORDINATORS)
        conjunct_total += sum(low.count(f" {w} ") for w in _CONJUNCTS)
    n = len(sentences)
    return {
        "compound_ratio": round(compound / n, 4),
        "subordinator_per_sentence": round(subord_total / n, 3),
        "conjunct_per_sentence": round(conjunct_total / n, 3),
    }


def _vocab_stats(text: str) -> dict:
    """词汇分布：TTR、平均词长、以及按词频分档的比例（用本地词库的 frequency）。"""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return {}
    types = set(words)
    stats = {
        "token_count": len(words),
        "type_count": len(types),
        "ttr": round(len(types) / len(words), 4),
        "avg_word_len": round(sum(len(w) for w in words) / len(words), 2),
    }

    # 用本地词库的频率分档（高频 / 中频 / 低频 / 未收录）
    try:
        sample = list(types)[:4000]
        placeholders = ",".join("?" * len(sample))
        with get_db() as db:
            rows = db.execute(
                f"SELECT lemma, frequency, level FROM words WHERE lemma IN ({placeholders})",
                sample,
            ).fetchall()
        freq_map = {r["lemma"].lower(): (r["frequency"] or 0) for r in rows}
        level_map = {r["lemma"].lower(): (r["level"] or "") for r in rows}
        known = [w for w in words if w in freq_map]
        if known:
            hi = sum(1 for w in known if freq_map[w] >= 10000)
            mid = sum(1 for w in known if 1000 <= freq_map[w] < 10000)
            low = sum(1 for w in known if freq_map[w] < 1000)
            k = len(known)
            stats.update({
                "known_coverage": round(k / len(words), 4),
                "high_freq_ratio": round(hi / k, 4),
                "mid_freq_ratio": round(mid / k, 4),
                "low_freq_ratio": round(low / k, 4),
            })
        cet4 = sum(1 for w in types if level_map.get(w) == "CET4")
        cet6 = sum(1 for w in types if level_map.get(w) == "CET6")
        stats.update({
            "cet4_type_ratio": round(cet4 / len(types), 4),
            "cet6_type_ratio": round(cet6 / len(types), 4),
        })
    except Exception:
        pass  # 词库缺失时不影响其它统计

    return stats


def distill_text(text: str) -> dict:
    """对一段文本做完整蒸馏，返回画像参数。"""
    sentences = split_sentences(text)
    params: dict = {}
    params.update(_sentence_stats(sentences))
    params.update(_complexity_stats(sentences))
    params.update(_vocab_stats(text))
    return params


def distill_materials(material_ids: list[int], name: str = "",
                      activate: bool = False) -> dict:
    """对指定材料做蒸馏并保存画像。

    多份材料会合并统计（而不是分别统计再平均），这样分布更真实。
    """
    if not material_ids:
        return {"ok": False, "error": "未选择材料"}

    placeholders = ",".join("?" * len(material_ids))
    with get_db() as db:
        rows = db.execute(
            f"SELECT id, title, content FROM materials "
            f"WHERE id IN ({placeholders}) AND parse_status='parsed'",
            material_ids,
        ).fetchall()

    if not rows:
        return {"ok": False, "error": "所选材料没有可用的解析结果"}

    merged = "\n\n".join((r["content"] or "") for r in rows)
    params = distill_text(merged)
    if not params:
        return {"ok": False, "error": "材料内容为空，无法蒸馏"}

    if not name:
        if len(rows) == 1:
            name = rows[0]["title"]
        else:
            name = f"{rows[0]['title']} 等 {len(rows)} 份材料"

    now = int(time.time())
    with get_db() as db:
        if activate:
            db.execute("UPDATE style_profiles SET is_active=0")
        cur = db.execute(
            """INSERT INTO style_profiles (name, material_ids, params, sample_count,
                                           is_active, created_at)
               VALUES (?,?,?,?,?,?)""",
            (name, json.dumps([r["id"] for r in rows]), json.dumps(params, ensure_ascii=False),
             params.get("sentence_count", 0), 1 if activate else 0, now),
        )
        profile_id = cur.lastrowid

    return {"ok": True, "profile_id": profile_id, "name": name, "params": params}


def list_profiles() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT id, name, material_ids, params, sample_count, is_active, created_at "
            "FROM style_profiles ORDER BY created_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["params"] = json.loads(d["params"] or "{}")
            except json.JSONDecodeError:
                d["params"] = {}
            out.append(d)
        return out


def get_active_profile() -> Optional[dict]:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM style_profiles WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["params"] = json.loads(d["params"] or "{}")
        except json.JSONDecodeError:
            d["params"] = {}
        return d


def activate_profile(profile_id: int) -> bool:
    with get_db() as db:
        row = db.execute("SELECT id FROM style_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            return False
        db.execute("UPDATE style_profiles SET is_active=0")
        db.execute("UPDATE style_profiles SET is_active=1 WHERE id=?", (profile_id,))
    return True


def delete_profile(profile_id: int) -> bool:
    with get_db() as db:
        cur = db.execute("DELETE FROM style_profiles WHERE id=?", (profile_id,))
        return cur.rowcount > 0


def profile_prompt_hint() -> str:
    """把生效画像转成给 AI 的风格提示片段。

    只描述「写出来应该是什么样」，不包含任何原文。
    """
    p = get_active_profile()
    if not p:
        return ""
    s = p.get("params", {})
    if not s:
        return ""
    bits = []
    if s.get("avg_len"):
        bits.append(f"平均句长约 {s['avg_len']:.0f} 词（标准差 {s.get('stdev_len', 0):.0f}）")
    if s.get("long_ratio") is not None:
        bits.append(f"超过 25 词的长句约占 {s['long_ratio'] * 100:.0f}%")
    if s.get("compound_ratio") is not None:
        bits.append(f"含从句或连接词的复合句约占 {s['compound_ratio'] * 100:.0f}%")
    if s.get("ttr"):
        bits.append(f"词汇丰富度（类符/形符比）约 {s['ttr']:.2f}")
    if s.get("low_freq_ratio") is not None:
        bits.append(f"低频词占比约 {s['low_freq_ratio'] * 100:.0f}%")
    if not bits:
        return ""
    return "【文体参考】" + "；".join(bits) + "。"
