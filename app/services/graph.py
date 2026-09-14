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
    """重建整张图。

    这是幂等的：节点按 (type, ref_id) 唯一，边按 (src, dst, type) 累加权重，
    所以可以反复调用，图会随数据增长而变丰富。
    """
    stats = {"nodes": 0, "edges": 0, "words": 0, "phrases": 0, "docs": 0}

    with get_db() as db:
        word_ids: dict[str, int] = {}

        # ---- 1. 词节点：优先取「用户接触过 / 正在学」的词，其次高频词
        if include_words:
            rows = db.execute(
                """SELECT id, lemma, text, meaning, level, frequency, status,
                          lookup_count, encounter_count
                   FROM words
                   WHERE status IN ('learning','review') OR lookup_count > 0
                      OR encounter_count > 0
                   ORDER BY (lookup_count + encounter_count) DESC, frequency DESC
                   LIMIT ?""",
                (max_nodes,),
            ).fetchall()

            # 如果用户还没有学习记录，退化为「按词频取常用词」，保证图不是空的
            if not rows:
                rows = db.execute(
                    """SELECT id, lemma, text, meaning, level, frequency, status,
                              lookup_count, encounter_count
                       FROM words WHERE frequency > 0
                       ORDER BY frequency DESC LIMIT ?""",
                    (max_nodes,),
                ).fetchall()

            for r in rows:
                lemma = (r["lemma"] or r["text"] or "").lower()
                if not lemma or lemma in _STOPWORDS:
                    continue
                w = 1.0 + (r["lookup_count"] or 0) * 0.6 + (r["encounter_count"] or 0) * 0.3
                nid = _upsert_node(db, "word", r["id"], lemma, round(w, 2), {
                    "level": r["level"], "status": r["status"],
                    "meaning": (r["meaning"] or "")[:120],
                    "frequency": r["frequency"] or 0,
                })
                word_ids[lemma] = nid
                stats["words"] += 1

        # ---- 2. 短语节点
        if include_phrases:
            rows = db.execute(
                "SELECT id, text, meaning, level, frequency FROM phrases "
                "ORDER BY frequency DESC LIMIT 150"
            ).fetchall()
            for r in rows:
                _upsert_node(db, "phrase", r["id"], r["text"], 1.5, {
                    "level": r["level"], "meaning": (r["meaning"] or "")[:120],
                    "frequency": r["frequency"] or 0,
                })
                stats["phrases"] += 1

        # ---- 3. 文章 / 材料节点 + 共现边
        if include_docs:
            docs = []
            for r in db.execute("SELECT id, title, content FROM articles LIMIT 100").fetchall():
                docs.append(("article", r["id"], r["title"], r["content"] or ""))
            for r in db.execute(
                "SELECT id, title, content FROM materials WHERE parse_status='parsed' LIMIT 100"
            ).fetchall():
                docs.append(("material", r["id"], r["title"], r["content"] or ""))

            for dtype, did, title, content in docs:
                # 只统计已建节点的词，避免图里出现孤立叶子
                toks = [w.lower() for w in _WORD_RE.findall(content)]
                present = [w for w in toks if w in word_ids]
                if not present:
                    continue
                counter = Counter(present)
                did_node = _upsert_node(
                    db, dtype, did, (title or "")[:60],
                    round(1.0 + len(counter) / 200, 2),
                    {"words": len(counter)},
                )
                # 文章→词（contains）
                for w, c in counter.most_common(40):
                    _upsert_edge(db, did_node, word_ids[w], "contains", float(c))
                # 词↔词 共现（只取同段高频词对，控制边数）
                top = [w for w, _ in counter.most_common(12)]
                for i in range(len(top)):
                    for j in range(i + 1, len(top)):
                        _upsert_edge(db, word_ids[top[i]], word_ids[top[j]], "cooccur", 1.0)
                stats["docs"] += 1

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
