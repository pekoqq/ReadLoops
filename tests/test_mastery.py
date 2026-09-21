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
    """让某个词在多篇文章里「被遇见」，可选在部分文章里查了它。

    ⚠️ 必须同时维护 `words.encounter_count`（耐久计数器）——
    掌握度判定用的是它，不是数 word_encounters 的行数。
    这样删文章（连带删明细）不会抹掉用户的学习历史。
    """
    lookup_in = lookup_in or []
    fresh = 0
    with get_db() as db:
        for a in articles:
            cur = db.execute(
                "INSERT OR IGNORE INTO word_encounters (word_id, article_id, context, action, created_at) "
                "VALUES (?,?,'','seen',0)", (word_id, a))
            if cur.rowcount:
                fresh += 1
        for a in lookup_in:
            db.execute(
                "INSERT INTO word_encounters (word_id, article_id, context, action, created_at) "
                "VALUES (?,?,'','lookup',0)", (word_id, a))
        if fresh:
            db.execute(
                "UPDATE words SET encounter_count = COALESCE(encounter_count,0) + ? WHERE id = ?",
                (fresh, word_id))


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
    """**低于阈值**的纯遇见撑不起「回忆」——必须真有答题证据。

    Pellicer-Sánchez (2015)：8 次遇见后回忆仅 55%，说明回忆能力随遇见次数增长但很慢。

    ⚠️ 12 次及以上是**例外**（见下一条测试）：方法本身就要求「在不同语境中重遇 ≥12 次」，
    而绝大多数用户不会主动做题（不查词、不加生词本、不测试）。
    如果只保留「答题才能 recalled」，这些人会永远停在 recognized ——
    而 recognized 被选词排除，他们既不被输入也不被考，**词汇量原地打转**。
    """
    _see(3, list(range(1, 12)))          # 11 次干净遇见（差一次到阈值）
    mastery.refresh([3])
    e = mastery.explain(3)
    assert e["clean_exposures"] == 11
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
    """见过的词要能被选上（重遇缺口），而不是一直只教全新的词。"""
    _see(3, [1, 2])                   # rare 见过 2 次
    mastery.refresh()
    picked = [w.lower() for w in selection.select_new_words(3, exam="cet4")]
    assert "rare" in picked, f"见过的 rare 应当入选：{picked}"


def test_looked_up_words_outrank_merely_seen(env):
    """⚠️ 核心回归：**查过的词**必须比**只见过的词**更优先重遇。

    这一层的旧判据是 `clean > 0`（clean = 见过但没查过的次数），
    于是**逻辑是反的**：你查得越多、越记不住，越会被排除出重遇。
    实测 `study`（lookup_count=1、遇见 0 次）永远进不了这一层。

    新判据把查词次数作为最重要的正信号 —— 查词行为直接暴露了哪些词还没习得，
    而「≥12 次不同语境遇见」正是习得的前提（Pellicer-Sánchez 2015）。
    """
    _see(3, [1, 2])                   # rare 只见过 2 次（fixture 里 looked 有 lookup_count=1）
    mastery.refresh()
    picked = [w.lower() for w in selection.select_new_words(3, exam="cet4")]
    assert "looked" in picked and "rare" in picked, f"两者都应入选：{picked}"
    assert picked.index("looked") < picked.index("rare"), (
        f"查过的 looked 应排在只见过的 rare 之前：{picked}")


def test_selection_respects_target_exam(env):
    """目标考试不同，选出的词必须不同。

    ⚠️ 选词会**排除已知集**（词频 ≤ 词汇量估计的词 + 掌握度 recalled/recognized 的词），
    所以这个测试必须自己造两个「已知集之外、m_level=unknown、带考试标签」的词，
    否则两边都只剩 fixture 里的 rare，测不出分流。
    """
    with get_db() as db:
        db.executemany(
            """INSERT INTO words (id, lemma, text, meaning, level, status, frq, bnc,
                                  created_at, updated_at)
               VALUES (?,?,?,'释义','CET4','new',?,0,0,0)""",
            [(801, "cet4only", "cet4only", 6000),
             (802, "ieltsonly", "ieltsonly", 6100)],
        )
        db.execute("UPDATE words SET tags='cet4' WHERE id=801")
        db.execute("UPDATE words SET tags='ielts' WHERE id=802")
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
    # 第 3 层按考试词表分流 —— 测的是**顺序**：目标考试自己的词应排在另一考试之前。
    # （候选不够时第 4 层会兜底把两边的词都补进来，所以不能断言"不包含"。）
    c4 = [w.lower() for w in cet4]
    i6 = [w.lower() for w in ielts]
    assert "cet4only" in c4 and "ieltsonly" in c4, f"cet4 候选不足：{c4}"
    assert "ieltsonly" in i6 and "cet4only" in i6, f"ielts 候选不足：{i6}"
    assert c4.index("cet4only") < c4.index("ieltsonly"), (
        f"cet4 目标下 cet4only 应排在 ieltsonly 之前：{c4}")
    assert i6.index("ieltsonly") < i6.index("cet4only"), (
        f"ielts 目标下 ieltsonly 应排在 cet4only 之前：{i6}")


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


