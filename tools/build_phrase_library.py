"""从205篇四级真题阅读中提取高频短语，构建短语库。"""
import json
import os
import re
import sqlite3
from collections import Counter

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_PATH = os.path.join(_ROOT, 'data', 'yuedu.db')
CORPUS_PATH = os.getenv('READLOOPS_CORPUS_FILE') or os.path.join(
    _ROOT, '语料库', '真题阅读纯文本', 'all_passages_lazynote.json')

# 停用词（短语不能以这些词开头或结尾）
STOPWORDS = set("""
a an the and or but if then else when while although though because since as
of in on at to for with by from about into over under after before between
is are was were be been being have has had do does did will would could should
may might can shall it its this that these those i you he she we they me him
her us them my your his our their not no so too very just also only own same
such any all each both few more most other some than them what which who whom
whose where why how there here up down out off on again once further
""".split())

def extract_ngrams(text, n=2):
    """提取 n-gram 短语。"""
    words = re.findall(r'[a-zA-Z]+', text.lower())
    ngrams = []
    for i in range(len(words) - n + 1):
        phrase = ' '.join(words[i:i+n])
        # 过滤：不能以停用词开头或结尾，不能全是停用词
        first = words[i]
        last = words[i+n-1]
        if first not in STOPWORDS and last not in STOPWORDS:
            # 检查短语中是否包含实词
            content_words = [w for w in words[i:i+n] if w not in STOPWORDS]
            if len(content_words) >= 1:
                ngrams.append(phrase)
    return ngrams

def main():
    # 读取真题语料
    print("读取真题语料...")
    with open(CORPUS_PATH, 'r', encoding='utf-8') as f:
        passages = json.load(f)
    print(f"共 {len(passages)} 篇文章")

    # 提取短语
    all_phrases = Counter()
    for p in passages:
        text = p.get('text', '')
        for n in [2, 3, 4]:
            ngrams = extract_ngrams(text, n)
            all_phrases.update(ngrams)

    print(f"共提取 {len(all_phrases)} 个不同短语")

    # 过滤：频率 >= 2 的短语
    frequent = {p: c for p, c in all_phrases.items() if c >= 2}
    print(f"频率>=2的短语: {len(frequent)} 个")

    # 按频率排序
    sorted_phrases = sorted(frequent.items(), key=lambda x: x[1], reverse=True)

    # 存入数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    import time
    now = int(time.time())
    inserted = 0

    for phrase, freq in sorted_phrases:
        # 只存频率 >= 3 的短语（避免太稀有的）
        if freq < 3:
            continue
        try:
            # 短语已并入 words（type='phrase'），与单词共享学习状态。
            # 注意：这里**不写释义** —— 释义必须来自真实词典，
            # 由 tools/enrich_phrases.py 补齐，绝不在生成阶段编造。
            cursor.execute(
                """INSERT OR IGNORE INTO words
                   (lemma, text, type, level, frequency, status, source, created_at, updated_at)
                   VALUES (?, ?, 'phrase', 'CET4', ?, 'new', 'phrase_library', ?, ?)""",
                (phrase, phrase, freq, now, now)
            )
            inserted += 1
        except Exception:
            pass

    conn.commit()

    # 统计
    total = cursor.execute("SELECT COUNT(*) FROM words WHERE type='phrase'").fetchone()[0]
    print(f"短语库共 {total} 个短语")

    # 显示 TOP 20
    print("\n=== TOP 20 高频短语 ===")
    for phrase, freq in sorted_phrases[:20]:
        print(f"  {freq:4d}  {phrase}")

    conn.close()

if __name__ == '__main__':
    main()
