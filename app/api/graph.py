"""知识图谱 API。

同一份图两套用途：前端力导向可视化（给用户看）+ 邻接检索（给 AI 用）。
"""
from fastapi import APIRouter, HTTPException

from app.services import graph as g

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("")
def get_graph(types: str = "", limit: int = 400):
    """取图数据。types 用逗号分隔，例如 word,phrase。"""
    node_types = [t.strip() for t in types.split(",") if t.strip()] or None
    return g.get_graph(node_types=node_types, limit=max(20, min(limit, 1200)))


@router.get("/stats")
def stats():
    return g.stats()


@router.get("/node/{node_id}")
def neighbors(node_id: int, depth: int = 1):
    return g.neighbors(node_id, depth=max(1, min(depth, 3)))


@router.post("/rebuild")
def rebuild(payload: dict | None = None):
    payload = payload or {}
    try:
        return g.rebuild(
            include_words=bool(payload.get("words", True)),
            include_phrases=bool(payload.get("phrases", True)),
            include_docs=bool(payload.get("docs", True)),
            max_nodes=int(payload.get("max_nodes", 600)),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重建失败：{e}")


@router.delete("")
def clear():
    g.clear()
    return {"ok": True}
