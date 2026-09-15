"""词汇量定级测试 API。

无状态设计：GET 出题 → 客户端本地作答 → POST 一次性提交评分。
服务器不保存进行中的试卷，省掉一整类「会话过期 / 并发覆盖」问题。
"""
from fastapi import APIRouter, HTTPException

from app.services import placement as pl

router = APIRouter(prefix="/api/placement", tags=["placement"])


@router.get("/items")
def items(per_band: int = pl.ITEMS_PER_BAND, pseudo: int = pl.PSEUDO_COUNT):
    """生成一份测试卷。"""
    return pl.build_items(
        items_per_band=max(3, min(per_band, 60)),
        pseudo_count=max(0, min(pseudo, len(pl.PSEUDO_WORDS))),
    )


@router.post("/submit")
def submit(payload: dict):
    """提交作答，评分并落库。payload: {"answers": [{"text": "...", "known": true}]}"""
    answers = payload.get("answers")
    if not isinstance(answers, list) or not answers:
        raise HTTPException(status_code=400, detail="没有作答内容")
    result = pl.score(answers)
    if not result["bands"]:
        raise HTTPException(status_code=400, detail="作答内容无法匹配到题库，请重新测试")
    result["run_id"] = pl.save_run(result, answers)
    return result


@router.get("/summary")
def summary():
    """最近一次测试结果（没测过返回 has_result: false）。"""
    return pl.summary()
