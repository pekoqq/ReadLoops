"""单词路由：查词、生词本、批量添加。"""
import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.database import get_db
from app.services.ai import lookup_word, recognize_batch_vocabulary, translate_sentence

router = APIRouter(prefix="/api/words", tags=["words"])


class AddWordRequest(BaseModel):
    word: str
    meaning: str = ""
    context: str = ""


class BatchWordItem(BaseModel):
    word: str
    meaning: str = ""
    phonetic: str = ""
    level: str = "CET4"


class BatchAddRequest(BaseModel):
    # 兼容旧前端：words 仍可直接提交；新前端提交带释义/等级的 items。
    words: list[str] = []
    items: list[BatchWordItem] = []


class BatchRecognizeRequest(BaseModel):
    text: str


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


class TranslateRequest(BaseModel):
    text: str


@router.post("/translate")
async def translate(req: TranslateRequest):
    """翻译句子 / 短语。

    划词选中多个单词时走这里 —— 词典查不到整句，需要调 AI。
    AI 调用是同步阻塞的，放到线程池避免卡住事件循环；失败返回 502 与原因，
    前端据此提示「点击重试」而不是笼统的「翻译失败」。
    """
    text = (req.text or "").strip()
    if not text:
        return {"text": "", "translation": "", "ok": False, "error": "选中文本为空"}
    try:
        translation = await asyncio.to_thread(translate_sentence, text)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"text": text, "translation": "", "ok": False,
                     "error": f"AI 服务暂时不可用：{str(exc)[:160]}"},
        )
    return {"text": text, "translation": translation, "ok": bool(translation)}


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


@router.post("/batch-recognize")
async def batch_recognize(req: BatchRecognizeRequest):
    """AI 识别任意格式的批量词汇；AI 不可用时自动降级本地解析。"""
    if not req.text.strip():
        return {"items": [], "mode": "local", "warning": "内容为空"}
    return await asyncio.to_thread(recognize_batch_vocabulary, req.text)


@router.post("/batch-add")
async def batch_add(req: BatchAddRequest):
    """确认后批量加入生词本。

    词典中已存在的词并非重复：它们只是本地词典记录，仍应被激活为 learning。
    因此返回 added（全新）和 activated（已有词典/复习词转入学习）两个计数。
    """
    now = int(time.time())
    raw_items = [i.model_dump() for i in req.items] if req.items else [
        {"word": w, "meaning": "", "phonetic": "", "level": "CET4"} for w in req.words
    ]
    added = activated = skipped = 0
    seen = set()
    valid_levels = {"CET4", "CET6", "other"}
    with get_db() as conn:
        for item in raw_items:
            w = (item.get("word") or "").strip().lower()
            # 允许短语，但拒绝含数字、标点或过长的噪声行
            import re
            if not re.fullmatch(r"[a-z][a-z'-]*(?: [a-z][a-z'-]*){0,3}", w) or w in seen:
                skipped += 1
                continue
            seen.add(w)
            meaning = (item.get("meaning") or "").strip()[:300]
            phonetic = (item.get("phonetic") or "").strip()[:100]
            level = (item.get("level") or "CET4").upper()
            if level not in valid_levels:
                level = "CET4"

            existing = conn.execute("SELECT id, status FROM words WHERE text = ?", (w,)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE words SET meaning=COALESCE(NULLIF(?, ''), meaning),
                       phonetic=COALESCE(NULLIF(?, ''), phonetic), level=COALESCE(NULLIF(?, ''), level),
                       status='learning', updated_at=? WHERE id=?""",
                    (meaning, phonetic, level, now, existing["id"]),
                )
                activated += 1
            else:
                conn.execute(
                    """INSERT INTO words
                       (lemma, text, type, meaning, phonetic, level, status, created_at, updated_at)
                       VALUES (?, ?, 'word', ?, ?, ?, 'learning', ?, ?)""",
                    (w, w, meaning, phonetic, level, now, now),
                )
                added += 1
    return {"added": added, "activated": activated, "skipped": skipped,
            "total": len(raw_items)}


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
            "INSERT INTO tests (type, status, created_at) VALUES ('vocab', 'in_progress', ?)",
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