# ---------------------------------------------------------------- 短语与单词同等对待

def test_phrases_are_selected_from_their_own_pool(env):
    """短语必须走和单词相同的分层逻辑，且**不共享候选池**。

    视频里「短语和单词同等重要」的意思是各自都有位置，
    而不是让短语去抢单词的名额。
    """
    with get_db() as db:
        db.executemany(
            """INSERT INTO words (id, lemma, text, type, meaning, level, frequency, status,
                                  frq, created_at, updated_at)
               VALUES (?,?,?,'phrase',? ,? ,?,'new',0,0,0)""",
            [(10, "high school", "high school", "中学", "CET4", 30),
             (11, "long term", "long term", "长期的", "CET4", 25),
             (12, "years ago", "years ago", "多年前", "CET4", 20),
             (13, "s and s", "s and s", "释义", "CET4", 3)],   # 低于频次下限，应被挡掉
        )
        db.execute("UPDATE words SET type='word' WHERE text='rare'")
    mastery.refresh()
    picked = selection.select_new_words(5, exam="cet4", kind="phrase")
    assert picked, "应当能选出短语"
    assert all(" " in w for w in picked), f"短语池里不该出现单词：{picked}"
    assert "s and s" not in picked, "频次低于下限的噪声短语应被过滤"


def test_phrase_ranking_is_by_frequency_descending(env):
    """⚠️ 回归：短语的 frequency 是**次数**，单词的 frq 是**排名**，二者语义相反。

    曾经用同一个 `ORDER BY rank ASC`，导致短语优先选到**最低频**的垃圾
    （实测挑出 `s and s`、`one example` 这种高频词序列而非固定搭配）。
    """
    with get_db() as db:
        db.executemany(
            """INSERT INTO words (id, lemma, text, type, meaning, level, frequency, status,
                                  frq, created_at, updated_at)
               VALUES (?,?,?,'phrase',? ,? ,?,'new',0,0,0)""",
            [(20, "high school", "high school", "中学", "CET4", 33),
             (21, "medium term", "medium term", "中期", "CET4", 15),
             (22, "low term", "low term", "低期", "CET4", 5)],
        )
    mastery.refresh()
    picked = selection.select_new_words(3, exam="cet4", kind="phrase")
    assert picked[0] == "high school", f"应当先选最高频的短语，实际 {picked}"


def test_phrases_and_words_do_not_share_quota(env):
    """一次挑选应当同时给出单词与短语，互不挤占。"""
    with get_db() as db:
        db.executemany(
            """INSERT INTO words (id, lemma, text, type, meaning, level, frequency, status,
                                  frq, created_at, updated_at)
               VALUES (?,?,?,?,'释义','CET4',?, 'new',?,0,0)""",
            [(30, "alpha", "alpha", "word", 0, 500),
             (31, "beta", "beta", "word", 0, 600),
             (32, "high school", "high school", "phrase", 30, 0)],
        )
    mastery.refresh()
    r = selection.select_targets(word_count=5, phrase_count=2, exam="cet4")
    assert r["words"] and all(" " not in w for w in r["words"])
    assert r["phrases"] and all(" " in p for p in r["phrases"])


def test_phrase_encounter_is_tracked_like_words(env):
    """短语并入 words 表后，重遇与掌握度机制自动复用，无需另起一套。"""
    from app.services import encounter

    with get_db() as db:
        db.execute(
            """INSERT INTO words (id, lemma, text, type, meaning, level, frequency, status,
                                  frq, created_at, updated_at)
               VALUES (40,'long term','long term','phrase','','CET4',30,'new',0,0,0)""")
    encounter.record_article(1, "", ["long term"])
    mastery.refresh([40])
    e = mastery.explain(40)
    assert e["evidence"]["干净遇见（见过但没查）"] == 1
    assert e["level"] == "seen"


def test_phrases_without_dictionary_source_are_excluded(env):
    """⚠️ 关键约束：查不到词典来源的短语**不能被当作词汇单位去教**。

    `young people` / `new study` 这类只是碰巧连在一起的高频词，任何词典都没有它们的
    条目 —— 硬造释义就是「凭空创造」，学习者分辨不出来。所以选词时必须排除。
    """
    with get_db() as db:
        db.executemany(
            """INSERT INTO words (id, lemma, text, type, meaning, level, frequency, status,
                                  frq, meaning_source, created_at, updated_at)
               VALUES (?,?,?,'phrase',?,'CET4',?,'new',0,?,0,0)""",
            [(50, "high school", "high school", "中学", 30, "ecdict"),
             (51, "young people", "young people", "", 28, None),      # 无来源
             (52, "new study", "new study", "", 26, None)],          # 无来源
        )
    mastery.refresh()
    picked = selection.select_new_words(10, exam="cet4", kind="phrase")
    assert "high school" in picked, "有词典来源的短语应当可选"
    assert "young people" not in picked, "无来源的短语不该被选中"
    assert "new study" not in picked


