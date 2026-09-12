"""智能词汇测试服务。
基于用户阅读数据的权重抽样，优先出不会的词。
支持多题型：单词释义、中文选单词、词形变化、短语搭配。
"""
import json
import random
import time

from app.database import get_db


def calculate_word_weight(word):
    """计算一个词的出题权重。权重越高，越容易被选中。"""
    weight = 1.0

    # 生词本：高权重
    if word['status'] == 'learning':
        weight *= 3.0

    # 测验词（查词≥2次）：高权重
    if word['status'] == 'target':
        weight *= 3.0

    # 查词次数：查得越多越重要
    lookup_count = word['lookup_count'] or 0
    weight *= (1 + lookup_count * 0.5)

    # 测试错误次数：错得越多越要练
    wrong_count = word['wrong_count'] or 0
    weight *= (1 + wrong_count * 1.0)

    # 测试正确次数：对得多降低权重
    correct_count = word['correct_count'] or 0
    if correct_count > 0:
        weight *= max(0.3, 1.0 - correct_count * 0.1)

    # FSRS 记忆强度：越低越要复习
    stability = word['srs_stability'] or 0
    if stability > 0 and stability < 10:
        weight *= 2.0
    elif stability >= 10 and stability < 30:
        weight *= 1.5

    # 最近测试过的词降低权重（避免连续重复）
    last_test_at = word['last_test_at'] or 0
    if last_test_at > 0:
        days_since = (time.time() - last_test_at) / 86400
        if days_since < 1:
            weight *= 0.5

    return weight


def weighted_sample(words, count):
    """按权重抽样。"""
    if not words:
        return []

    weights = [calculate_word_weight(w) for w in words]
    total = sum(weights)

    if total == 0:
        return random.sample(words, min(count, len(words)))

    result = []
    available = list(zip(words, weights))

    while len(result) < count and available:
        r = random.uniform(0, total)
        cumulative = 0
        for i, (word, weight) in enumerate(available):
            cumulative += weight
            if cumulative >= r:
                result.append(word)
                total -= weight
                available.pop(i)
                break
        else:
            # 浮点误差，选最后一个
            if available:
                result.append(available[-1][0])
                total -= available[-1][1]
                available.pop()

    return result


def generate_smart_test(count=20, source='adaptive'):
    """生成智能测试。
    source:
      - adaptive: 自适应（基于用户数据权重抽样，默认）
      - vocab: 仅生词本
      - target: 仅测验词
      - all: 全部词库（随机）
    """
    with get_db() as conn:
        # 获取候选词
        if source == 'adaptive':
            # 自适应：生词本 + 测验词 + 查词≥1的词 + CET4高频词补充
            candidates = conn.execute("""
                SELECT id, text, meaning, exchange, status, lookup_count,
                       wrong_count, correct_count, srs_stability, last_test_at, level
                FROM words
                WHERE meaning IS NOT NULL AND meaning != ''
                  AND (status IN ('learning', 'target')
                       OR lookup_count >= 1
                       OR level = 'CET4')
                ORDER BY lookup_count DESC, wrong_count DESC
                LIMIT 500
            """).fetchall()
        elif source == 'vocab':
            candidates = conn.execute("""
                SELECT id, text, meaning, exchange, status, lookup_count,
                       wrong_count, correct_count, srs_stability, last_test_at, level
                FROM words
                WHERE status = 'learning' AND meaning IS NOT NULL AND meaning != ''
                LIMIT 500
            """).fetchall()
        elif source == 'target':
            candidates = conn.execute("""
                SELECT id, text, meaning, exchange, status, lookup_count,
                       wrong_count, correct_count, srs_stability, last_test_at, level
                FROM words
                WHERE status = 'target' AND meaning IS NOT NULL AND meaning != ''
                LIMIT 500
            """).fetchall()
        else:
            candidates = conn.execute("""
                SELECT id, text, meaning, exchange, status, lookup_count,
                       wrong_count, correct_count, srs_stability, last_test_at, level
                FROM words
                WHERE meaning IS NOT NULL AND meaning != ''
                ORDER BY RANDOM() LIMIT 500
            """).fetchall()

        # 干扰项词库
        all_words = conn.execute("""
            SELECT text, meaning FROM words
            WHERE meaning IS NOT NULL AND meaning != ''
            ORDER BY RANDOM() LIMIT 200
        """).fetchall()

        # 短语库
        phrases = conn.execute("""
            SELECT text, frequency FROM phrases
            WHERE frequency >= 3 ORDER BY frequency DESC LIMIT 100
        """).fetchall()

    # 按权重抽样
    selected = weighted_sample(candidates, count)

    # 生成题目
    result = []
    for word in selected:
        question_type = choose_question_type(word)
        question = generate_question(word, question_type, all_words, phrases)
        if question:
            result.append(question)

    return result


def choose_question_type(word):
    """选择题型。"""
    has_exchange = word['exchange'] and word['exchange'] != 'null'

    # 70% 单词释义，15% 中文选单词，10% 词形变化，5% 短语
    r = random.random()
    if r < 0.7:
        return 'word_to_meaning'
    elif r < 0.85:
        return 'meaning_to_word'
    elif r < 0.95 and has_exchange:
        return 'word_form'
    else:
        return 'word_to_meaning'


