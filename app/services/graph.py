"""知识图谱：把词、短语、文章、材料连成一张网。

同一份数据服务两个用途：
- **给用户看**：前端渲染成力导向图，直观看到自己的知识积累
- **给 AI 用**：邻接关系作为结构化检索线索（比纯向量检索更可控）

节点规模有上限（默认 600），避免图太密既看不清也拖慢渲染。
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from typing import Optional

from app.database import get_db

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "as", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "am", "do", "does", "did", "have", "has", "had", "it", "its",
    "he", "she", "they", "them", "his", "her", "their", "you", "your", "i",
    "we", "our", "us", "me", "my", "this", "that", "these", "those", "not",
    "no", "so", "than", "then", "there", "here", "when", "where", "which",
    "who", "whom", "what", "how", "all", "any", "some", "such", "very", "can",
    "could", "will", "would", "shall", "should", "may", "might", "must",
}

MAX_NODES = 600

# ------------------------------------------------------------------ 清洗
# 词节点只接受「单个英文词」形态：小写、无空格、无标点（撇号 / 连字符除外）。
# 历史遗留：批量识词与查词兜底曾把整句碎片写进 words 表
# （"rget What They Learn?"、"urnal, s"、"Old Chen"），它们不是词，
# 进图只会变成噪声，并把图的视觉重心带偏 —— 在这里一律挡掉。
_WORD_LABEL_RE = re.compile(r"^[a-z][a-z'-]*$")
_MIN_WORD_LEN = 2
_MAX_WORD_LEN = 24

# 短语库早期从 HTML 页面抓取，残留 &quot; 实体，产出 "quot says" / "said quot"
# 这类条目（频率还很高，会霸占榜首）。含实体残留或标点的短语直接丢弃。
_JUNK_PHRASE_RE = re.compile(r"(?i)\bquot\b|&[a-z]+;|&#\d+;")
_PHRASE_RE = re.compile(r"^[a-z][a-z' -]*[a-z]$")
_MIN_PHRASE_LEN = 4
_MAX_PHRASE_LEN = 60


def _clean_word(label: Optional[str]) -> Optional[str]:
    """把候选词规范成单个小写英文词；不是词就返回 None。"""
    word = (label or "").strip().lower()
    if not (_MIN_WORD_LEN <= len(word) <= _MAX_WORD_LEN):
        return None
    if not _WORD_LABEL_RE.match(word):
        return None
    if word in _STOPWORDS:
        return None
    return word


def _clean_phrase(text: Optional[str]) -> Optional[str]:
    """短语必须是 2–5 个纯英文词；含实体残留 / 标点的一律丢弃。"""
    phrase = " ".join((text or "").split()).lower()
    if not (_MIN_PHRASE_LEN <= len(phrase) <= _MAX_PHRASE_LEN):
        return None
    if _JUNK_PHRASE_RE.search(phrase):
        return None
    if not _PHRASE_RE.match(phrase):
        return None
    if not 2 <= len(phrase.split()) <= 5:
        return None
    return phrase


# ---------------------------------------------------------------- 构建

def _upsert_node(db, node_type: str, ref_id: Optional[int], label: str,
                 weight: float = 1.0, meta: Optional[dict] = None) -> int:
    """插入或更新节点，返回节点 id。"""
    now = int(time.time())
    row = db.execute(
        "SELECT id FROM graph_nodes WHERE node_type=? AND ref_id IS ?",
        (node_type, ref_id),
    ).fetchone()
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    if row:
        db.execute(
            "UPDATE graph_nodes SET label=?, weight=?, meta=? WHERE id=?",
            (label, weight, meta_json, row["id"]),
        )
        return row["id"]
    cur = db.execute(
        """INSERT INTO graph_nodes (node_type, ref_id, label, weight, meta, created_at)
           VALUES (?,?,?,?,?,?)""",
        (node_type, ref_id, label, weight, meta_json, now),
    )
    return cur.lastrowid


def _upsert_edge(db, src: int, dst: int, edge_type: str, weight: float = 1.0) -> None:
    if src == dst:
        return
    now = int(time.time())
    db.execute(
        """INSERT INTO graph_edges (source_id, target_id, edge_type, weight, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(source_id, target_id, edge_type)
           DO UPDATE SET weight = weight + excluded.weight""",
        (src, dst, edge_type, weight, now),
    )


def rebuild(include_words: bool = True, include_phrases: bool = True,
            include_docs: bool = True, max_nodes: int = MAX_NODES) -> dict:
    """重建整张图（**全量重算**，不是增量累加）。

    早期实现用 `ON CONFLICT ... weight = weight + excluded.weight` 累加边权，
    反复点「重建图谱」会让权重无限膨胀，而且过时节点永远清不掉。
    重建就该是重建：先清空，再按当前数据重算。

    构图原则：**不产生孤立节点**。只有真的被连上的词 / 短语才建节点 ——
    孤立节点在力导向图里没有任何弹簧牵引，会被斥力一路推到画布边缘排成硬边，
    整张图的视觉比例就毁了（这正是「短语节点 100% 孤立 → 上下两条硬边」的成因）。
    """
    stats = {"nodes": 0, "edges": 0, "words": 0, "phrases": 0, "docs": 0}

    with get_db() as db:
        db.execute("DELETE FROM graph_edges")
        db.execute("DELETE FROM graph_nodes")

        # ---- 1. 候选词：清洗碎片 + 按词形去重
        # 同一个词形可能有多行（真实词条 + 早期兜底写进来的重复项，
        # 如 "every" 与 "Every"），按投入度排序后只保留第一个。
        word_meta: dict[str, dict] = {}
        if include_words:
            rows = db.execute(
                """SELECT id, lemma, text, meaning, level, frequency, status,
                          lookup_count, encounter_count, m_level
                   FROM words
                   WHERE type = 'word'
                     AND (status IN ('learning','review','target') OR lookup_count > 0
                          OR encounter_count > 0)
                   ORDER BY (lookup_count + encounter_count) DESC, frequency DESC
                   LIMIT ?""",
                (max_nodes * 4,),
            ).fetchall()

            # 还没有学习记录时退化为「按词频取常用词」，保证图不是空的
            if not rows:
                rows = db.execute(
                    """SELECT id, lemma, text, meaning, level, frequency, status,
                              lookup_count, encounter_count
                       FROM words WHERE frequency > 0
                       ORDER BY frequency DESC LIMIT ?""",
                    (max_nodes,),
                ).fetchall()

            for r in rows:
                label = _clean_word(r["lemma"] or r["text"])
                if not label or label in word_meta:
                    continue
                weight = 1.0 + min(6.0, (r["lookup_count"] or 0) * 0.6
                                        + (r["encounter_count"] or 0) * 0.3)
                word_meta[label] = {
                    "ref_id": r["id"], "weight": round(weight, 2),
                    # m_level 一并带出去：前端按掌握度给节点编码明度，
                    # 而不是用四个饱和色相区分类型（那样看着像"AI 生成"的仪表盘）
                    "meta": {"level": r["level"], "status": r["status"],
                             "meaning": (r["meaning"] or "")[:120],
                             "frequency": r["frequency"] or 0,
                             "m_level": r["m_level"] or "unknown"},
                }

        # ---- 2. 候选短语
        # 短语已并入 words 表（type='phrase'），所以这里读的是**学习状态**：
        #   - 用户接触过的短语（status 非 new / 有遇见 / 有掌握度）→ 一律入图，
        #     它们是用户自己的学习对象，和单词同等重要
        #   - 其余的按真题频次补足，作为语境
        phrase_meta: dict[str, dict] = {}
        if include_phrases:
            seen_ids: set[int] = set()
            for r in db.execute(
                """SELECT id, text, meaning, level, frequency, m_level, status
                   FROM words
                   WHERE type = 'phrase'
                     AND (status IN ('learning','review','target')
                          OR lookup_count > 0 OR encounter_count > 0
                          OR m_level != 'unknown')
                   ORDER BY frequency DESC LIMIT ?""",
                (max_nodes,),
            ).fetchall():
                text = _clean_phrase(r["text"])
                if not text or text in phrase_meta:
                    continue
                seen_ids.add(r["id"])
                phrase_meta[text] = {
                    "ref_id": r["id"],
                    "weight": round(1.5 + min(2.0, (r["frequency"] or 0) / 40), 2),
                    "meta": {"level": r["level"], "meaning": (r["meaning"] or "")[:120],
                             "frequency": r["frequency"] or 0,
                             "m_level": r["m_level"] or "unknown",
                             "status": r["status"]},
                }
            # 补足语境用的高频短语
            for r in db.execute(
                """SELECT id, text, meaning, level, frequency, m_level, status
                   FROM words WHERE type = 'phrase' AND frequency >= 8
                   ORDER BY frequency DESC LIMIT ?""",
                (max_nodes,),
            ).fetchall():
                text = _clean_phrase(r["text"])
                if not text or text in phrase_meta or r["id"] in seen_ids:
                    continue
                phrase_meta[text] = {
                    "ref_id": r["id"],
                    "weight": round(1.5 + min(2.0, (r["frequency"] or 0) / 40), 2),
                    "meta": {"level": r["level"], "meaning": (r["meaning"] or "")[:120],
                             "frequency": r["frequency"] or 0,
                             "m_level": r["m_level"] or "unknown",
                             "status": r["status"]},
                }

        node_ids: dict[tuple[str, str], int] = {}

        def _get_word_node(label: str) -> int:
            key = ("word", label)
            if key not in node_ids:
                info = word_meta[label]
                node_ids[key] = _upsert_node(db, "word", info["ref_id"], label,
                                             info["weight"], info["meta"])
            return node_ids[key]

        def _get_phrase_node(text: str) -> int:
            key = ("phrase", text)
            if key not in node_ids:
                info = phrase_meta[text]
                node_ids[key] = _upsert_node(db, "phrase", info["ref_id"], text,
                                             info["weight"], info["meta"])
            return node_ids[key]

        # ---- 3. 文档节点 + 边
        if include_docs:
            docs = []
            for r in db.execute("SELECT id, title, content FROM articles LIMIT 100").fetchall():
                docs.append(("article", r["id"], r["title"], r["content"] or ""))
            for r in db.execute(
                "SELECT id, title, content FROM materials WHERE parse_status='parsed' LIMIT 100"
            ).fetchall():
                docs.append(("material", r["id"], r["title"], r["content"] or ""))

            for dtype, did, title, content in docs:
                lowered = content.lower()
                toks = [w.lower() for w in _WORD_RE.findall(content)]
                present = [w for w in toks if w in word_meta]
                # 短语只有真的出现在文档里才建节点，这样它必然带有连边
                matched = [p for p in phrase_meta if p in lowered]
                if not present and not matched:
                    continue

                counter = Counter(present)
                doc_node = _upsert_node(
                    db, dtype, did, (title or "")[:60],
                    round(1.0 + min(6.0, len(counter) / 20 + len(matched) / 10), 2),
                    {"words": len(counter), "phrases": len(matched)},
                )
                for w, c in counter.most_common(40):
                    _upsert_edge(db, doc_node, _get_word_node(w), "contains", float(c))
                # 每份文档最多挂 40 条短语。短语来自通用短语库、不是用户自己的积累，
                # 全挂上去会让某一份材料变成"蒲公英"，把整张图的比例主导掉。
                for p in matched[:40]:
                    _upsert_edge(db, doc_node, _get_phrase_node(p), "contains",
                                 float(min(20, lowered.count(p))))

                # 词↔词 共现（只取同一文档里的高频词对，控制边数）
                top = [w for w, _ in counter.most_common(12)]
                for i in range(len(top)):
                    for j in range(i + 1, len(top)):
                        _upsert_edge(db, _get_word_node(top[i]), _get_word_node(top[j]),
                                     "cooccur", 1.0)
                stats["docs"] += 1

        # ---- 4. 兜底：一份文档都没有时，也要给出一张能看的图
        # 此时节点天然孤立，前端会把孤立节点锚在环形上，不会挤成硬边。
        if not stats["docs"]:
            for label in list(word_meta)[:60]:
                _get_word_node(label)
            if include_phrases:
                for text in list(phrase_meta)[:60]:
                    _get_phrase_node(text)

        stats["words"] = sum(1 for kind, _ in node_ids if kind == "word")
        stats["phrases"] = sum(1 for kind, _ in node_ids if kind == "phrase")
        stats["nodes"] = db.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        stats["edges"] = db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]

    return stats


# ---------------------------------------------------------------- 查询

def get_graph(node_types: Optional[list[str]] = None, limit: int = 400) -> dict:
    """取图数据（前端渲染用）。

    limit 限制返回的节点数：按 weight 降序取最重要的节点，
    只保留两端都在集合内的边，避免出现悬空连线。
    """
    with get_db() as db:
        if node_types:
            ph = ",".join("?" * len(node_types))
            nodes = db.execute(
                f"SELECT id, node_type, ref_id, label, weight, meta FROM graph_nodes "
                f"WHERE node_type IN ({ph}) ORDER BY weight DESC LIMIT ?",
                (*node_types, limit),
            ).fetchall()
        else:
            nodes = db.execute(
                "SELECT id, node_type, ref_id, label, weight, meta FROM graph_nodes "
                "ORDER BY weight DESC LIMIT ?",
                (limit,),
            ).fetchall()

        ids = {r["id"] for r in nodes}
        if not ids:
            return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0}}

        ph = ",".join("?" * len(ids))
        id_list = list(ids)
        edges = db.execute(
            f"SELECT source_id, target_id, edge_type, weight FROM graph_edges "
            f"WHERE source_id IN ({ph}) AND target_id IN ({ph})",
            (*id_list, *id_list),
        ).fetchall()

    out_nodes = []
    for r in nodes:
        try:
            meta = json.loads(r["meta"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        out_nodes.append({
            "id": r["id"], "type": r["node_type"], "ref_id": r["ref_id"],
            "label": r["label"], "weight": r["weight"], "meta": meta,
        })

    return {
        "nodes": out_nodes,
        "edges": [
            {"source": e["source_id"], "target": e["target_id"],
             "type": e["edge_type"], "weight": e["weight"]}
            for e in edges
        ],
        "stats": {"nodes": len(out_nodes), "edges": len(edges)},
    }


def neighbors(node_id: int, depth: int = 1) -> dict:
    """取某个节点的邻域，用于点击展开。"""
    with get_db() as db:
        root = db.execute("SELECT * FROM graph_nodes WHERE id=?", (node_id,)).fetchone()
        if not root:
            return {"nodes": [], "edges": []}

        seen = {node_id}
        frontier = {node_id}
        edges = []
        for _ in range(max(1, depth)):
            if not frontier:
                break
            ph = ",".join("?" * len(frontier))
            rows = db.execute(
                f"""SELECT source_id, target_id, edge_type, weight FROM graph_edges
                    WHERE source_id IN ({ph}) OR target_id IN ({ph})""",
                (*frontier, *frontier),
            ).fetchall()
            nxt = set()
            for e in rows:
                edges.append({
                    "source": e["source_id"], "target": e["target_id"],
                    "type": e["edge_type"], "weight": e["weight"],
                })
                for n in (e["source_id"], e["target_id"]):
                    if n not in seen:
                        seen.add(n)
                        nxt.add(n)
            frontier = nxt

        ph = ",".join("?" * len(seen))
        nodes = db.execute(
            f"SELECT id, node_type, ref_id, label, weight, meta FROM graph_nodes WHERE id IN ({ph})",
            tuple(seen),
        ).fetchall()

    out = []
    for r in nodes:
        try:
            meta = json.loads(r["meta"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        out.append({"id": r["id"], "type": r["node_type"], "ref_id": r["ref_id"],
                    "label": r["label"], "weight": r["weight"], "meta": meta})
    return {"nodes": out, "edges": edges}


def clear() -> None:
    with get_db() as db:
        db.execute("DELETE FROM graph_edges")
        db.execute("DELETE FROM graph_nodes")


def stats() -> dict:
    with get_db() as db:
        nodes = db.execute(
            "SELECT node_type, COUNT(*) c FROM graph_nodes GROUP BY node_type"
        ).fetchall()
        edges = db.execute(
            "SELECT edge_type, COUNT(*) c FROM graph_edges GROUP BY edge_type"
        ).fetchall()
    return {
        "nodes": {r["node_type"]: r["c"] for r in nodes},
        "edges": {r["edge_type"]: r["c"] for r in edges},
    }
