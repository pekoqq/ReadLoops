"""书架 API：公共领域书籍的搜索、下载、导入材料。

只接 Project Gutenberg。这类书籍版权已过期，可合法下载与再加工，
是唯一没有版权风险的语料来源。
"""
from fastapi import APIRouter, HTTPException

from app.services import bookshelf as bs
from app.services import materials as mt

router = APIRouter(prefix="/api/books", tags=["books"])


@router.get("/search")
def search(q: str = "", page: int = 1, language: str = "en"):
    if not q.strip():
        return {"results": [], "page": page, "has_next": False}
    try:
        return bs.search_books(q, page=page, language=language)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"书源访问失败：{e}")


@router.get("")
def list_all(status: str | None = None):
    return bs.list_books(status=status)


@router.post("/download")
def download(payload: dict):
    source_id = str(payload.get("source_id") or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="缺少 source_id")
    result = bs.download_book(
        source_id,
        title=payload.get("title", ""),
        author=payload.get("author", ""),
        language=payload.get("language", "en"),
        subjects=payload.get("subjects") or [],
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "下载失败"))
    return result


@router.post("/{book_id}/import")
def import_as_material(book_id: int):
    """把书架里的书导入为材料，供蒸馏与出题使用。"""
    result = mt.import_book(book_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "导入失败"))
    return result


@router.delete("/{book_id}")
def remove(book_id: int):
    if not bs.delete_book(book_id):
        raise HTTPException(status_code=404, detail="书籍不存在")
    return {"ok": True}