def generate_question(word, question_type, all_words, phrases):
    """生成一道题。"""
    meaning = word['meaning']
    text = word['text']

    if question_type == 'word_to_meaning':
        # 英文单词选中文释义
        distractors = random.sample(
            [w for w in all_words if w['text'] != text],
            min(3, len(all_words) - 1)
        )
        options = [meaning] + [d['meaning'] for d in distractors]
        random.shuffle(options)
        return {
            'word_id': word['id'],
            'word': text,
            'type': 'word_to_meaning',
            'question': f"选择 \"{text}\" 的正确释义",
            'options': options,
            'answer': meaning,
        }

    elif question_type == 'meaning_to_word':
        # 中文释义选英文单词
        distractors = random.sample(
            [w for w in all_words if w['text'] != text],
            min(3, len(all_words) - 1)
        )
        options = [text] + [d['text'] for d in distractors]
        random.shuffle(options)
        return {
            'word_id': word['id'],
            'word': text,
            'type': 'meaning_to_word',
            'question': f"选择释义为 \"{meaning[:30]}\" 的单词",
            'options': options,
            'answer': text,
        }

    elif question_type == 'word_form':
        # 词形变化题
        try:
            exchange = json.loads(word['exchange']) if isinstance(word['exchange'], str) else word['exchange']
        except Exception:
            exchange = {}

        if not exchange:
            return None

        # 选一个词形变化类型
        form_types = [k for k in exchange.keys() if k != 'lemma']
        if not form_types:
            return None

        form_type = random.choice(form_types)
        form_word = exchange[form_type]

        type_names = {
            'plural': '复数形式',
            'ing': '现在分词',
            'past': '过去式',
            'done': '过去分词',
            'er': '比较级',
            'est': '最高级',
            'third': '第三人称单数',
        }
        type_name = type_names.get(form_type, form_type)

        distractors = random.sample(
            [w for w in all_words if w['text'] != form_word],
            min(3, len(all_words) - 1)
        )
        options = [form_word] + [d['text'] for d in distractors]
        random.shuffle(options)

        return {
            'word_id': word['id'],
            'word': text,
            'type': 'word_form',
            'question': f"\"{text}\" 的{type_name}是",
            'options': options,
            'answer': form_word,
        }

    return None


def submit_test_result(test_id, word_id, is_correct, reaction_time=None):
    """提交测试结果，更新词的统计数据和 FSRS。"""
    from app.services.srs import update_after_review
    with get_db() as conn:
        now = int(time.time())

        # 更新词的统计
        if is_correct:
            conn.execute("""
                UPDATE words SET correct_count = COALESCE(correct_count, 0) + 1,
                       last_test_at = ?
                WHERE id = ?
            """, (now, word_id))
            # FSRS: 正确 → 良好/简单（根据反应时间）
            if reaction_time and reaction_time < 3000:
                rating = 4  # 简单（反应快）
            else:
                rating = 3  # 良好
        else:
            conn.execute("""
                UPDATE words SET wrong_count = COALESCE(wrong_count, 0) + 1,
                       last_test_at = ?
                WHERE id = ?
            """, (now, word_id))

            # 错误的词自动加入生词本（如果还没在）
            word = conn.execute("SELECT status FROM words WHERE id = ?", (word_id,)).fetchone()
            if word and word['status'] not in ('learning', 'known'):
                conn.execute("UPDATE words SET status = 'learning' WHERE id = ?", (word_id,))
            # FSRS: 错误 → 忘记/困难
            if reaction_time and reaction_time > 10000:
                rating = 1  # 忘记（反应慢还错）
            else:
                rating = 2  # 困难

        # 更新 FSRS
        try:
            update_after_review(word_id, rating, conn)
        except Exception as e:
            # FSRS 更新失败不影响主流程
            print(f"FSRS update failed: {e}")

        # 记录题目详情
        conn.execute("""
            INSERT INTO test_questions (test_id, word_id, is_correct, reaction_time, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (test_id, word_id, 1 if is_correct else 0, reaction_time, now))

        conn.commit()


def get_test_report(test_id):
    """获取测试报告。"""
    with get_db() as conn:
        questions = conn.execute("""
            SELECT q.*, w.text as word_text, w.meaning as word_meaning
            FROM test_questions q
            LEFT JOIN words w ON q.word_id = w.id
            WHERE q.test_id = ?
            ORDER BY q.id
        """, (test_id,)).fetchall()

    total = len(questions)
    correct = sum(1 for q in questions if q['is_correct'])
    wrong = total - correct
    accuracy = (correct / total * 100) if total > 0 else 0

    wrong_words = [
        {'word': q['word_text'], 'meaning': q['word_meaning']}
        for q in questions if not q['is_correct']
    ]

    return {
        'test_id': test_id,
        'total': total,
        'correct': correct,
        'wrong': wrong,
        'accuracy': round(accuracy, 1),
        'wrong_words': wrong_words,
    }
