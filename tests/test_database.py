"""数据库结构测试：确保全新安装即可用（10 表 + 完整列）。"""
from app import config
from app.database import get_db

EXPECTED_TABLES = {
    "users",
    "words",
    "word_encounters",
    "articles",
    "highlights",
    "reading_sessions",
    "tests",
    "settings",
    "phrases",
    "test_questions",
}

EXPECTED_WORD_COLUMNS = {
    "lemma",
    "text",
    "type",
    "meaning",
    "phonetic",
    "level",
    "status",
    "srs_stability",
    "srs_difficulty",
    "srs_interval",
    "exchange",
    "wrong_count",
    "correct_count",
    "last_test_at",
}


def test_db_path_is_isolated():
    """测试必须跑在临时目录，绝不能碰真实数据库。"""
    assert "readloops-test-" in str(config.DB_PATH)


def test_all_tables_created():
    with get_db() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names


def test_words_has_required_columns():
    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(words)")}
    assert EXPECTED_WORD_COLUMNS <= cols


def test_default_settings_seeded():
    with get_db() as conn:
        keys = {r[0] for r in conn.execute("SELECT key FROM settings")}
    assert {"theme", "ai_model", "ai_api_key"} <= keys