# ---------------------------------------------------------------- 闭环：recognized → 提取练习

def test_recognized_words_are_testable(env):
    """⚠️ 闭环回归：`recognized` 的词必须能被测试选中。

    此前它们陷入死角：选词算法排除它们（该做提取练习而非继续输入，这是对的），
    但 smart_test 的权重只看 status/查词/对错次数，**完全不知道 m_level** ——
    于是「认得出来但考不出来」的词既不被输入也不被考，永久卡在中间。
    """
    from app.services.smart_test import generate_smart_test

    with get_db() as db:
        # 造 4 个 recognized 的词（有释义，可出题）
        db.executemany(
            """INSERT INTO words (id, lemma, text, type, meaning, level, frequency, status,
                                  frq, m_level, created_at, updated_at)
               VALUES (?,?,?,'word',?,'CET4',0,'new',?, 'recognized',0,0)""",
            [(60, "alpha", "alpha", "甲", 500), (61, "beta", "beta", "乙", 600),
             (62, "gamma", "gamma", "丙", 700), (63, "delta", "delta", "丁", 800)],
        )
    qs = generate_smart_test(count=4, source="recognized")
    assert len(qs) == 4, f"recognized 源应当能出题，实际 {len(qs)} 道"
    assert all("word" in q for q in qs)


def test_recalled_words_are_downweighted_in_adaptive(env):
    """已经想得起来的词不该占测试题目。"""
    from app.services.smart_test import calculate_word_weight

    base = {"status": "learning", "lookup_count": 0, "wrong_count": 0,
            "correct_count": 0, "srs_stability": 0, "last_test_at": 0,
            "exchange": None, "m_level": "unknown"}
    recognized = calculate_word_weight({**base, "m_level": "recognized"})
    recalled = calculate_word_weight({**base, "m_level": "recalled"})
    unknown = calculate_word_weight({**base, "m_level": "unknown"})
    assert recognized > unknown, "recognized 的词应当比未接触的更该被考"
    assert recalled < unknown, "recalled 的词应当被压低"


def test_smart_test_no_longer_depends_on_phrases_table(env):
    """⚠️ 回归：旧的 phrases 表已废弃，测试出题不能再依赖它。"""
    import inspect

    from app.services import smart_test
    src = inspect.getsource(smart_test)
    assert "FROM phrases" not in src, "smart_test 不应再查询已废弃的 phrases 表"


def test_mastery_survives_article_deletion(env):
    """⚠️ 核心回归：**删除文章不得抹掉学习历史**。

    这是用户提的关键场景：读完就把文章删掉。删文章会（也应当）连带删除它的
    word_encounters 明细，但「我见过这个词 N 次」是**用户的学习历史**，
    不该因为删掉内容而消失。

    此前掌握度直接数 word_encounters 行数，于是：
        读完 → 删文章 → 证据归零 → 掌握度回退 → 系统重新教一遍 → 死循环
    现在改用耐久计数器 words.encounter_count，明细可删、历史仍在。
    """
    _see(3, [1, 2, 3, 4])          # 4 次干净遇见 → recognized
    mastery.refresh([3])
    assert mastery.explain(3)["level"] == "recognized"
    before = mastery.explain(3)["recall"]

    # 模拟「读完把文章都删了」：明细被连带清空
    with get_db() as db:
        db.execute("DELETE FROM word_encounters WHERE word_id=3")
    mastery.refresh()
    after = mastery.explain(3)
    assert after["level"] == "recognized", (
        f"删文章不该让掌握度回退，实际变成 {after['level']}")
    assert after["recall"] == before, "删文章不该改变掌握度得分"


def test_mastery_cleared_only_when_counters_are_gone(env):
    """耐久计数器也被清空时，等级才应当回退。

    与上面相对：删除**明细**是内容生命周期（允许），
    清空**耐久计数器**才是真正的「证据消失」（应当回退）。
    """
    _see(3, [1, 2, 3, 4])
    mastery.refresh([3])
    assert mastery.explain(3)["level"] == "recognized"

    with get_db() as db:
        db.execute("DELETE FROM word_encounters WHERE word_id=3")
        db.execute("UPDATE words SET encounter_count=0, lookup_count=0, "
                   "correct_count=0, wrong_count=0, mastered=0 WHERE id=3")
    mastery.refresh()
    assert mastery.explain(3)["level"] == "unknown", "证据真的没了，等级必须回退"
    with get_db() as db:
        row = db.execute("SELECT m_level, m_recognize, m_recall FROM words WHERE id=3").fetchone()
    assert row["m_level"] == "unknown" and row["m_recognize"] == 0 and row["m_recall"] == 0
