"""FSRS 服务测试（自研算法，基于临时数据库）。"""
import time

from app.database import get_db
from app.services import srs


def _new_word(conn, text="testword"):
    now = int(time.time())
    cur = conn.execute(
        "INSERT INTO words (lemma, text, type, status, created_at, updated_at) "
        "VALUES (?, ?, 'word', 'learning', ?, ?)",
        (text, text, now, now),
    )
    return cur.lastrowid


def test_init_srs_sets_baseline(conn):
    word_id = _new_word(conn)
    srs.init_srs(word_id, conn)
    row = conn.execute(
        "SELECT srs_stability, srs_difficulty, srs_reps FROM words WHERE id=?", (word_id,)
    ).fetchone()
    assert row["srs_stability"] == 1.0
    assert row["srs_reps"] == 0


def test_good_review_increases_stability(conn):
    word_id = _new_word(conn)
    srs.init_srs(word_id, conn)
    result = srs.update_after_review(word_id, 3, conn)
    assert result["stability"] > 1.0
    assert result["interval"] >= 1


def test_forget_resets_interval_to_one_day(conn):
    word_id = _new_word(conn)
    srs.init_srs(word_id, conn)
    srs.update_after_review(word_id, 3, conn)
    result = srs.update_after_review(word_id, 1, conn)
    assert result["interval"] == 1


def test_easy_review_grows_faster_than_good(conn):
    easy_word = _new_word(conn, "easyword")
    good_word = _new_word(conn, "goodword")
    srs.init_srs(easy_word, conn)
    srs.init_srs(good_word, conn)
    easy = srs.update_after_review(easy_word, 4, conn)
    good = srs.update_after_review(good_word, 3, conn)
    assert easy["stability"] > good["stability"]


def test_review_stats_shape(conn):
    stats = srs.get_review_stats(conn)
    assert set(stats) == {"total", "due_today", "avg_stability"}
