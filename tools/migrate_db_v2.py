"""数据库迁移：添加词形变化、测试统计字段，建短语表和测试题目表。"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'yuedu.db')

def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. words 表加字段
    existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(words)").fetchall()]

    if 'exchange' not in existing_cols:
        cursor.execute("ALTER TABLE words ADD COLUMN exchange TEXT")
        print("添加 exchange 字段")

    if 'wrong_count' not in existing_cols:
        cursor.execute("ALTER TABLE words ADD COLUMN wrong_count INTEGER DEFAULT 0")
        print("添加 wrong_count 字段")

    if 'correct_count' not in existing_cols:
        cursor.execute("ALTER TABLE words ADD COLUMN correct_count INTEGER DEFAULT 0")
        print("添加 correct_count 字段")

    if 'last_test_at' not in existing_cols:
        cursor.execute("ALTER TABLE words ADD COLUMN last_test_at INTEGER")
        print("添加 last_test_at 字段")

    # 2. 建 phrases 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS phrases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT UNIQUE NOT NULL,
            meaning TEXT,
            frequency INTEGER DEFAULT 0,
            level TEXT DEFAULT 'CET4',
            created_at INTEGER
        )
    """)
    print("创建 phrases 表")

    # 3. 建 test_questions 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS test_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER,
            word_id INTEGER,
            word_text TEXT,
            question_type TEXT,
            question TEXT,
            options TEXT,
            answer TEXT,
            user_answer TEXT,
            is_correct INTEGER,
            reaction_time INTEGER,
            created_at INTEGER
        )
    """)
    print("创建 test_questions 表")

    # 4. 建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_phrases_level ON phrases(level)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_questions_test_id ON test_questions(test_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_questions_word_id ON test_questions(word_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_words_status ON words(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_words_level ON words(level)")

    conn.commit()
    conn.close()
    print("迁移完成")

if __name__ == '__main__':
    migrate()
