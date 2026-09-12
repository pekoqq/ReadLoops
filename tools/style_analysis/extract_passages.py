#!/usr/bin/env python3
"""从四级真题 markdown 中提取阅读文章，保存为纯文本。"""
import glob
import os
import re


def clean_text(text):
    """清理文本。"""
    # 移除 markdown 格式
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    text = re.sub(r'<u>.*?</u>', '_____', text)  # 选词填空的空格
    # 移除表格
    text = re.sub(r'\|.*?\|\n', '', text)
    text = re.sub(r'[-|]+\n', '', text)
    # 移除题目选项 A) B) C) D)
    text = re.sub(r'^[A-D]\)\s.*$', '', text, flags=re.MULTILINE)
    # 移除题号
    text = re.sub(r'^\d+\.\s*$', '', text, flags=re.MULTILINE)
    # 移除空行过多
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_section_a(content):
    """提取 Section A（选词填空）的文章。"""
    match = re.search(r'### Section A\n(.*?)(?=\n### |\Z)', content, re.DOTALL)
    if not match:
        return None
    section = match.group(1)
    # 找到 Directions 之后，表格之前的内容
    text_match = re.search(r'\*\*Directions:\*\*.*?\n(.*?)(?=\n\|)', section, re.DOTALL)
    if text_match:
        return clean_text(text_match.group(1))
    return None


def extract_section_b(content):
    """提取 Section B（长篇阅读）的文章。"""
    match = re.search(r'### Section B\n(.*?)(?=\n### |\Z)', content, re.DOTALL)
    if not match:
        return None
    section = match.group(1)
    # 找到 Directions 之后，题目之前的内容
    # 题目格式：数字. 句子
    text_match = re.search(r'\*\*Directions\*\*:?.*?\n(.*?)(?=\n\d+\.\s)', section, re.DOTALL)
    if text_match:
        return clean_text(text_match.group(1))
    return None


def extract_section_c(content):
    """提取 Section C（仔细阅读）的文章，通常2篇。"""
    match = re.search(r'### Section C\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if not match:
        return []
    section = match.group(1)

    passages = []
    # Section C 通常有2篇文章，每篇后面跟5道题
    # 按题目分割：找到 "1." "6." 这样的题号
    # 文章在 Directions 之后，第一道题之前
    # 第一篇文章
    first_passage_match = re.search(r'\*\*Directions:\*\*.*?\n(.*?)(?=\n1\.\s)', section, re.DOTALL)
    if first_passage_match:
        passages.append(clean_text(first_passage_match.group(1)))

    # 第二篇文章（在第6题之后，第11题之前，或者在 "Questions 6 to 10" 之后）
    second_match = re.search(r'(?:Questions 6 to 10|6\.\s).*?\n(.*?)(?=\n11\.\s|\Z)', section, re.DOTALL)
    if second_match:
        passages.append(clean_text(second_match.group(1)))

    return [p for p in passages if len(p.split()) > 50]


def main():
    input_dir = os.getenv('READLOOPS_RAW_MD_DIR', '/tmp/english-exam-md/CET4')
    output_dir = os.getenv('READLOOPS_CORPUS_DIR') or os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')),
        '语料库', '真题阅读纯文本')
    os.makedirs(output_dir, exist_ok=True)

    all_passages = []
    md_files = glob.glob(os.path.join(input_dir, '**', '*.md'), recursive=True)

    for md_path in sorted(md_files):
        rel_path = os.path.relpath(md_path, input_dir)
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 找到 Part III
        part_iii_match = re.search(r'## Part III.*?\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
        if not part_iii_match:
            print(f'{rel_path}: 未找到 Part III')
            continue
        reading_part = part_iii_match.group(1)

        passages = []
        # Section A
        sa = extract_section_a(reading_part)
        if sa:
            passages.append({'type': 'Section A 选词填空', 'text': sa})
        # Section B
        sb = extract_section_b(reading_part)
        if sb:
            passages.append({'type': 'Section B 长篇阅读', 'text': sb})
        # Section C
        scs = extract_section_c(reading_part)
        for sc in scs:
            passages.append({'type': 'Section C 仔细阅读', 'text': sc})

        for p in passages:
            p['source'] = rel_path
            all_passages.append(p)

        print(f'{rel_path}: 提取 {len(passages)} 篇')

    # 保存所有文章
    output_file = os.path.join(output_dir, 'all_passages.txt')
    with open(output_file, 'w', encoding='utf-8') as f:
        for i, p in enumerate(all_passages, 1):
            f.write(f'=== Passage {i} ({p["type"]}) ===\n')
            f.write(f'来源: {p["source"]}\n')
            f.write(f'词数: {len(p["text"].split())}\n\n')
            f.write(p['text'])
            f.write('\n\n')

    total_words = sum(len(p['text'].split()) for p in all_passages)
    print(f'\n总计: {len(all_passages)} 篇文章, {total_words} 词')
    print(f'保存到: {output_file}')


if __name__ == '__main__':
    main()
