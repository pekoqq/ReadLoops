"""词典导入：支持 ECDICT（CSV / JSON）与纯文本词表。

设计要点：
- 流式读取，避免把整份词典读进内存（ECDICT 约 70MB / 77 万词）
- 先一次性载入已有 lemma 集合，再批量插入，避免逐行查询（O(n²)）
- 兼容多种来源格式，自动识别
"""
import csv
import json
import re
import time
from pathlib import Path

from ..database import get_db

# ECDICT exchange 字段的类型码映射
EXCHANGE_TYPES = {
    "0": "lemma",     # 原型
    "1": "plural",    # 复数
    "2": "ing",       # 现在分词
    "3": "third",     # 第三人称单数
    "4": "done",      # 过去分词
    "5": "er",        # 比较级
    "6": "est",       # 最高级
    "s": "plural",
    "i": "ing",
    "p": "past",
    "d": "done",
    "r": "er",
    "t": "est",
}

# ECDICT tag 字段 → 项目 level（按优先级匹配）
LEVEL_TAGS = [
    ("cet4", "CET4"),
    ("cet6", "CET6"),
    ("ky", "KY"),
    ("gk", "GK"),
    ("zk", "ZK"),
]

BATCH_SIZE = 2000


def parse_exchange(value):
    """把 ECDICT 的 exchange 字段解析为 {类型: 词形}。"""
    if not value or value == "/":
        return {}
    result = {}
    for part in value.split("/"):
        if ":" not in part:
            continue
        code, form = part.split(":", 1)
        form = form.strip()
        if form:
            result[EXCHANGE_TYPES.get(code, code)] = form
    return result


def level_from_tag(tag):
    """从 ECDICT 的 tag 字段推断词表等级。"""
    if not tag:
        return "OTHER"
    low = tag.lower()
    for key, level in LEVEL_TAGS:
        if key in low:
            return level
    return "OTHER"


def detect_format(path):
    """识别词表文件格式：ecdict_csv / json / text。"""
    path = Path(path)
    if path.suffix.lower() == ".json":
        return "json"
    if path.suffix.lower() == ".csv":
        return "ecdict_csv"
    # 未知后缀：看首行像不像带表头的 CSV
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        head = f.readline()
    if "," in head and "word" in head.lower():
        return "ecdict_csv"
    return "text"


def _iter_ecdict_csv(path):
    """流式产出 ECDICT CSV 中的行。"""
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            word = (row.get("word") or "").strip()
            if not word:
                continue
            yield {
                "lemma": word.lower(),
                "text": word,
                "phonetic": (row.get("phonetic") or "").strip(),
                "meaning": (row.get("translation") or row.get("definition") or "").strip(),
                "level": level_from_tag(row.get("tag")),
                "exchange": parse_exchange(row.get("exchange")),
                "frequency": _to_int(row.get("frq")) or _to_int(row.get("bnc")) or 0,
            }


def _iter_json(path):
    """产出 JSON 词表行（兼容 list[dict] 与 dict 包裹）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("words") or data.get("data") or list(data.values())
    for row in data:
        if not isinstance(row, dict):
            continue
        word = (row.get("word") or row.get("text") or row.get("lemma") or "").strip()
        if not word:
            continue
        yield {
            "lemma": word.lower(),
            "text": word,
            "phonetic": (row.get("phonetic") or "").strip(),
            "meaning": (row.get("translation") or row.get("meaning")
                        or row.get("definition") or "").strip(),
            "level": row.get("level") or level_from_tag(row.get("tag")),
            "exchange": (row.get("exchange") if isinstance(row.get("exchange"), dict)
                         else parse_exchange(row.get("exchange"))),
            "frequency": _to_int(row.get("frq")) or _to_int(row.get("frequency")) or 0,
        }


def _iter_text(path):
    """产出纯文本词表行，格式：word [phonetic] pos.meaning"""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or len(line) <= 2 or re.match(r"^[A-Z]$", line):
                continue
            phonetic = ""
            m = re.search(r"\[([^\]]+)\]", line)
            if m:
                phonetic = m.group(1)
                line = line[:m.start()] + line[m.end():]
            parts = line.split(None, 1)
            if len(parts) < 2 or not re.match(r"^[a-zA-Z]", parts[0]):
                continue
            word = parts[0].strip()
            yield {
                "lemma": word.lower(),
                "text": word,
                "phonetic": phonetic,
                "meaning": parts[1].strip(),
                "level": "OTHER",
                "exchange": {},
                "frequency": 0,
            }


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


_ITERATORS = {
    "ecdict_csv": _iter_ecdict_csv,
    "json": _iter_json,
    "text": _iter_text,
}


def import_dictionary(path, level_filter=None, on_progress=None):
    """导入词典文件。

    path:         文件路径
    level_filter: 只导入指定等级（如 'CET4'），None 表示全部
    on_progress:  回调 fn(已导入数)，用于打印进度

    返回 {"format", "inserted", "skipped", "total"}
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{path}")

    fmt = detect_format(path)
    iterator = _ITERATORS[fmt]

    inserted = skipped = 0
    batch = []
    now = int(time.time())

    with get_db() as conn:
        # 一次性载入已有词，避免逐行查询
        existing = {r[0] for r in conn.execute("SELECT lemma FROM words")}

        for row in iterator(path):
            lemma = row["lemma"]
            if not lemma or not re.match(r"^[a-z]", lemma):
                continue
            if level_filter and row["level"] != level_filter:
                continue
            if lemma in existing:
                skipped += 1
                continue
            existing.add(lemma)
            batch.append((
                lemma,
                row["text"] or lemma,
                row["meaning"],
                row["phonetic"],
                row["level"],
                json.dumps(row["exchange"], ensure_ascii=False) if row["exchange"] else None,
                row["frequency"],
                now,
                now,
            ))
            if len(batch) >= BATCH_SIZE:
                _flush(conn, batch)
                inserted += len(batch)
                batch.clear()
                if on_progress:
                    on_progress(inserted)

        if batch:
            _flush(conn, batch)
            inserted += len(batch)

    return {"format": fmt, "inserted": inserted, "skipped": skipped}


def _flush(conn, batch):
    conn.executemany(
        "INSERT OR IGNORE INTO words "
        "(lemma, text, type, meaning, phonetic, level, exchange, frequency, "
        " status, created_at, updated_at) "
        "VALUES (?, ?, 'word', ?, ?, ?, ?, ?, 'new', ?, ?)",
        batch,
    )
