"""统计、测试、设置路由。"""
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.services import encounter, mastery, selection, vocab_gap

router = APIRouter(prefix="/api", tags=["stats"])


class SettingsRequest(BaseModel):
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    theme: str = ""
    font_size: str = ""
    line_height: str = ""


class TestApiRequest(BaseModel):
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""


@router.get("/stats/overview")
async def stats_overview():
    """总览统计。"""
    with get_db() as conn:
        articles_read = conn.execute("SELECT COUNT(*) as c FROM articles").fetchone()["c"]
        total_words = conn.execute("SELECT COALESCE(SUM(word_count),0) as s FROM articles").fetchone()["s"]
        words_learning = conn.execute(
            "SELECT COUNT(*) as c FROM words WHERE status='learning'"
        ).fetchone()["c"]
        words_known = conn.execute(
            "SELECT COUNT(*) as c FROM words WHERE status='known'"
        ).fetchone()["c"]
        words_target = conn.execute(
            "SELECT COUNT(*) as c FROM words WHERE status='target'"
        ).fetchone()["c"]
        total_time = conn.execute(
            "SELECT COALESCE(SUM(duration_seconds),0) as s FROM reading_sessions"
        ).fetchone()["s"]
        total_lookups = conn.execute(
            "SELECT COUNT(*) as c FROM word_encounters WHERE action='lookup'"
        ).fetchone()["c"]

        # 连续学习天数（streak）
        streak = 0
        days = conn.execute(
            "SELECT DISTINCT date(created_at, 'unixepoch', 'localtime') as d "
            "FROM articles ORDER BY d DESC LIMIT 30"
        ).fetchall()
        if days:
            from datetime import datetime, timedelta
            today = datetime.now().date()
            day_set = set(d["d"] for d in days)
            # 从今天或昨天开始算
            check = today
            if str(check) not in day_set:
                check = today - timedelta(days=1)
            while str(check) in day_set:
                streak += 1
                check -= timedelta(days=1)

        # 词汇量估算（已掌握 + 学习中 * 0.5 + 测试正确率加权）
        test_correct = conn.execute(
            "SELECT COALESCE(SUM(correct_count),0) as s FROM words"
        ).fetchone()["s"]
        test_total = conn.execute(
            "SELECT COALESCE(SUM(correct_count + wrong_count),0) as s FROM words"
        ).fetchone()["s"]
        vocab_estimate = words_known + int(words_learning * 0.5) + 2000  # 基础2000 + 已掌握 + 学习中一半

        # FSRS 统计
        from app.services.srs import get_review_stats
        srs = get_review_stats(conn)

        # 查词热词 TOP10
        hot_words = conn.execute(
            "SELECT w.text, w.lookup_count FROM words w "
            "WHERE w.lookup_count > 0 ORDER BY w.lookup_count DESC LIMIT 10"
        ).fetchall()

    return {
        "articles_read": articles_read,
        "total_words": total_words,
        "words_learning": words_learning,
        "words_known": words_known,
        "words_target": words_target,
        "total_reading_seconds": total_time,
        "total_lookups": total_lookups,
        "streak_days": streak,
        "vocab_estimate": vocab_estimate,
        "test_accuracy": round(test_correct / test_total * 100, 1) if test_total > 0 else 0,
        "srs": srs,
        "reentry": encounter.summary(),
        "hot_words": [{"text": r["text"], "count": r["lookup_count"]} for r in hot_words],
    }


@router.get("/stats/recent")
async def stats_recent(days: int = 7):
    """最近统计。"""
    since = int(time.time()) - days * 86400
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date(created_at, 'unixepoch', 'localtime') as day, COUNT(*) as cnt "
            "FROM articles WHERE created_at >= ? GROUP BY day ORDER BY day",
            (since,),
        ).fetchall()
    return [{"date": r["day"], "count": r["cnt"]} for r in rows]


