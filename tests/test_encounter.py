"""重遇机制测试。

守两条要害：
1. **同一篇文章重复记录不能灌水** —— 同一篇看 12 遍不算重遇
2. **换一篇文章才算一次** —— 方法要求的是「变化的语境」
"""
import pytest

from app.database import get_db
from app.services import encounter, lexicon


def _clear(db) -> None:
    """按外键依赖顺序清表（先删引用方）。

    teardown 也必须清：否则残留的 word_encounters 会让后续测试
    （conftest 的 conn fixture 会 DELETE FROM words）撞外键约束。
    """
    for t in ("word_encounters", "highlights", "reading_sessions",
              "test_questions", "articles", "words",
              "placement_bands", "placement_runs"):
        try:
            db.execute(f"DELETE FROM {t}")
        except Exception:
            pass


@pytest.fixture()
def seeded():
    """造几个学习词 + 两篇文章。"""
    from app.database import get_db

    with get_db() as db:
        _clear(db)
        now = 0
        db.executemany(
            """INSERT INTO words (id, lemma, text, meaning, level, frequency, status,
                                  exchange, mastered, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (1, "student", "student", "学生", "CET4", 0, "learning",
                 '{"s": "students", "lemma": "student"}', 0, now, now),
                (2, "abandon", "abandon", "放弃", "CET4", 0, "learning",
                 '{"past": "abandoned", "ing": "abandoning"}', 0, now, now),
                (3, "harvest", "harvest", "收获", "CET4", 0, "new",
                 None, 1, now, now),
                (4, "unrelated", "unrelated", "无关词", "CET4", 0, "new",
                 None, 0, now, now),
            ],
        )
        db.executemany(
            "INSERT INTO articles (id, title, content, source, word_count, target_words, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [(11, "A1", "students abandon things", "ai", 3, '["student"]', now),
             (12, "A2", "another students story", "ai", 3, '["student"]', now)],
        )
    lexicon.invalidate()
    yield
    with get_db() as db:
        _clear(db)
    lexicon.invalidate()


def test_record_is_idempotent_within_same_article(seeded):
    """同一篇文章记两次，只算一次遇见 —— 这是「变化语境」的前提。"""
    r1 = encounter.record_article(11, "students abandon things", ["student"])
    assert r1["recorded"] > 0
    r2 = encounter.record_article(11, "students abandon things", ["student"])
    assert r2["recorded"] == 0, "重复记录同一篇不应产生新行"

    stats = {s["text"]: s for s in encounter.reentry_stats(only_active=False)}
    assert stats["student"]["articles"] == 1


def test_second_article_counts_as_second_encounter(seeded):
    """换一篇文章才 +1。"""
    encounter.record_article(11, "students abandon things", ["student"])
    encounter.record_article(12, "another students story", ["student"])
    stats = {s["text"]: s for s in encounter.reentry_stats(only_active=False)}
    assert stats["student"]["articles"] == 2
    assert stats["student"]["remaining"] == encounter.TARGET_ENCOUNTERS - 2


def test_records_active_vocabulary_found_in_content(seeded):
    """正文里出现的**已有学习词**也要记 —— 这才是真正的「在新语境中重遇」。"""
    # 目标词只有 student，但正文里有 abandoned（abandon 的屈折形式）
    encounter.record_article(11, "students abandoned things", ["student"])
    stats = {s["text"]: s for s in encounter.reentry_stats(only_active=False)}
    assert "abandon" in stats, "屈折形式 abandoned 应还原成 abandon 并记录"


def test_ignores_words_not_being_learned(seeded):
    """没在学的词不记（对它们谈「重遇」没有意义），owned=0 的无关词应被跳过。"""
    encounter.record_article(11, "students unrelated things", ["student"])
    stats = {s["text"]: s for s in encounter.reentry_stats(only_active=False)}
    assert "unrelated" not in stats


def test_mastered_words_are_tracked(seeded):
    """已掌握但出现在新语境里的词同样值得统计。"""
    encounter.record_article(11, "harvest students", [])
    stats = {s["text"]: s for s in encounter.reentry_stats(only_active=False)}
    assert "harvest" in stats


def test_progress_and_done_threshold(seeded):
    for aid in range(11, 11 + encounter.TARGET_ENCOUNTERS):
        encounter.record_article(aid, "students here", ["student"])
    stats = {s["text"]: s for s in encounter.reentry_stats(only_active=False)}
    s = stats["student"]
    assert s["articles"] == encounter.TARGET_ENCOUNTERS
    assert s["done"] is True
    assert s["remaining"] == 0
    assert s["progress"] == 1.0


def test_summary_shape(seeded):
    encounter.record_article(11, "students abandon things", ["student"])
    s = encounter.summary()
    assert s["target"] == encounter.TARGET_ENCOUNTERS
    assert s["tracked"] >= 1
    assert "message" in s


def test_summary_empty(seeded):
    s = encounter.summary()
    assert s["tracked"] == 0
    assert s["done"] == 0


def test_record_with_no_article_id_is_noop(seeded):
    assert encounter.record_article(0, "students", ["student"])["recorded"] == 0


# ---------------------------------------------------------------- 词形还原

def test_lexicon_normalize_handles_contractions(seeded):
    """缩写要拆开，否则最高频的一批词会被当成不认识。"""
    assert lexicon.normalize("students") == "student"
    assert lexicon.normalize("abandoned") == "abandon"
    assert lexicon.normalize("abandoning") == "abandon"


def test_lexicon_normalize_unknown_returns_none(seeded):
    assert lexicon.normalize("zzzznotaword") is None
    assert lexicon.normalize("") is None


def test_only_active_filter_returns_every_tracked_word(seeded):
    """回归：过滤条件必须进 WHERE，不能落到 GROUP BY 后面。

    曾经把 `AND (w.status IN (...))` 直接拼在整条 SQL 末尾，变成
    `GROUP BY w.id AND (...)` —— SQLite 把布尔表达式当成分组键求值，
    所有词被并成 2 组，列表只返回 2 行、计数虚高，且不报任何错。
    """
    # 让 3 个不同的学习词各出现在一篇文章里
    encounter.record_article(11, "students abandon harvest", [])
    tracked = encounter.reentry_stats(only_active=True)
    assert len(tracked) == 3, f"应有 3 个学习词，实际 {len(tracked)}: {[t['text'] for t in tracked]}"
    assert all(t["articles"] == 1 for t in tracked)
    # 列表条数必须与总览口径一致
    assert len(tracked) == encounter.summary()["tracked"]


def test_only_active_false_includes_nonlearning_words(seeded):
    """unrelated 没在学，但它如果被记录过就应出现在 only_active=False 里。"""
    encounter.record_article(11, "students", ["student"])
    with get_db() as db:
        db.execute(
            "INSERT INTO word_encounters (word_id, article_id, context, action, created_at) "
            "VALUES (4, 11, '', 'seen', 0)"
        )
    assert len(encounter.reentry_stats(only_active=True)) == 1
    assert len(encounter.reentry_stats(only_active=False)) == 2
