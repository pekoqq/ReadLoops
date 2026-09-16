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
    -- 来自 ECDICT 的考试标签与词频（tools/import_exam_tags.py 导入）：
    -- 词汇差距统计需要知道「哪些词属于哪场考试」，以及「这个词有多常用」。
    tags TEXT,                        -- 空格分隔：cet4 cet6 ky ielts toefl gre gk zk
    frq INTEGER DEFAULT 0,            -- COCA 词频排名（0 = 不在表）；越小越高频
    bnc INTEGER DEFAULT 0,            -- BNC 词频排名
    collins INTEGER,                  -- 柯林斯星级 1–5
    oxford INTEGER DEFAULT 0,         -- 是否牛津核心 3000 词
    mastered INTEGER DEFAULT 0,       -- 0=未测 / 1=掌握 / 2=未掌握（定级测试声明）
    -- 掌握度模型（app/services/mastery.py）：识别与回忆分开累积 log-odds。
    -- 依据 Pellicer-Sánchez (2015)：8 次遇见只能带来 86% 形式识别 / 75% 意义识别 /
    -- 55% 回忆 —— 识别与回忆的证据强度差一个量级，合成一个数会把两者都毁掉。
    m_recognize REAL DEFAULT 0,       -- 识别层 log-odds
    m_recall REAL DEFAULT 0,          -- 回忆层 log-odds
    m_level TEXT DEFAULT 'unknown',   -- unknown / seen / recognized / recalled
    m_updated INTEGER,
    -- 词条来源，决定生词本的「删除」是真删还是只移出学习队列：
    --   ecdict  来自 ECDICT 导入（有词典价值）
    --   legacy  更早一次词典导入的遗留（无释义）
    --   user    用户在界面里手动 / AI 补录的（只有这种才允许真删）
    -- 「删除」不该把一本词典里的词抠掉 —— 用户想表达的是「别让我再学它了」。
    source TEXT DEFAULT 'user',
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

-- ========== 书架：公共领域书籍（Project Gutenberg / Standard Ebooks）==========
-- 只存元数据与本地路径，书籍文件落在 data/library/，不进版本库。
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'gutenberg',
    source_id TEXT,                      -- 来源站点的书号
    title TEXT NOT NULL,
    author TEXT,
    language TEXT DEFAULT 'en',
    subjects TEXT DEFAULT '[]',          -- JSON 数组
    format TEXT DEFAULT 'txt',           -- txt / epub
    download_url TEXT,
    local_path TEXT,                     -- 下载后的本地文件路径
    word_count INTEGER DEFAULT 0,
    difficulty_score REAL,               -- 可读性评分（越低越简单）
    status TEXT DEFAULT 'downloaded',    -- downloaded / parsed / imported / failed
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- ========== 材料：用户导入的任意文本（教材、讲义、自备真题等）==========
-- 材料内容只存在用户本机，用于蒸馏与出题，不随项目分发。
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'file',   -- file / paste / book
    source_path TEXT,
    content TEXT,                        -- 解析后的纯文本
    word_count INTEGER DEFAULT 0,
    para_count INTEGER DEFAULT 0,
    sentence_count INTEGER DEFAULT 0,
    parse_status TEXT DEFAULT 'pending', -- pending / parsed / failed
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- ========== 蒸馏：从材料中提取的风格画像 ==========
-- 只保存统计特征（事实性数据），不保存原文。
CREATE TABLE IF NOT EXISTS style_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    material_ids TEXT DEFAULT '[]',      -- JSON 数组：参与蒸馏的材料
    params TEXT NOT NULL DEFAULT '{}',   -- JSON：句长/词频/语法结构等统计量
    sample_count INTEGER DEFAULT 0,      -- 参与统计的句子数
    is_active INTEGER DEFAULT 0,         -- 当前生效的画像（生成文章时使用）
    created_at INTEGER NOT NULL
);

-- ========== 知识图谱：节点与边 ==========
-- 同一份图两套用途：前端可视化（给用户看）+ 结构化检索（给 AI 用）。
CREATE TABLE IF NOT EXISTS graph_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type TEXT NOT NULL,             -- word / article / material / book / phrase / grammar
    ref_id INTEGER,                      -- 指向对应表的行
    label TEXT NOT NULL,
    weight REAL DEFAULT 1.0,             -- 节点大小
    meta TEXT DEFAULT '{}',              -- JSON：附加信息
    created_at INTEGER NOT NULL,
    UNIQUE(node_type, ref_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL DEFAULT 'cooccur',  -- cooccur / contains / similar / derives
    weight REAL DEFAULT 1.0,
    created_at INTEGER NOT NULL,
    UNIQUE(source_id, target_id, edge_type),
    FOREIGN KEY (source_id) REFERENCES graph_nodes(id),
    FOREIGN KEY (target_id) REFERENCES graph_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_words_status ON words(status);
CREATE INDEX IF NOT EXISTS idx_words_level ON words(level);
CREATE INDEX IF NOT EXISTS idx_words_text ON words(text);
CREATE INDEX IF NOT EXISTS idx_phrases_level ON phrases(level);
CREATE INDEX IF NOT EXISTS idx_test_questions_test_id ON test_questions(test_id);
CREATE INDEX IF NOT EXISTS idx_test_questions_word_id ON test_questions(word_id);
CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);
CREATE INDEX IF NOT EXISTS idx_materials_status ON materials(parse_status);
CREATE INDEX IF NOT EXISTS idx_style_profiles_active ON style_profiles(is_active);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id);

