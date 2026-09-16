"""掌握度模型与选词算法的测试。

守的是文献给出的那几条硬约束，不是我自己拍的数字：
- Pellicer-Sánchez (2015)：8 次遇见 → 词形识别 86% / 意义识别 75% / **回忆仅 55%**
  ⇒ 反复遇见能建立「认得出来」，但撑不起「想得起来」
- van den Broek (2022)：即使在语境中理解了，也不一定能回忆出来
  ⇒ 等级必须由**证据类型**决定，不能只看概率
"""
import pytest

from app.database import get_db
from app.services import mastery, placement, selection


@pytest.fixture()
def env():
    """造一组词 + 分档掌握率，并清理证据表。"""
    with get_db() as db:
        for t in ("word_encounters", "highlights", "reading_sessions",
                  "test_questions", "articles", "words",
                  "placement_bands", "placement_runs"):
            db.execute(f"DELETE FROM {t}")
        now = 0
        # 一个「档位先验很高」的词（frq 500 → 前 1000 档，默认假设下 rate=1.0）
        db.executemany(
            """INSERT INTO words (id, lemma, text, meaning, level, frequency, status,
                                  frq, bnc, lookup_count, correct_count, wrong_count,
                                  mastered, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (1, "common", "common", "常见的", "CET4", 0, "new", 500, 0, 0, 0, 0, 0, now, now),
                (2, "mid", "mid", "中频", "CET4", 0, "new", 2500, 0, 0, 0, 0, 0, now, now),
                (3, "rare", "rare", "生僻", "CET4", 0, "new", 15000, 0, 0, 0, 0, 0, now, now),
                (4, "looked", "looked", "被查过的", "CET4", 0, "new", 600, 0, 1, 0, 0, 0, now, now),
                (5, "tested", "tested", "答对过的", "CET4", 0, "new", 3000, 0, 0, 2, 0, 0, now, now),
            ],
        )
        db.execute("INSERT INTO articles (id, title, content, source, created_at) VALUES (1,'a','x','ai',0)")
        # 默认 2500 词的分档
        cur = db.execute(
            "INSERT INTO placement_runs (vocab_estimate, is_lower_bound, false_alarm, answered, source, created_at) "
            "VALUES (2500,0,0,0,'default',0)")
        run_id = cur.lastrowid
        remaining = 2500.0
        rows = []
        for lo, hi in placement.BANDS:
            size = hi - lo + 1
            rate = max(0.0, min(1.0, remaining / size))
            remaining -= rate * size
            rows.append((run_id, lo, hi, size, 0, 0, round(rate, 4)))
        db.executemany(
            "INSERT INTO placement_bands (run_id, band_lo, band_hi, band_size, sampled, known, rate) "
            "VALUES (?,?,?,?,?,?,?)", rows)
    yield
    with get_db() as db:
        for t in ("word_encounters", "articles", "words",
                  "placement_bands", "placement_runs"):
            db.execute(f"DELETE FROM {t}")


def _see(word_id: int, articles: list[int], lookup_in: list[int] | None = None) -> None:
    """让某个词在多篇文章里「被遇见」，可选在部分文章里查了它。"""
    lookup_in = lookup_in or []
    with get_db() as db:
        for a in articles:
            db.execute(
                "INSERT OR IGNORE INTO word_encounters (word_id, article_id, context, action, created_at) "
                "VALUES (?,?,'','seen',0)", (word_id, a))
        for a in lookup_in:
            db.execute(
                "INSERT INTO word_encounters (word_id, article_id, context, action, created_at) "
                "VALUES (?,?,'','lookup',0)", (word_id, a))


# ---------------------------------------------------------------- 等级规则

def test_prior_is_clamped_so_evidence_can_override(env):
    """⚠️ 回归：先验必须封顶。

    默认假设给最高频档 rate=1.0，logit 高达 9.2 —— 不封顶的话先验会压死一切证据，
    一个用户明确查过的词也会被判成「识别 100%」。
    """
    _see(4, [1], lookup_in=[1])
    mastery.refresh([4])
    e = mastery.explain(4)
    assert e["p_recognize"] < 0.95, "查过的词不应该有接近 100% 的识别度"
    assert e["level"] != "recalled"


def test_test_evidence_is_required_for_recalled(env):
    """反复遇见撑不起「回忆」——Pellicer-Sánchez：8 次遇见后回忆仅 55%。

    所以只靠遇见（哪怕 12 次）最多判到 recognized，必须真有答题证据才给 recalled。
    """
    _see(3, list(range(1, 13)))          # 12 次干净遇见
    mastery.refresh([3])
    e = mastery.explain(3)
    assert e["clean_exposures"] == 12
    assert e["level"] == "recognized", "纯遇见不该判成 recalled"
    assert e["evidence"]["测试答对"] == 0


def test_four_exposures_reach_recognized(env):
    """依据：Pellicer-Sánchez 说 3–4 次遇见后阅读速度显著变快（词形识别形成）。"""
    _see(3, [1, 2, 3])
    mastery.refresh([3])
    assert mastery.explain(3)["level"] == "seen"      # 3 次还差一点
    _see(3, [4])
    mastery.refresh([3])
    assert mastery.explain(3)["level"] == "recognized"  # 第 4 次达到


def test_test_correct_gives_recalled(env):
    _see(5, [1])
    mastery.refresh([5])
    assert mastery.explain(5)["level"] == "recalled"


def test_single_correct_is_not_enough_for_recalled(env):
    """四选一有 25% 蒙对率，一次答对不足以判定「能想起来」。"""
    with get_db() as db:
        db.execute("UPDATE words SET correct_count = 1 WHERE id = 3")
    _see(3, [1])
    mastery.refresh([3])
    assert mastery.explain(3)["level"] != "recalled"


def test_lookup_without_exposure_still_counts_as_seen(env):
    """用户查了一个没被记录进文章的词，也算接触过。"""
    with get_db() as db:
        db.execute("UPDATE words SET lookup_count = 1 WHERE id = 2")
    mastery.refresh([2])
    e = mastery.explain(2)
    assert e["level"] == "seen"
    assert e["evidence"]["查词次数"] == 1


def test_clean_exposure_excludes_looked_up_articles(env):
    """「干净遇见」= 见过但**没查**。查过的那几篇不算干净遇见。

    这正是用户提的那个信号：没查过才是掌握的证据。
    """
    _see(3, [1, 2, 3, 4], lookup_in=[1, 2])
    mastery.refresh([3])
    e = mastery.explain(3)
    assert e["clean_exposures"] == 2
    assert e["evidence"]["查词次数"] == 2
    assert e["level"] == "seen"          # 2 次干净遇见 < 4


def test_declared_known_is_recognized_not_recalled(env):
    """定级测试里自陈「认识」是识别级证据，不等于能回忆（van den Broek 2022）。"""
    with get_db() as db:
        db.execute("UPDATE words SET mastered = 1 WHERE id = 3")
    mastery.refresh([3])
    assert mastery.explain(3)["level"] == "recognized"


def test_declared_unknown_overrides_high_band_prior(env):
    """自陈「不认识」必须压过「这个词属于我认识的档位」这个先验。"""
    with get_db() as db:
        db.execute("UPDATE words SET mastered = 2 WHERE id = 1")   # common，前 1000 档
    mastery.refresh([1])
    e = mastery.explain(1)
    assert e["p_recognize"] < 0.5
    assert e["level"] != "recognized"


def test_refresh_only_touches_words_with_evidence(env):
    """只重算**有证据**的词 —— 库里有 18 万词，逐个算是不可接受的。

    fixture 里只有 `looked`(查过 1 次) 与 `tested`(答对 2 次) 带证据，
    另外 3 个词（common/mid/rare）没有任何证据，不应被处理。
    """
    r = mastery.refresh()
    assert r["processed"] == 2, f"应只处理 2 个有证据的词，实际 {r['processed']}"


# ---------------------------------------------------------------- 选词

def test_selection_excludes_recalled(env):
    _see(5, [1])                      # 答对 2 次的词 → recalled
    mastery.refresh()
    picked = selection.select_new_words(200, exam="cet4")
    with get_db() as db:
        ids = {r[0] for r in db.execute("SELECT id FROM words WHERE lower(text) IN (%s)"
                                        % ",".join("?" * len(picked)), tuple(picked))}
    assert 5 not in ids, "已能回忆的词不该再被选为生词"


def test_selection_prefers_reentry_gap(env):
    """见过的词优先再遇见（重遇缺口），而不是一直教全新的词。"""
    _see(3, [1, 2])                   # rare 见过 2 次
    mastery.refresh()
    picked = selection.select_new_words(1, exam="cet4")
    assert picked and picked[0].lower() == "rare"


def test_selection_respects_target_exam(env):
    """目标考试不同，选出的词必须不同。"""
    with get_db() as db:
        db.execute("UPDATE words SET tags='cet4' WHERE id=2")
        db.execute("UPDATE words SET tags='ielts' WHERE id=3")
    mastery.refresh()
    cet4 = selection.select_new_words(50, exam="cet4")
    ielts = selection.select_new_words(50, exam="ielts")
    # 第 3 层（全新词）应按考试词表分流
    with get_db() as db:
        def tiers(words):
            if not words:
                return set()
            ph = ",".join("?" * len(words))
            return {r[0] for r in db.execute(
                f"SELECT id FROM words WHERE lower(text) IN ({ph})", tuple(w.lower() for w in words))}
    # 若两考试候选都足够，tier3 部分应当不同
    assert cet4 != ielts or not cet4


def test_selection_dedupes_case_variants(env):
    """库里有 every / Every 这类大小写重复条目，同篇不能埋两个「同一个词」。"""
    with get_db() as db:
        db.execute(
            "INSERT INTO words (id, lemma, text, meaning, level, frequency, status, frq, created_at, updated_at) "
            "VALUES (99,'every','Every','每一个','CET4',0,'new',800,0,0)")
        db.execute("UPDATE words SET frq=800 WHERE id=1")
        db.execute("UPDATE words SET text='every' WHERE id=1")
    mastery.refresh()
    picked = [w.lower() for w in selection.select_new_words(200, exam="cet4")]
    assert picked.count("every") <= 1


def test_set_target_exam_rejects_unknown(env):
    assert selection.set_target_exam("not-an-exam") is False
    assert selection.set_target_exam("cet6") is True
    assert selection.target_exam() == "cet6"


# ---------------------------------------------------------------- 删除语义

def test_delete_article_cascades_encounters(env):
    """⚠️ 回归：删文章必须连它产生的遇见记录一起删。

    重遇次数按 COUNT(DISTINCT article_id) 算、口径含 action='lookup' ——
    文章删了记录还在，就成了「幽灵文章」：明明只剩 5 篇，界面显示跨 6 篇。
    """
    import time as _t

    from app.api.articles import delete_article
    from app.services import encounter

    _see(3, [1, 2, 3])
    with get_db() as db:
        db.execute("INSERT INTO articles (id,title,content,source,created_at) VALUES (2,'b','x','ai',0)")
        db.execute("INSERT INTO articles (id,title,content,source,created_at) VALUES (3,'c','x','ai',0)")
    mastery.refresh([3])
    before = {s["text"]: s["articles"] for s in encounter.reentry_stats(only_active=False)}
    assert before["rare"] == 3

    import asyncio
    asyncio.run(delete_article(2))

    after = {s["text"]: s["articles"] for s in encounter.reentry_stats(only_active=False)}
    assert after["rare"] == 2, f"删文章后重遇次数应回退到 2，实际 {after['rare']}"
    with get_db() as db:
        left = db.execute("SELECT COUNT(*) FROM word_encounters WHERE article_id=2").fetchone()[0]
    assert left == 0, "不应留下指向已删文章的遇见记录"


def test_delete_material_clears_profile_reference(env):
    """删材料要清掉画像里的引用，否则画像指向不存在的材料。"""
    import json as _json

    from app.services.materials import delete_material

    with get_db() as db:
        db.execute("INSERT INTO materials (id,title,source_type,parse_status,created_at,updated_at) "
                   "VALUES (7,'m','paste','parsed',0,0)")
        db.execute("INSERT INTO style_profiles (id,name,material_ids,params,sample_count,is_active,created_at) "
                   "VALUES (1,'p','[7, 8]','{}',0,0,0)")
    assert delete_material(7) is True
    with get_db() as db:
        refs = _json.loads(db.execute("SELECT material_ids FROM style_profiles WHERE id=1").fetchone()[0])
    assert refs == [8], f"应只剩 [8]，实际 {refs}"