@router.get("/stats/activity")
async def stats_activity(weeks: int = 52):
    """每日活动热力图数据（最近 N 周）。"""
    from datetime import datetime, timedelta
    today = datetime.now().date()
    # 计算起始日期（对齐到周一）
    start = today - timedelta(days=weeks * 7 + today.weekday())

    with get_db() as conn:
        # 文章数按天
        articles_by_day = {}
        for r in conn.execute(
            "SELECT date(created_at, 'unixepoch', 'localtime') as d, COUNT(*) as c "
            "FROM articles WHERE created_at >= ? GROUP BY d",
            (int(datetime.combine(start, datetime.min.time()).timestamp()),),
        ).fetchall():
            articles_by_day[r["d"]] = r["c"]

        # 阅读时长按天
        time_by_day = {}
        for r in conn.execute(
            "SELECT date(start_time, 'unixepoch', 'localtime') as d, "
            "COALESCE(SUM(duration_seconds),0) as s FROM reading_sessions "
            "WHERE start_time >= ? GROUP BY d",
            (int(datetime.combine(start, datetime.min.time()).timestamp()),),
        ).fetchall():
            time_by_day[r["d"]] = r["s"]

        # 查词数按天
        lookup_by_day = {}
        for r in conn.execute(
            "SELECT date(created_at, 'unixepoch', 'localtime') as d, COUNT(*) as c "
            "FROM word_encounters WHERE action='lookup' AND created_at >= ? GROUP BY d",
            (int(datetime.combine(start, datetime.min.time()).timestamp()),),
        ).fetchall():
            lookup_by_day[r["d"]] = r["c"]

    # 生成完整日期序列
    days = []
    d = start
    while d <= today:
        ds = str(d)
        articles = articles_by_day.get(ds, 0)
        seconds = time_by_day.get(ds, 0)
        lookups = lookup_by_day.get(ds, 0)
        # 活动等级：0-4（基于文章数+阅读分钟+查词数综合）
        score = articles * 2 + min(seconds / 300, 3) + min(lookups / 5, 2)
        level = 0 if score == 0 else min(4, int(score) + 1)
        days.append({
            "date": ds,
            "articles": articles,
            "reading_seconds": seconds,
            "lookups": lookups,
            "level": level,
        })
        d += timedelta(days=1)

    return {"days": days, "total_days": len(days), "active_days": sum(1 for d in days if d["level"] > 0)}


@router.post("/stats/test/start")
async def test_start():
    """开始一次词汇测试。"""
    now = int(time.time())
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tests (type, status, created_at) VALUES ('vocab', 'in_progress', ?)",
            (now,),
        )
        test_id = cur.lastrowid
    return {"test_id": test_id}


@router.post("/stats/test/{test_id}/submit")
async def test_submit(test_id: int, correct: int = 0, total: int = 0):
    """提交测试结果。"""
    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            "UPDATE tests SET status='completed', score=?, total=?, completed_at=? WHERE id=?",
            (correct, total, now, test_id),
        )
    return {"test_id": test_id, "score": correct, "total": total}


@router.get("/settings")
async def get_settings():
    """获取设置。"""
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.post("/settings")
async def update_settings(req: SettingsRequest):
    """更新设置。"""
    with get_db() as conn:
        for key, value in req.model_dump().items():
            if value:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
    return {"status": "ok"}


@router.post("/settings/test")
async def test_api_connection(req: TestApiRequest):
    """测试 AI API 连接。"""
    base_url = req.ai_base_url.rstrip("/")
    api_key = req.ai_api_key
    model = req.ai_model
    if not base_url or not api_key:
        return {"ok": False, "error": "请填写 Base URL 和 API Key"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", [])]
                return {"ok": True, "model": model or (models[0] if models else "未知")}
            resp2 = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model or "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 5,
                    "thinking": {"type": "disabled"},
                },
            )
            if resp2.status_code == 200:
                return {"ok": True, "model": model}
            return {"ok": False, "error": f"HTTP {resp2.status_code}: {resp2.text[:100]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}


@router.get("/stats/reentry")
def reentry(limit: int = 200, only_active: bool = True):
    """重遇进度：每个学习词跨了多少篇文章。

    口径是「跨多少篇」而不是「见了几次」—— 方法要求的是**变化的语境**，
    同一篇文章里看 12 遍没有意义。
    """
    return {
        "summary": encounter.summary(),
        "items": encounter.reentry_stats(limit=max(1, min(limit, 1000)),
                                         only_active=only_active),
    }


@router.get("/stats/vocab-gap")
def vocab_gap_overview():
    """词汇差距：词汇量、真题覆盖率、各考试缺口、进度预测。

    覆盖率不是「认识多少词表里的词」，而是**按词频档位给语料加权** ——
    实测 CET4 大纲词表只覆盖四级真题语料 15.8% 的 token（词表装的是内容词，
    剩下 84% 是基础词与功能词），拿词表百分比当「能读懂多少」会严重失真。
    """
    # 没有定级结果时铺一条默认假设，否则新用户的整页数字都是空的
    from app.services import placement as pl
    pl.ensure_default_run()
    return vocab_gap.overview()


@router.get("/stats/target-exam")
def get_target_exam():
    """当前目标考试。"""
    return {"exam": selection.target_exam(),
            "label": selection.EXAM_LABELS.get(selection.target_exam(), ""),
            "options": [{"key": k, "label": v} for k, v in selection.EXAM_LABELS.items()]}


@router.post("/stats/target-exam")
def set_target_exam(payload: dict):
    """设置目标考试 —— 它决定下一篇埋哪些词。"""
    exam = str(payload.get("exam", "")).strip()
    if not selection.set_target_exam(exam):
        raise HTTPException(status_code=400, detail=f"不支持的考试：{exam}")
    return {"ok": True, "exam": exam,
            "label": selection.EXAM_LABELS.get(exam, "")}


@router.get("/stats/mastery")
def mastery_stats():
    """掌握度分布：识别 vs 回忆分开统计。"""
    return mastery.stats()
