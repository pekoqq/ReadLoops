"""阅读行为路由：查词记录、高亮、阅读会话。"""
import time

from fastapi import APIRouter
from pydantic import BaseModel

from app.database import get_db

router = APIRouter(prefix="/api/reading", tags=["reading"])


class LookupRequest(BaseModel):
    word_id: int = 0
    article_id: int = 0
    context: str = ""


class HighlightRequest(BaseModel):
    article_id: int
    text: str
    word_id: int = 0
    color: str = "yellow"


class SessionRequest(BaseModel):
    article_id: int
    duration_seconds: int = 0
    lookups: int = 0
    highlights: int = 0


@router.post("/lookup")
async def record_lookup(word_id: int = 0, article_id: int = 0, context: str = ""):
    """记录一次查词。
    算法：
    - 第1次查词：只记录
    - 第2次查词：标记为 target（后续文章优先包含，测验记忆）
    - 第3次及以上：自动加入生词本（learning）
    """
    now = int(time.time())
    with get_db() as conn:
        if word_id:
            conn.execute(
                "INSERT INTO word_encounters (word_id, article_id, context, action, created_at) "
                "VALUES (?, ?, ?, 'lookup', ?)",
                (word_id, article_id or None, context, now),
            )
            # 查词是掌握度模型里权重很高的负向证据，立刻重算该词
            from app.services import mastery
            mastery.refresh([word_id])
            # 获取当前 lookup_count（更新前）
            row = conn.execute("SELECT lookup_count, status FROM words WHERE id=?", (word_id,)).fetchone()
            if row:
                current_count = row["lookup_count"]
                new_count = current_count + 1
                # 根据查词次数更新状态
                if new_count >= 3 and row["status"] != "learning":
                    # 第3次查词：自动加入生词本
                    new_status = "learning"
                elif new_count == 2 and row["status"] == "new":
                    # 第2次查词：标记为目标词
                    new_status = "target"
                else:
                    new_status = row["status"]
                conn.execute(
                    "UPDATE words SET lookup_count = ?, status = ?, updated_at = ? WHERE id=?",
                    (new_count, new_status, now, word_id),
                )
                return {"status": "ok", "lookup_count": new_count, "word_status": new_status}
    return {"status": "ok"}


@router.get("/lookups")
async def get_lookup_history(limit: int = 50):
    """获取查词历史（最近查过的词，去重）。"""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT w.id, w.text, w.phonetic, w.meaning, MAX(we.created_at) as last_lookup,
                   COUNT(we.id) as lookup_count
            FROM word_encounters we
            JOIN words w ON w.id = we.word_id
            WHERE we.action = 'lookup'
            GROUP BY w.id
            ORDER BY last_lookup DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "word_id": r["id"],
            "text": r["text"],
            "phonetic": r["phonetic"] or "",
            "meaning": r["meaning"] or "",
            "last_lookup": r["last_lookup"],
            "lookup_count": r["lookup_count"],
        }
        for r in rows
    ]


@router.post("/highlight")
async def record_highlight(req: HighlightRequest):
    """记录一次高亮。"""
    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO highlights (article_id, text, word_id, color, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (req.article_id, req.text, req.word_id or None, req.color, now),
        )
        if req.word_id:
            conn.execute(
                "UPDATE words SET encounter_count = encounter_count + 1 WHERE id=?",
                (req.word_id,),
            )
    return {"status": "ok"}


@router.delete("/highlight")
async def delete_highlight(article_id: int, text: str):
    """删除一次高亮。"""
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM highlights WHERE article_id=? AND text=?",
            (article_id, text),
        )
    return {"status": "ok", "deleted": result.rowcount}


@router.post("/session")
async def record_session(req: SessionRequest):
    """记录一次阅读会话。"""
    now = int(time.time())
    with get_db() as conn:
        # 先检查文章是否存在，避免外键约束失败
        article = conn.execute("SELECT id FROM articles WHERE id=?", (req.article_id,)).fetchone()
        if article:
            conn.execute(
                "INSERT INTO reading_sessions (article_id, start_time, end_time, duration_seconds, lookups, highlights) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (req.article_id, now - req.duration_seconds, now, req.duration_seconds, req.lookups, req.highlights),
            )
            conn.execute(
                "UPDATE articles SET reading_time_seconds = reading_time_seconds + ? WHERE id=?",
                (req.duration_seconds, req.article_id),
            )
    return {"status": "ok"}


@router.get("/highlights/{article_id}")
async def get_highlights(article_id: int):
    """获取文章的高亮。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT text, color, created_at FROM highlights WHERE article_id=? ORDER BY created_at",
            (article_id,),
        ).fetchall()
    return [{"text": r["text"], "color": r["color"], "created_at": r["created_at"]} for r in rows]
