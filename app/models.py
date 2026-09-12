"""数据模型。"""
import time
from dataclasses import dataclass, field


@dataclass
class Article:
    id: int | None = None
    title: str = ""
    content: str = ""
    source: str = "ai"
    word_count: int = 0
    target_words: str = "[]"
    new_word_count: int = 0
    difficulty_score: float | None = None
    reading_time_seconds: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class Word:
    id: int | None = None
    text: str = ""
    meaning: str = ""
    phonetic: str = ""


@dataclass
class WordEncounter:
    id: int | None = None
    word_id: int = 0
    article_id: int | None = None
    context: str = ""
    action: str = "lookup"
    created_at: int = field(default_factory=lambda: int(time.time()))
