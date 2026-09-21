"""文章路由：生成、列表、详情。"""
import json

from fastapi import APIRouter, HTTPException

from app.database import get_db
from app.services import encounter, mastery
from app.services.ai import generate_article

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.post("/generate")
async def generate():
    """生成一篇新文章。"""
    article = generate_article()
    if not article:
        raise HTTPException(status_code=500, detail="无法生成文章，请检查 AI 配置")
    targets = json.loads(article.target_words) if article.target_words else []
    # 生成即记「遇见」：方法的要害是「在变化语境中反复遇见」，而系统此前只在
    # 用户查词时才计数 —— 看到了但没查就完全不算，重遇统计永远起不来。
    try:
        encounter.record_article(article.id, article.content, targets)
        # 遇见改变了识别层的证据，立刻重算（只算这一篇涉及到的词）
        mastery.refresh()
    except Exception:
        pass  # 记录失败不能影响文章生成
    return {
        "id": article.id,
        "title": article.title,
        "content": article.content,
        "source": article.source,
        "word_count": article.word_count,
        "new_word_count": article.new_word_count,
        "target_words": targets,
        # 短语单列出来，前端可以分开展示「本篇补的单词 / 短语」
        "target_phrases": [t for t in targets if " " in t],
        "created_at": article.created_at,
    }


@router.get("/")
async def list_articles(limit: int = 30):
    """获取文章列表。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, source, word_count, new_word_count, reading_time_seconds, created_at "
            "FROM articles ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "source": r["source"],
            "word_count": r["word_count"],
            "new_word_count": r["new_word_count"],
            "reading_time_seconds": r["reading_time_seconds"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("/{article_id}")
async def get_article(article_id: int):
    """获取文章详情。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文章不存在")
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "source": row["source"],
        "word_count": row["word_count"],
        "new_word_count": row["new_word_count"],
        "target_words": json.loads(row["target_words"]) if row["target_words"] else [],
        "difficulty_score": row["difficulty_score"],
        "reading_time_seconds": row["reading_time_seconds"],
        "created_at": row["created_at"],
    }


@router.get("/{article_id}/details")
async def get_article_details(article_id: int):
    """获取文章增强详情：高频词、目标生词、真题相似度。"""
    from app.services.similarity import get_article_details
    details = get_article_details(article_id)
    if not details:
        raise HTTPException(status_code=404, detail="文章不存在")
    return details


@router.delete("/{article_id}")
async def delete_article(article_id: int):
    """删除文章。"""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="文章不存在")
        # 先删除关联数据，再删除文章本身（避免外键约束失败）
        conn.execute("DELETE FROM highlights WHERE article_id = ?", (article_id,))
        conn.execute("DELETE FROM reading_sessions WHERE article_id = ?", (article_id,))
        # ⚠️ 遇见记录也必须一起清。
        # 重遇次数按 COUNT(DISTINCT article_id) 计算，而统计口径包含 action='lookup' ——
        # 文章删了、记录还在，就成了「幽灵文章」：明明只剩 5 篇，界面却显示跨 6 篇。
        # 实测就这么虚高过（journal 多算 1 篇）。
        conn.execute("DELETE FROM word_encounters WHERE article_id = ?", (article_id,))
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    return {"status": "ok", "message": "文章已删除（含它产生的遇见记录）"}


@router.get("/{article_id}/sentences")
async def article_sentence_analysis(article_id: int):
    """长难句拆解 —— 阅读器的「这句读不懂」辅助。"""
    from app.services import sentence_analysis

    with get_db() as conn:
        row = conn.execute(
            "SELECT content FROM articles WHERE id = ?", (article_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文章不存在")
    return {"sentences": sentence_analysis.analyze_text(row["content"] or "")}
