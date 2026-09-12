#!/usr/bin/env python3
"""四级真题阅读风格分析。
分析维度：
1. 文本特征：句长、段长、词汇难度、词长分布
2. 句式特征：简单句/复合句、连接词、从句
3. 结构特征：开头方式、结尾方式
4. 词汇特征：高频词、高频短语
5. 题材分布
"""
import json
import os
import re
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..'))
PASSAGES_FILE = os.getenv('READLOOPS_CORPUS_FILE') or os.path.join(
    _ROOT, '语料库', '真题阅读纯文本', 'all_passages_lazynote.txt')
OUTPUT_DIR = _HERE


def load_passages():
    """加载所有文章。"""
    passages = []
    with open(PASSAGES_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = re.split(r'=== Passage \d+.*?===\n', content)
    for block in blocks[1:]:
        lines = block.strip().split('\n')
        source = ''
        word_count = 0
        text_lines = []
        for line in lines:
            if line.startswith('来源:'):
                source = line.replace('来源:', '').strip()
            elif line.startswith('词数:'):
                word_count = int(line.replace('词数:', '').strip())
            elif line.strip():
                text_lines.append(line)
        text = '\n'.join(text_lines).strip()
        if text:
            passages.append({'source': source, 'word_count': word_count, 'text': text})
    return passages


def analyze_text_features(passages):
    """文本特征分析。"""
    all_sentences = []
    all_words = []
    sentence_lengths = []
    paragraph_counts = []
    word_lengths = []

    for p in passages:
        text = p['text']
        # 分段
        paragraphs = [para for para in text.split('\n\n') if para.strip()]
        paragraph_counts.append(len(paragraphs))
        # 分句（按 . ! ? 分割，但要排除缩写如 Mr. U.S.）
        sentences = re.split(r'(?<=[.!?])\s+', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        all_sentences.extend(sentences)
        for s in sentences:
            words = re.findall(r'[a-zA-Z]+', s)
            sentence_lengths.append(len(words))
            all_words.extend(words)
            for w in words:
                word_lengths.append(len(w))

    # 词汇难度（用词长近似）
    short_words = sum(1 for w in all_words if len(w) <= 3)
    medium_words = sum(1 for w in all_words if 4 <= len(w) <= 6)
    long_words = sum(1 for w in all_words if len(w) >= 7)

    return {
        '总文章数': len(passages),
        '总句数': len(all_sentences),
        '总词数': len(all_words),
        '平均每篇词数': round(len(all_words) / len(passages), 1),
        '平均每篇段落数': round(sum(paragraph_counts) / len(passages), 1),
        '平均句长（词）': round(sum(sentence_lengths) / len(sentence_lengths), 1),
        '句长中位数': sorted(sentence_lengths)[len(sentence_lengths)//2],
        '短句占比(<=10词)': f"{round(sum(1 for s in sentence_lengths if s <= 10) / len(sentence_lengths) * 100, 1)}%",
        '中句占比(11-20词)': f"{round(sum(1 for s in sentence_lengths if 11 <= s <= 20) / len(sentence_lengths) * 100, 1)}%",
        '长句占比(>20词)': f"{round(sum(1 for s in sentence_lengths if s > 20) / len(sentence_lengths) * 100, 1)}%",
        '平均词长': round(sum(word_lengths) / len(word_lengths), 2),
        '短词占比(<=3)': f"{round(short_words / len(all_words) * 100, 1)}%",
        '中词占比(4-6)': f"{round(medium_words / len(all_words) * 100, 1)}%",
        '长词占比(>=7)': f"{round(long_words / len(all_words) * 100, 1)}%",
    }


def analyze_sentence_structure(passages):
    """句式特征分析。"""
    all_sentences = []
    for p in passages:
        sentences = re.split(r'(?<=[.!?])\s+', p['text'])
        all_sentences.extend([s.strip() for s in sentences if len(s.strip()) > 10])

    # 连接词统计
    conjunctions = ['and', 'but', 'or', 'so', 'because', 'although', 'though', 'while',
                    'if', 'when', 'where', 'which', 'that', 'who', 'whom', 'whose',
                    'however', 'therefore', 'moreover', 'furthermore', 'nevertheless',
                    'meanwhile', 'otherwise', 'unless', 'since', 'as', 'until', 'before', 'after']

    conj_counts = Counter()
    for s in all_sentences:
        words = re.findall(r'[a-zA-Z]+', s.lower())
        for c in conjunctions:
            if c in words:
                conj_counts[c] += 1

    # 从句类型（近似判断）
    relative_clauses = sum(1 for s in all_sentences if re.search(r'\b(which|that|who|whom|whose)\b', s, re.I))
    adverbial_clauses = sum(1 for s in all_sentences if re.search(r'\b(because|although|though|while|if|when|where|since|as|unless|until|before|after)\b', s, re.I))
    compound_sentences = sum(1 for s in all_sentences if re.search(r'\b(and|but|or|so)\b', s, re.I))

    return {
        '连接词TOP15': conj_counts.most_common(15),
        '复合句占比(含and/but/or/so)': f"{round(compound_sentences / len(all_sentences) * 100, 1)}%",
        '定语从句占比(which/that/who)': f"{round(relative_clauses / len(all_sentences) * 100, 1)}%",
        '状语从句占比(because/although/if等)': f"{round(adverbial_clauses / len(all_sentences) * 100, 1)}%",
    }


def analyze_structure(passages):
    """结构特征分析：开头和结尾方式。"""
    openings = []
    endings = []
    for p in passages:
        sentences = re.split(r'(?<=[.!?])\s+', p['text'])
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if sentences:
            openings.append(sentences[0])
            endings.append(sentences[-1])

    # 开头方式分类
    opening_types = Counter()
    for s in openings:
        first_word = re.findall(r'[a-zA-Z]+', s)[0].lower() if re.findall(r'[a-zA-Z]+', s) else ''
        if first_word in ['the', 'a', 'an']:
            opening_types['冠词开头(The/A)'] += 1
        elif first_word in ['it', 'this', 'that', 'these', 'those']:
            opening_types['代词开头(It/This)'] += 1
        elif first_word in ['when', 'while', 'if', 'although', 'because', 'since', 'as']:
            opening_types['从句开头(When/If)'] += 1
        elif first_word in ['according', 'based', 'given', 'compared']:
            opening_types['分词/介词开头'] += 1
        elif first_word in ['people', 'students', 'children', 'women', 'men', 'americans']:
            opening_types['名词复数开头(People)'] += 1
        else:
            opening_types[f'其他({first_word})'] += 1

    # 结尾方式
    ending_types = Counter()
    for s in endings:
        if re.search(r'\b(may|might|could|should|would|can)\b', s, re.I):
            ending_types['情态动词结尾(建议/推测)'] += 1
        elif re.search(r'\b(important|necessary|essential|crucial|key)\b', s, re.I):
            ending_types['强调重要性结尾'] += 1
        elif re.search(r'\b(however|but|yet|nevertheless)\b', s, re.I):
            ending_types['转折结尾'] += 1
        else:
            ending_types['陈述事实结尾'] += 1

    return {
        '开头方式分布': opening_types.most_common(),
        '结尾方式分布': ending_types.most_common(),
        '开头示例': openings[:5],
        '结尾示例': endings[:5],
    }


def analyze_vocabulary(passages):
    """词汇特征分析。"""
    all_words = []
    for p in passages:
        words = re.findall(r'[a-zA-Z]+', p['text'].lower())
        all_words.extend(words)

    # 停用词
    stopwords = set(['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                      'of', 'with', 'by', 'from', 'as', 'is', 'are', 'was', 'were', 'be',
                      'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                      'would', 'could', 'should', 'may', 'might', 'can', 'shall', 'it',
                      'its', 'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she',
                      'we', 'they', 'me', 'him', 'her', 'us', 'them', 'my', 'your',
                      'his', 'our', 'their', 'not', 'no', 'so', 'if', 'than', 'then',
                      'there', 'here', 'what', 'which', 'who', 'whom', 'whose', 'when',
                      'where', 'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more',
                      'most', 'other', 'some', 'such', 'only', 'own', 'same', 'very',
                      'about', 'up', 'out', 'into', 'over', 'after', 'before', 'between',
                      'through', 'during', 'without', 'under', 'again', 'once', 'also'])

    content_words = [w for w in all_words if w not in stopwords and len(w) > 2]
    word_freq = Counter(content_words)

    # 高频短语（2词组合）
    bigrams = []
    for p in passages:
        words = re.findall(r'[a-zA-Z]+', p['text'].lower())
        for i in range(len(words) - 1):
            if words[i] not in stopwords and words[i+1] not in stopwords:
                bigrams.append(f'{words[i]} {words[i+1]}')
    bigram_freq = Counter(bigrams)

    return {
        '高频实词TOP30': word_freq.most_common(30),
        '高频短语TOP20': bigram_freq.most_common(20),
        '词汇丰富度(不同词/总词)': f"{round(len(set(all_words)) / len(all_words) * 100, 1)}%",
    }


def analyze_topics(passages):
    """题材分布（关键词匹配）。"""
    topic_keywords = {
        '健康/医学': ['health', 'medical', 'disease', 'study', 'research', 'patients', 'doctor', 'hospital', 'exercise', 'diet', 'cancer', 'heart', 'brain', 'sleep', 'stress'],
        '科技/互联网': ['technology', 'internet', 'computer', 'digital', 'online', 'social', 'media', 'phone', 'app', 'data', 'ai', 'robot', 'virtual', 'screen'],
        '教育/学习': ['education', 'school', 'student', 'teacher', 'college', 'university', 'learning', 'study', 'exam', 'class', 'course', 'degree', 'academic'],
        '环境/气候': ['environment', 'climate', 'change', 'warming', 'carbon', 'pollution', 'energy', 'sustainable', 'green', 'eco', 'species', 'extinct', 'ocean', 'forest'],
        '社会/生活': ['people', 'society', 'social', 'family', 'work', 'job', 'city', 'urban', 'community', 'culture', 'tradition', 'lifestyle', 'happiness', 'relationship'],
        '商业/经济': ['business', 'economy', 'economic', 'market', 'company', 'consumer', 'price', 'cost', 'money', 'finance', 'trade', 'industry', 'growth', 'employment'],
        '心理/行为': ['psychology', 'behavior', 'behaviour', 'mind', 'brain', 'emotion', 'stress', 'anxiety', 'depression', 'motivation', 'decision', 'habit', 'personality'],
    }

    topic_counts = Counter()
    for p in passages:
        text = p['text'].lower()
        for topic, keywords in topic_keywords.items():
            if any(kw in text for kw in keywords):
                topic_counts[topic] += 1

    return {
        '题材分布': topic_counts.most_common(),
        '说明': '一篇文章可能匹配多个题材',
    }


def main():
    passages = load_passages()
    print(f'加载 {len(passages)} 篇文章\n')

    results = {}
    results['文本特征'] = analyze_text_features(passages)
    results['句式特征'] = analyze_sentence_structure(passages)
    results['结构特征'] = analyze_structure(passages)
    results['词汇特征'] = analyze_vocabulary(passages)
    results['题材分布'] = analyze_topics(passages)

    # 保存 JSON
    json_file = os.path.join(OUTPUT_DIR, 'style_analysis.json')
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 保存人类可读报告
    report_file = os.path.join(OUTPUT_DIR, 'style_analysis_report.md')
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('# 四级真题阅读风格分析报告\n\n')
        f.write(f'> 分析基于 {len(passages)} 篇真题阅读文章\n\n')

        for section, data in results.items():
            f.write(f'## {section}\n\n')
            for key, value in data.items():
                if isinstance(value, list):
                    f.write(f'### {key}\n\n')
                    for item in value:
                        if isinstance(item, tuple):
                            f.write(f'- {item[0]}: {item[1]}\n')
                        else:
                            f.write(f'- {item}\n')
                    f.write('\n')
                else:
                    f.write(f'- **{key}**: {value}\n')
            f.write('\n')

    print('分析完成！')
    print(f'JSON: {json_file}')
    print(f'报告: {report_file}')
    print('\n=== 关键指标 ===')
    for k, v in results['文本特征'].items():
        print(f'  {k}: {v}')


if __name__ == '__main__':
    main()
