#!/usr/bin/env python3
"""从懒笔记网站爬取四级真题阅读文章。
抓取：仔细阅读(Section C) + 长篇阅读(Section B) + 选词填空(Section A)
"""
import json
import os
import re
import time
import urllib.request

BASE_URL = 'https://english-exam.lazynote.cn'
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUTPUT_DIR = os.getenv('READLOOPS_CORPUS_DIR') or os.path.join(_ROOT, '语料库', '真题阅读纯文本')
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def fetch(url):
    """获取网页内容。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode('utf-8')


def get_article_links():
    """从阅读列表页获取所有文章链接。"""
    print('获取仔细阅读列表...')
    html = fetch(f'{BASE_URL}/cet4/sections/reading/')
    # 匹配 /cet4/articles/YYYY-MM-N/part3-section-c-N/
    links = re.findall(r'href=["\'](/cet4/articles/\d{4}-\d{2}-\d/part3-section-c-\d/)["\']', html)
    links = sorted(set(links))
    print(f'找到 {len(links)} 篇仔细阅读')

    print('获取长篇阅读列表...')
    html2 = fetch(f'{BASE_URL}/cet4/sections/long-reading/')
    links2 = re.findall(r'href=["\'](/cet4/articles/\d{4}-\d{2}-\d/part3-section-b/)["\']', html2)
    links2 = sorted(set(links2))
    print(f'找到 {len(links2)} 篇长篇阅读')

    return links + links2


def extract_article(html, url):
    """从文章页面提取正文。"""
    # 懒笔记的文章正文可能在特定的 div 中
    # 先尝试找文章标题
    title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ''

    # 尝试找正文内容
    # 懒笔记可能用 article 标签或特定 class
    content_match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if not content_match:
        content_match = re.search(r'<div[^>]*class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>', html, re.DOTALL)
    if not content_match:
        # 找主要内容区域
        content_match = re.search(r'<main[^>]*>(.*?)</main>', html, re.DOTALL)

    if content_match:
        content = content_match.group(1)
    else:
        content = html

    # 移除 HTML 标签
    text = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '\n', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&#\d+;', '', text)

    # 清理空行
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    # 过滤掉太短的行（导航、按钮等）和中文行（保留英文正文）
    english_lines = []
    for line in lines:
        # 统计英文字母比例
        alpha_count = sum(1 for c in line if c.isalpha() and ord(c) < 128)
        if alpha_count > 10 and alpha_count / len(line) > 0.5:
            english_lines.append(line)

    text = '\n\n'.join(english_lines)
    return title, text


def main():
    links = get_article_links()
    print(f'\n总共 {len(links)} 篇文章，开始爬取...\n')

    all_passages = []
    failed = []

    for i, link in enumerate(links, 1):
        url = BASE_URL + link
        try:
            html = fetch(url)
            title, text = extract_article(html, url)
            word_count = len(text.split())

            # 从 URL 提取来源信息
            # /cet4/articles/2015-06-1/part3-section-c-1/
            source_match = re.search(r'/articles/(\d{4}-\d{2}-\d)/part3-section-([a-z])-?(\d)?/', link)
            if source_match:
                date = source_match.group(1)
                section = source_match.group(2).upper()
                num = source_match.group(3) or ''
                source = f'{date} Section {section}{num}'
            else:
                source = link

            passage = {
                'source': source,
                'title': title,
                'url': url,
                'word_count': word_count,
                'text': text,
            }
            all_passages.append(passage)
            print(f'[{i}/{len(links)}] {source}: {word_count}词 - {title[:40]}')

        except Exception as e:
            print(f'[{i}/{len(links)}] 失败: {url} - {e}')
            failed.append(url)

        # 礼貌延迟，避免被封
        time.sleep(0.5)

    # 保存所有文章
    output_file = os.path.join(OUTPUT_DIR, 'all_passages_lazynote.txt')
    with open(output_file, 'w', encoding='utf-8') as f:
        for i, p in enumerate(all_passages, 1):
            f.write(f'=== Passage {i} ({p["source"]}) ===\n')
            f.write(f'标题: {p["title"]}\n')
            f.write(f'来源: {p["url"]}\n')
            f.write(f'词数: {p["word_count"]}\n\n')
            f.write(p['text'])
            f.write('\n\n')

    # 保存 JSON
    json_file = os.path.join(OUTPUT_DIR, 'all_passages_lazynote.json')
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(all_passages, f, ensure_ascii=False, indent=2)

    total_words = sum(p['word_count'] for p in all_passages)
    print('\n=== 完成 ===')
    print(f'成功: {len(all_passages)} 篇')
    print(f'失败: {len(failed)} 篇')
    print(f'总词数: {total_words}')
    print(f'保存到: {output_file}')
    if failed:
        print(f'失败链接: {failed}')


if __name__ == '__main__':
    main()
