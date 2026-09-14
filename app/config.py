"""全局配置：路径、AI、主题。

数据目录的选取规则（跨平台）：
- 源码模式（项目根下存在 pyproject.toml）→ `<项目根>/data`，便于开发调试
- 安装模式（pip install）→ 各系统的用户数据目录，避免写入 site-packages
- 任何时候都可用环境变量 `READLOOPS_DATA_DIR` 覆盖
"""
import os
import sys
from pathlib import Path

# 包所在目录
PACKAGE_DIR = Path(__file__).parent
# 上一级：源码模式下是项目根；安装模式下是 site-packages
ROOT_DIR = PACKAGE_DIR.parent


def _default_data_dir():
    """按运行模式与操作系统选取可写的数据目录。"""
    # 源码模式：项目根下有 pyproject.toml（该文件不会被安装进 site-packages）
    if (ROOT_DIR / "pyproject.toml").exists():
        return ROOT_DIR / "data"

    # 安装模式：使用系统约定的用户数据目录
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "ReadLoops"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ReadLoops"
    # Linux / 其他 Unix
    base = os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "readloops"


# 数据目录可用环境变量覆盖（便于测试与多环境部署）
DATA_DIR = Path(os.getenv("READLOOPS_DATA_DIR") or _default_data_dir())
CORPUS_DIR = Path(os.getenv("READLOOPS_CORPUS_DIR") or ROOT_DIR / "语料库")
WEB_DIR = PACKAGE_DIR / "web"

DB_PATH = Path(os.getenv("READLOOPS_DB_PATH") or DATA_DIR / "yuedu.db")
DICT_DB_PATH = DATA_DIR / "ecdict.db"

# 书架：公共领域书籍的本地存放目录（书籍文件较大，单独放，不进版本库）
LIBRARY_DIR = Path(os.getenv("READLOOPS_LIBRARY_DIR") or DATA_DIR / "library")

# 书架数据源。主源用 Gutenberg 官方（实测稳定）；
# Gutendex 是第三方镜像，实测时好时坏，仅作可选加速，不作为唯一依赖。
GUTENBERG_BASE = "https://www.gutenberg.org"
GUTENBERG_MIRROR = "https://www.gutenberg.org"

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
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
