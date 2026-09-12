"""从 ECDICT CSV 导入 exchange 词形变化数据到 words 表。"""
import csv
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'yuedu.db')
ECDICT_PATH = '/tmp/ecdict.csv'

# exchange 类型映射
EXCHANGE_TYPES = {
    '0': 'lemma',      # 原型
    '1': 'plural',     # 复数
    '2': 'ing',        # 现在分词
    '4': 'done',       # 过去分词
    '5': 'er',         # 比较级
    '6': 'est',        # 最高级
    's': 'plural',     # 复数
    'i': 'ing',        # 现在分词
    'p': 'past',       # 过去式
    'd': 'done',       # 过去分词
    'r': 'er',         # 比较级
    't': 'est',        # 最高级
    '3': 'third',      # 第三人称单数
}

def parse_exchange(exchange_str):
    """解析 exchange 字段，返回 {type: word} 字典。"""
    if not exchange_str or exchange_str == '/':
        return {}

    result = {}
    parts = exchange_str.split('/')
    for part in parts:
        if ':' in part:
            type_code, word = part.split(':', 1)
            type_name = EXCHANGE_TYPES.get(type_code, type_code)
            result[type_name] = word
    return result

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 先获取 words 表中所有词，建立 text -> id 映射
    print("加载 words 表...")
    word_ids = {}
    for row in cursor.execute("SELECT id, text FROM words").fetchall():
        word_ids[row[1].lower()] = row[0]
    print(f"words 表有 {len(word_ids)} 个词")

    # 读取 ECDICT CSV，更新 exchange 字段
    print("读取 ECDICT CSV...")
    updated = 0
    with open(ECDICT_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            word = row['word'].lower()
            exchange_str = row.get('exchange', '')
            if word in word_ids and exchange_str and exchange_str != '/':
                parsed = parse_exchange(exchange_str)
                if parsed:
                    import json
                    exchange_json = json.dumps(parsed, ensure_ascii=False)
                    cursor.execute(
                        "UPDATE words SET exchange=? WHERE id=?",
                        (exchange_json, word_ids[word])
                    )
                    updated += 1
                    if updated % 10000 == 0:
                        print(f"已更新 {updated} 个词...")
                        conn.commit()

    conn.commit()
    conn.close()
    print(f"完成！共更新 {updated} 个词的 exchange 数据")

if __name__ == '__main__':
    main()