-- ========== 定级测试：词汇量的初始标定 ==========
-- 没有它，覆盖率 / 考试缺口 / i+1 选词都没有起点（新用户只有
-- 「2000 + 学过的几个词」这种无意义的数字）。
CREATE TABLE IF NOT EXISTS placement_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vocab_estimate INTEGER NOT NULL,
    is_lower_bound INTEGER DEFAULT 0,   -- 最高档也掌握得很好 → 估计值只是下界
    false_alarm REAL DEFAULT 0,         -- 伪词虚报率（自评高估的校正项）
    answered INTEGER DEFAULT 0,
    source TEXT DEFAULT 'placement',    -- placement=真实测试 / default=默认假设（若未测）
    created_at INTEGER NOT NULL
);

-- 逐档掌握率：覆盖率计算靠它按词频档位给语料加权
CREATE TABLE IF NOT EXISTS placement_bands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    band_lo INTEGER NOT NULL,
    band_hi INTEGER NOT NULL,
    band_size INTEGER NOT NULL,
    sampled INTEGER NOT NULL,
    known INTEGER NOT NULL,
    rate REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES placement_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_placement_bands_run ON placement_bands(run_id);

-- 「遇见」记录幂等：同一篇文章对同一个词只记一次。
-- 用部分唯一索引而不是普通唯一索引，是因为 action='lookup' 的查词记录
-- 本来就是一次一条（同一篇里查两次应当记两次），不能被去重。
CREATE UNIQUE INDEX IF NOT EXISTS idx_encounters_seen
    ON word_encounters(word_id, article_id) WHERE action = 'seen';
"""

DEFAULT_SETTINGS = {
    "theme": "dark",
    "font_size": "17",
    "line_height": "1.75",
    "ai_base_url": "https://api.deepseek.com",
    "ai_api_key": "",
    "ai_model": "deepseek-flash",
}


# 列级迁移：为历史遗留数据库补齐 SCHEMA 中新增的列（幂等，可重复执行）
# 背景：CREATE TABLE IF NOT EXISTS 不会改动已存在的表，导致旧库缺列、代码报
#       "table X has no column named Y"。这里统一在启动时补齐，避免 schema 漂移。
COLUMN_MIGRATIONS = {
    "words": {
        "exchange": "TEXT",
        "wrong_count": "INTEGER DEFAULT 0",
        "correct_count": "INTEGER DEFAULT 0",
        "last_test_at": "INTEGER",
        "srs_interval": "REAL DEFAULT 0",
        "tags": "TEXT",
        "frq": "INTEGER DEFAULT 0",
        "bnc": "INTEGER DEFAULT 0",
        "collins": "INTEGER",
        "oxford": "INTEGER DEFAULT 0",
        "mastered": "INTEGER DEFAULT 0",
        "m_recognize": "REAL DEFAULT 0",
        "m_recall": "REAL DEFAULT 0",
        "m_level": "TEXT DEFAULT 'unknown'",
        "m_updated": "INTEGER",
        "source": "TEXT DEFAULT 'user'",
    },
    "placement_runs": {
        "source": "TEXT DEFAULT 'placement'",
    },
    "tests": {
        "status": "TEXT DEFAULT 'in_progress'",
        "total": "INTEGER DEFAULT 0",
        "completed_at": "INTEGER",
    },
}


def _ensure_columns(conn):
    """补齐各表缺失的列（幂等）。"""
    for table, columns in COLUMN_MIGRATIONS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


# 建在「迁移新增列」上的索引，必须在 _ensure_columns() 之后执行 ——
# 否则首次升级旧库时，列还没补齐就建索引会报 "no such column"。
POST_MIGRATION_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_words_frq ON words(frq);
CREATE INDEX IF NOT EXISTS idx_words_mastered ON words(mastered);
CREATE INDEX IF NOT EXISTS idx_words_m_level ON words(m_level);
CREATE INDEX IF NOT EXISTS idx_words_source ON words(source);
-- 表达式索引：选词/定级/覆盖率都用「有效词频 = frq 或 bnc」排序或分档，
-- 建在 frq 上的普通索引对 COALESCE(NULLIF(frq,0), bnc) 完全用不上。
CREATE INDEX IF NOT EXISTS idx_words_eff_rank ON words(COALESCE(NULLIF(frq, 0), bnc, 0));
"""


def _dedupe_books(conn):
    """书架去重：同一来源的同一本书只保留最早一条。

    早期版本没有唯一约束，重复下载同一本书会插入多条记录。
    这里先清理，再建唯一索引（顺序不能反，否则索引创建会因脏数据失败）。
    """
    conn.execute(
        """DELETE FROM books WHERE id NOT IN (
               SELECT MIN(id) FROM books GROUP BY source, source_id
           )"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_books_source ON books(source, source_id)"
    )


def init_db():
    """初始化数据库：建表、补齐缺失列、插入默认数据。"""
    import time
    now = int(time.time())
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        conn.executescript(POST_MIGRATION_INDEXES)
        _dedupe_books(conn)
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
