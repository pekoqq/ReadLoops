"""pytest 全局配置。

关键：在导入 app 之前把数据目录指向临时路径，
避免测试污染真实的 data/yuedu.db。
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP_DATA = Path(tempfile.mkdtemp(prefix="readloops-test-"))
os.environ["READLOOPS_DATA_DIR"] = str(_TMP_DATA)
os.environ["READLOOPS_CORPUS_DIR"] = str(_TMP_DATA / "corpus")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402


@pytest.fixture()
def conn():
    """提供一个已建表的数据库连接，并在使用前清空 words 表。"""
    from app.database import get_db

    with get_db() as c:
        c.execute("DELETE FROM words")
        yield c
