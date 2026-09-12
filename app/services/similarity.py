"""文章相似度计算模块。
纯 Python 实现 TF-IDF + 余弦相似度，零外部依赖。
用于：文章去重、同质化检测、真题相似度匹配、高频词提取。
"""
import math
import re
from collections import Counter, defaultdict

from app import config
from app.database import get_db

# 英语停用词
STOPWORDS = set("""
a an the and or but if then else when while although though because since as
of in on at to for with by from about into over under after before between
is are was were be been being have has had do does did will would could should
may might can shall it its this that these those i you he she we they me him
her us them my your his our their not no so too very just also only own same
such any all each both few more most other some than them what which who whom
whose where why how there here up down out off on again once further
""".split())


def tokenize(text):
    """分词：转小写，提取单词，过滤停用词和短词。"""
    words = re.findall(r'[a-zA-Z]+', text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def get_word_freq(text):
    """获取词频统计。"""
    return Counter(tokenize(text))


def get_top_words(text, n=10):
    """获取高频词 TOP N。"""
    freq = get_word_freq(text)
    return freq.most_common(n)


def build_tfidf_vectors(documents):
    """构建 TF-IDF 向量。
    documents: list of (doc_id, text)
    returns: dict of doc_id -> {word: tfidf_weight}
    """
    # 计算文档频率（DF）
    doc_freq = defaultdict(int)
    doc_tokens = {}

    for doc_id, text in documents:
        tokens = set(tokenize(text))
        doc_tokens[doc_id] = tokens
        for word in tokens:
            doc_freq[word] += 1

    n_docs = len(documents)
    vectors = {}

    for doc_id, text in documents:
        tokens = tokenize(text)
        tf = Counter(tokens)
        vector = {}
        for word, count in tf.items():
            # TF = 词频 / 总词数
            tf_weight = count / len(tokens) if tokens else 0
            # IDF = log(总文档数 / 文档频率)
            idf_weight = math.log(n_docs / (doc_freq[word] + 1))
            vector[word] = tf_weight * idf_weight
        vectors[doc_id] = vector

    return vectors


def cosine_similarity(vec1, vec2):
    """计算两个向量的余弦相似度。"""
    # 找共同词
    common = set(vec1.keys()) & set(vec2.keys())
    if not common:
        return 0.0

    dot_product = sum(vec1[w] * vec2[w] for w in common)
    norm1 = math.sqrt(sum(v ** 2 for v in vec1.values()))
    norm2 = math.sqrt(sum(v ** 2 for v in vec2.values()))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


def get_article_similarity(article_text, other_articles, top_n=3):
    """计算一篇文章与其他文章的相似度，返回 TOP N。
    other_articles: list of (id, title, text)
    returns: list of (id, title, similarity_score)
    """
    if not other_articles:
        return []

    # 构建所有文档的 TF-IDF 向量（包含目标文章）
    all_docs = [('target', article_text)] + [(str(a[0]), a[2]) for a in other_articles]
    vectors = build_tfidf_vectors(all_docs)

    target_vec = vectors.get('target', {})
    similarities = []

    for a in other_articles:
        doc_id = str(a[0])
        if doc_id in vectors:
            sim = cosine_similarity(target_vec, vectors[doc_id])
            similarities.append((a[0], a[1], sim))

    # 按相似度降序排序
    similarities.sort(key=lambda x: x[2], reverse=True)
    return similarities[:top_n]


def get_exam_similarity(article_text, top_n=3):
    """计算文章与真题的相似度，返回 TOP N 最相近的真题。"""
    return _load_exam_from_file(article_text, top_n)


def _load_exam_from_file(article_text, top_n=3):
    """从语料库文件加载真题并计算相似度。

    真题语料受版权保护，不随仓库分发，需用户自备。默认路径：
    <项目根>/语料库/真题阅读纯文本/all_passages_lazynote.json
    可用环境变量 READLOOPS_CORPUS_FILE 覆盖。文件不存在时返回空列表。
    """
    import json
    import os

    exam_file = os.getenv('READLOOPS_CORPUS_FILE') or str(
        config.CORPUS_DIR / '真题阅读纯文本' / 'all_passages_lazynote.json'
    )
    if not os.path.exists(exam_file):
        return []

    with open(exam_file, 'r', encoding='utf-8') as f:
        passages = json.load(f)

    # 只取仔细阅读（Section C），约350词，和生成文章长度接近
    exam_articles = []
    for p in passages:
        if 'Section C' in p.get('source', ''):
            title = p.get('title', '') or p.get('source', '')
            exam_articles.append((p.get('source', ''), title, p.get('text', '')))

    return get_article_similarity(article_text, exam_articles, top_n)


def check_duplicate(article_text, recent_articles, threshold=0.4):
    """检查文章是否与最近文章重复或同质化。
    返回 (is_duplicate, max_similarity, most_similar_title)
    """
    if not recent_articles:
        return False, 0.0, ''

    other = [(a['id'], a['title'] or '文章', a['content'] or '') for a in recent_articles]
    similarities = get_article_similarity(article_text, other, top_n=1)

    if similarities:
        _, title, sim = similarities[0]
        return sim >= threshold, sim, title

    return False, 0.0, ''


def get_article_details(article_id):
    """获取文章详情：高频词、目标生词、真题相似度。"""
    with get_db() as conn:
        article = conn.execute(
            "SELECT id, title, content, target_words, created_at FROM articles WHERE id=?",
            (article_id,)
        ).fetchone()

    if not article:
        return None

    content = article['content'] or ''
    target_words = []
    if article['target_words']:
        try:
            import json
            target_words = json.loads(article['target_words'])
        except Exception:
            pass

    # 高频词
    top_words = get_top_words(content, n=10)

    # 真题相似度
    exam_sim = get_exam_similarity(content, top_n=3)

    # 词数
    word_count = len(re.findall(r'[a-zA-Z]+', content))

    return {
        'id': article['id'],
        'title': article['title'],
        'content': content,
        'word_count': word_count,
        'created_at': article['created_at'],
        'target_words': target_words,
        'top_words': top_words,
        'exam_similarity': [
            {'id': s[0], 'title': s[1], 'similarity': round(s[2] * 100, 1)}
            for s in exam_sim
        ],
    }
