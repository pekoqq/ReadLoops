"""数据库连接与初始化。

SCHEMA 是数据库结构的**单一事实源**：全新安装由 init_db() 直接建出完整结构（10 表）。
`tools/migrate_db_v2.py` 仅用于升级历史遗留的旧数据库，不再承担"补全结构"的职责。
"""
import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '用户1',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma TEXT NOT NULL,
    text TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'word',
    meaning TEXT,
    phonetic TEXT,
    level TEXT DEFAULT 'CET4',
    frequency INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    encounter_count INTEGER DEFAULT 0,
    lookup_count INTEGER DEFAULT 0,
    srs_due INTEGER,
    srs_stability REAL DEFAULT 0,
    srs_difficulty REAL DEFAULT 0,
    srs_state INTEGER DEFAULT 0,
    srs_lapses INTEGER DEFAULT 0,
    srs_reps INTEGER DEFAULT 0,
    srs_last_review INTEGER,
    srs_interval REAL DEFAULT 0,
    exchange TEXT,
    wrong_count INTEGER DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    last_test_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS word_encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id INTEGER NOT NULL,
    article_id INTEGER,
    context TEXT,
    action TEXT NOT NULL,
    implicit_rating TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (word_id) REFERENCES words(id)
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT DEFAULT 'ai',
    word_count INTEGER DEFAULT 0,
    target_words TEXT DEFAULT '[]',
    new_word_count INTEGER DEFAULT 0,
    difficulty_score REAL,
    reading_time_seconds INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS highlights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    word_id INTEGER,
    color TEXT DEFAULT 'yellow',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reading_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    start_time INTEGER NOT NULL,
    end_time INTEGER,
    duration_seconds INTEGER DEFAULT 0,
    lookups INTEGER DEFAULT 0,
    highlights INTEGER DEFAULT 0,
    FOREIGN KEY (article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT DEFAULT 'vocab',
    status TEXT DEFAULT 'in_progress',
    score INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    completed_at INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT UNIQUE NOT NULL,
    meaning TEXT,
    frequency INTEGER DEFAULT 0,
    level TEXT DEFAULT 'CET4',
    created_at INTEGER
);

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
);

CREATE INDEX IF NOT EXISTS idx_words_status ON words(status);
CREATE INDEX IF NOT EXISTS idx_words_level ON words(level);
CREATE INDEX IF NOT EXISTS idx_phrases_level ON phrases(level);
CREATE INDEX IF NOT EXISTS idx_test_questions_test_id ON test_questions(test_id);
CREATE INDEX IF NOT EXISTS idx_test_questions_word_id ON test_questions(word_id);
"""

DEFAULT_SETTINGS = {
    "theme": "dark",
    "font_size": "17",
    "line_height": "1.75",
    "ai_base_url": "https://api.deepseek.com",
    "ai_api_key": "",
    "ai_model": "deepseek-flash",
}


def init_db():
    """初始化数据库，建表并插入默认数据。"""
    import time
    now = int(time.time())
    with get_db() as conn:
        conn.executescript(SCHEMA)
        # 默认用户
        conn.execute("INSERT OR IGNORE INTO users (id, name, created_at) VALUES (1, '用户1', ?)", (now,))
        # 默认设置
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))


@contextmanager
def get_db():
    """获取数据库连接，自动提交和关闭。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# 模块加载时自动初始化
init_db()
