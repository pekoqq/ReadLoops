"""语料库导入：四级/六级词表导入 words 表。"""
import re
import time
from pathlib import Path

from .. import config
from ..database import get_db


def parse_word_line(line: str):
    """解析词表行：word [phonetic] pos.meaning"""
    line = line.strip()
    if not line or len(line) <= 2:
        return None
    if re.match(r'^[A-Z]$', line):
        return None
    phonetic = ""
    phonetic_match = re.search(r'\[([^\]]+)\]', line)
    if phonetic_match:
        phonetic = phonetic_match.group(1)
        line = line[:phonetic_match.start()] + line[phonetic_match.end():]
    parts = line.split(None, 1)
    if len(parts) < 2:
        return None
    word = parts[0].strip()
    meaning = parts[1].strip() if len(parts) > 1 else ""
    if not word or not re.match(r'^[a-zA-Z]', word):
        return None
    return word.lower(), word, phonetic, meaning

def import_word_list(filepath: str, level: str = "CET4") -> int:
    """导入词表文件，返回导入数量。"""
    path = Path(filepath)
    if not path.exists():
        return 0
    now = int(time.time())
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()
    with get_db() as conn:
        for line in lines:
            parsed = parse_word_line(line)
            if not parsed:
                continue
            lemma, text, phonetic, meaning = parsed
            existing = conn.execute("SELECT id FROM words WHERE lemma=?", (lemma,)).fetchone()
            if existing:
                continue
            conn.execute("""INSERT INTO words
                (lemma, text, type, meaning, phonetic, level, status, created_at, updated_at)
                VALUES (?, ?, 'word', ?, ?, ?, 'new', ?, ?)""",
                (lemma, text, meaning, phonetic, level, now, now))
            count += 1
    return count

def import_cet4_words() -> int:
    """导入四级词表。"""
    path = config.CORPUS_DIR / "词表" / "CET4_大纲词表.txt"
    return import_word_list(str(path), "CET4")

def import_cet6_words() -> int:
    """导入六级词表。"""
    path = config.CORPUS_DIR / "词表" / "CET6_大纲词表.txt"
    return import_word_list(str(path), "CET6")

def lookup_local_dict(word: str) -> dict:
    """从本地 words 表查询单词释义。"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM words WHERE lemma=?", (word.lower(),)).fetchone()
        if row:
            return {
                "word": row["text"],
                "phonetic": row["phonetic"] or "",
                "meaning": row["meaning"] or "",
                "in_vocab": True,
                "word_id": row["id"],
            }
    return {"word": word, "phonetic": "", "meaning": "", "in_vocab": False, "word_id": None}
