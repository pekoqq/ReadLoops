"""全局配置：路径、AI、主题。所有路径基于项目根目录。"""
import os
from pathlib import Path

# 项目根目录：约读/
ROOT_DIR = Path(__file__).parent.parent
# 数据目录可用环境变量覆盖（便于测试与多环境部署）
DATA_DIR = Path(os.getenv("READLOOPS_DATA_DIR", ROOT_DIR / "data"))
CORPUS_DIR = Path(os.getenv("READLOOPS_CORPUS_DIR", ROOT_DIR / "语料库"))
WEB_DIR = Path(__file__).parent / "web"

DB_PATH = Path(os.getenv("READLOOPS_DB_PATH", DATA_DIR / "yuedu.db"))
DICT_DB_PATH = DATA_DIR / "ecdict.db"

# AI 配置（默认 DeepSeek）
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.deepseek.com")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "deepseek-flash")

# FSRS 配置
FSRS_RETENTION = 0.9
FSRS_MAX_INTERVAL = 365

# 文章参数
TARGET_WORD_COUNT = 300
MIN_NEW_WORDS = 6
MAX_NEW_WORDS = 15
CALIBRATION_ARTICLES = 5

DATA_DIR.mkdir(parents=True, exist_ok=True)
