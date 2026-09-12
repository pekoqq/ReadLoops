"""单词路由：查词、生词本、批量添加。"""
import time

from fastapi import APIRouter
from pydantic import BaseModel

from app.database import get_db
from app.services.ai import lookup_word

router = APIRouter(prefix="/api/words", tags=["words"])


class AddWordRequest(BaseModel):
    word: str
    meaning: str = ""
    context: str = ""


class BatchAddRequest(BaseModel):
    words: list[str]


@router.get("/lookup/{word}")
async def lookup(word: str):
    """查询单词释义。优先本地，没有则问 AI。"""
    result = lookup_word(word)
    return {
        "word": result.text,
        "phonetic": result.phonetic or "",
        "meaning": result.meaning or "",
        "examples": [],
        "in_vocab": result.id is not None,
        "word_id": result.id,
    }


@router.post("/add")
async def add_word(req: AddWordRequest):
    """添加单词到生词本。"""
    now = int(time.time())
    with get_db() as conn:
        row = conn.execute("SELECT id FROM words WHERE text = ?", (req.word,)).fetchone()
        if row:
            word_id = row["id"]
            conn.execute(
                "UPDATE words SET meaning=COALESCE(NULLIF(?, ''), meaning), status='learning', updated_at=? WHERE id=?",
                (req.meaning, now, word_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO words (lemma, text, type, meaning, level, status, created_at, updated_at) "
                "VALUES (?, ?, 'word', ?, 'CET4', 'learning', ?, ?)",
                (req.word, req.word, req.meaning, now, now),
            )
            word_id = cur.lastrowid
    return {"word_id": word_id, "word": req.word, "status": "added"}


@router.post("/batch-add")
async def batch_add(req: BatchAddRequest):
    """批量添加单词。"""
    now = int(time.time())
    added = 0
    with get_db() as conn:
        for w in req.words:
            w = w.strip().lower()
            if not w or not w.isalpha():
                continue
            existing = conn.execute("SELECT id FROM words WHERE text = ?", (w,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO words (lemma, text, type, level, status, created_at, updated_at) "
                    "VALUES (?, ?, 'word', 'CET4', 'learning', ?, ?)",
                    (w, w, now, now),
                )
                added += 1
    return {"added": added, "total": len(req.words)}


@router.get("/vocab")
async def get_vocab(status: str = "learning", limit: int = 100):
    """获取生词本列表。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, text, phonetic, meaning, level, status, encounter_count, lookup_count, created_at "
            "FROM words WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "text": r["text"],
            "phonetic": r["phonetic"] or "",
            "meaning": r["meaning"] or "",
            "level": r["level"],
            "status": r["status"],
            "encounter_count": r["encounter_count"],
            "lookup_count": r["lookup_count"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


class BatchDeleteRequest(BaseModel):
    word_ids: list[int]


class BatchStatusRequest(BaseModel):
    word_ids: list[int]
    status: str


@router.delete("/{word_id}")
async def delete_word(word_id: int):
    """删除单个单词（同时删除相关的遇见记录）。"""
    with get_db() as conn:
        conn.execute("DELETE FROM word_encounters WHERE word_id=?", (word_id,))
        conn.execute("DELETE FROM words WHERE id=?", (word_id,))
    return {"status": "ok"}


@router.post("/batch-delete")
async def batch_delete(req: BatchDeleteRequest):
    """批量删除单词。"""
    with get_db() as conn:
        for wid in req.word_ids:
            conn.execute("DELETE FROM word_encounters WHERE word_id=?", (wid,))
            conn.execute("DELETE FROM words WHERE id=?", (wid,))
    return {"deleted": len(req.word_ids)}


@router.post("/batch-status")
async def batch_update_status(req: BatchStatusRequest):
    """批量修改单词状态（learning/known/new/target）。"""
    now = int(time.time())
    with get_db() as conn:
        for wid in req.word_ids:
            conn.execute("UPDATE words SET status=?, updated_at=? WHERE id=?", (req.status, now, wid))
    return {"updated": len(req.word_ids), "status": req.status}


@router.get("/test/generate")
async def generate_test(count: int = 20, difficulty: str = "mixed", source: str = "adaptive"):
    """生成单词测试题。
    source: adaptive(自适应/基于用户数据)/vocab(生词本)/target(测验词)/all(全部随机)
    """
    from app.services.smart_test import generate_smart_test
    return generate_smart_test(count, source)


@router.post("/test/{test_id}/submit")
async def submit_test(test_id: int, word_id: int, is_correct: bool, reaction_time: int = None):
    """提交单题测试结果。"""
    from app.services.smart_test import submit_test_result
    submit_test_result(test_id, word_id, is_correct, reaction_time)
    return {"status": "ok"}


@router.get("/test/{test_id}/report")
async def get_test_report(test_id: int):
    """获取测试报告。"""
    from app.services.smart_test import get_test_report
    return get_test_report(test_id)


@router.post("/test/start")
async def start_test():
    """开始一次测试，创建测试记录。"""
    import time
    now = int(time.time())
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tests (user_id, type, created_at) VALUES (1, 'vocab', ?)",
            (now,)
        )
        test_id = cur.lastrowid
    return {"test_id": test_id}


@router.get("/review-due")
async def get_review_due(limit: int = 50):
    """获取今天到期复习的词（FSRS 驱动）。"""
    from app.services.srs import get_due_words
    with get_db() as conn:
        words = get_due_words(limit=limit, conn=conn)
    return words


@router.get("/srs-stats")
async def get_srs_stats():
    """获取 FSRS 统计概览。"""
    from app.services.srs import get_review_stats
    with get_db() as conn:
        return get_review_stats(conn)
