"""FSRS 间隔重复调度服务。

简化版 FSRS：基于记忆稳定性(stability)和难度(difficulty)计算下次复习时间。
参考：https://github.com/open-spaced-repetition/fsrs4anki
"""
import time


def _now():
    return int(time.time())


def init_srs(word_id: int, conn):
    """初始化一个词的 FSRS 参数。"""
    now = _now()
    conn.execute(
        "UPDATE words SET srs_stability=1.0, srs_difficulty=5.0, srs_interval=0, "
        "srs_reps=0, srs_lapses=0, srs_due=?, srs_last_review=? WHERE id=?",
        (now, now, word_id),
    )


def update_after_review(word_id: int, rating: int, conn):
    """复习后更新 FSRS。

    rating: 1=忘记, 2=困难, 3=良好, 4=简单
    """
    row = conn.execute(
        "SELECT srs_stability, srs_difficulty, srs_interval, srs_reps, srs_lapses "
        "FROM words WHERE id=?",
        (word_id,),
    ).fetchone()
    if not row or row["srs_stability"] is None:
        init_srs(word_id, conn)
        row = conn.execute(
            "SELECT srs_stability, srs_difficulty, srs_interval, srs_reps, srs_lapses "
            "FROM words WHERE id=?",
            (word_id,),
        ).fetchone()

    stability = row["srs_stability"] or 1.0
    difficulty = row["srs_difficulty"] or 5.0
    reps = row["srs_reps"] or 0
    lapses = row["srs_lapses"] or 0

    # FSRS 简化算法
    if rating == 1:  # 忘记
        lapses += 1
        stability = max(0.5, stability * 0.4)
        interval = 1  # 1天后复习
        difficulty = min(10, difficulty + 0.5)
    elif rating == 2:  # 困难
        stability = stability * 0.8
        interval = max(1, stability * 0.8)
        difficulty = min(10, difficulty + 0.2)
    elif rating == 3:  # 良好
        stability = stability * 1.2
        interval = stability
        difficulty = max(1, difficulty - 0.1)
    else:  # 简单
        stability = stability * 1.5
        interval = stability * 1.3
        difficulty = max(1, difficulty - 0.3)

    reps += 1
    now = _now()
    due = now + int(interval * 86400)  # 转换为秒

    conn.execute(
        "UPDATE words SET srs_stability=?, srs_difficulty=?, srs_interval=?, "
        "srs_reps=?, srs_lapses=?, srs_due=?, srs_last_review=? WHERE id=?",
        (stability, difficulty, interval, reps, lapses, due, now, word_id),
    )

    return {
        "stability": stability,
        "difficulty": difficulty,
        "interval": interval,
        "next_review": due,
    }


def update_after_reading(word_id: int, looked_up: bool, reading_time_ratio: float, conn):
    """阅读后根据行为更新 FSRS。

    looked_up: 阅读中是否查了这个词
    reading_time_ratio: 实际阅读时间 / 预期阅读时间（>1 读得慢，<1 读得快）
    """
    row = conn.execute(
        "SELECT srs_stability, srs_difficulty, srs_interval, srs_reps "
        "FROM words WHERE id=?",
        (word_id,),
    ).fetchone()
    if not row or row["srs_stability"] is None:
        init_srs(word_id, conn)
        return

    stability = row["srs_stability"] or 1.0
    difficulty = row["srs_difficulty"] or 5.0

    if looked_up:
        # 查词了 → 难度上调，稳定性不变
        difficulty = min(10, difficulty + 0.1)
    else:
        # 没查词 → 稳定性微增
        if reading_time_ratio > 0.5 and reading_time_ratio < 2.0:
            # 阅读速度正常 → 记忆有效
            stability = stability * 1.05

    # 读得太慢 → 难度上调
    if reading_time_ratio > 2.0:
        difficulty = min(10, difficulty + 0.05)

    now = _now()
    conn.execute(
        "UPDATE words SET srs_stability=?, srs_difficulty=?, srs_last_review=? WHERE id=?",
        (stability, difficulty, now, word_id),
    )


def get_due_words(limit=50, conn=None):
    """获取今天到期复习的词。"""
    now = _now()
    rows = conn.execute(
        "SELECT id, text, phonetic, meaning, srs_stability, srs_interval, srs_due "
        "FROM words WHERE status IN ('learning', 'target') "
        "AND srs_due <= ? AND srs_stability IS NOT NULL "
        "ORDER BY srs_due ASC LIMIT ?",
        (now, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_review_stats(conn):
    """获取 FSRS 统计概览。"""
    now = _now()
    total = conn.execute(
        "SELECT COUNT(*) as c FROM words WHERE status IN ('learning', 'target') "
        "AND srs_stability IS NOT NULL"
    ).fetchone()["c"]
    due = conn.execute(
        "SELECT COUNT(*) as c FROM words WHERE status IN ('learning', 'target') "
        "AND srs_due <= ?",
        (now,),
    ).fetchone()["c"]
    avg_stability = conn.execute(
        "SELECT AVG(srs_stability) as a FROM words WHERE status IN ('learning', 'target') "
        "AND srs_stability IS NOT NULL"
    ).fetchone()["a"]
    return {
        "total": total,
        "due_today": due,
        "avg_stability": round(avg_stability or 0, 1),
    }
