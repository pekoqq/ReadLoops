"""材料导入：把用户自己的文本变成可用的语料。

支持的来源：
- 纯文本粘贴
- .txt / .md / .markdown 文件
- .epub 电子书（EPUB 本质是 zip + xhtml，用标准库解析，不引额外依赖）
- .pdf 扫描件/文档 —— 优先用已安装的解析库；都没有时给出明确的安装指引，
  而不是静默失败（用户最怕的就是「点了没反应」）

设计原则：材料内容只留在用户本机，用于蒸馏与出题，不随项目分发。
"""
from __future__ import annotations

import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Optional

from app.database import get_db

# ---------------------------------------------------------------- 文本处理

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")


def split_paragraphs(text: str) -> list[str]:
    """按空行切段，过滤空白段。"""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def split_sentences(text: str) -> list[str]:
    """切句。英文句末标点后跟大写字母才切，避免 Mr. / U.S. 被切碎。"""
    out = []
    for para in split_paragraphs(text):
        flat = re.sub(r"\s+", " ", para).strip()
        if not flat:
            continue
        out.extend(s for s in _SENT_SPLIT.split(flat) if s.strip())
    return out


def count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'-]*", text))


# ---------------------------------------------------------------- 文件解析

def _parse_txt(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _parse_epub(path: Path) -> str:
    """EPUB = zip(xhtml)。按 spine 顺序取正文，去标签后拼接。"""
    chunks = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
        # 按文件名排序近似阅读顺序（完整实现应解析 OPF spine，这里够用且不引依赖）
        for n in sorted(names):
            try:
                html = z.read(n).decode("utf-8", errors="ignore")
            except Exception:
                continue
            body = re.search(r"<body[^>]*>(.*?)</body>", html, re.S | re.I)
            seg = body.group(1) if body else html
            seg = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", seg, flags=re.S | re.I)
            seg = re.sub(r"<br\s*/?>|</p>|</div>|</h[1-6]>", "\n\n", seg, flags=re.I)
            seg = re.sub(r"<[^>]+>", "", seg)
            seg = re.sub(r"&nbsp;", " ", seg)
            seg = re.sub(r"&amp;", "&", seg)
            seg = re.sub(r"&lt;", "<", seg)
            seg = re.sub(r"&gt;", ">", seg)
            seg = re.sub(r"&quot;", '"', seg)
            seg = re.sub(r"\n{3,}", "\n\n", seg).strip()
            if seg:
                chunks.append(seg)
    return "\n\n".join(chunks)


# MinerU 的独立环境：它要求 Python 3.10–3.13 且依赖很重（20GB+），
# 不能装进项目环境，因此单独建一个 venv，这里用子进程调用。
MINERU_ENV_DIR = Path(__file__).resolve().parent.parent.parent / "tools" / "mineru-env"
MINERU_BIN = MINERU_ENV_DIR / "bin" / "mineru"


def _parse_pdf_with_mineru(path: Path) -> tuple[str, str]:
    """用独立环境里的 MinerU 解析（扫描件 / 双栏 / 复杂表格效果最好）。"""
    if not MINERU_BIN.exists():
        return "", "未安装"
    import subprocess
    import tempfile

    # 用干净的环境变量启动：某些运行环境会通过 PYTHONPATH 注入 sitecustomize，
    # 干扰子进程里的 pip / 文件操作；MinerU 自己也不需要这些变量。
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME")}
    env.setdefault("MINERU_MODEL_SOURCE", os.getenv("MINERU_MODEL_SOURCE", "modelscope"))
    # 模型缓存也放在项目 data 目录：避免默认 ~/.modelscope 在受限环境里
    # 不能原子替换 session 文件，同时数据目录本来就由 .gitignore 排除。
    mineru_data = Path(__file__).resolve().parent.parent.parent / "data"
    env.setdefault("MODELSCOPE_CACHE", str(mineru_data / "mineru-cache"))
    env.setdefault("MODELSCOPE_HOME", str(mineru_data / "mineru-home"))

    with tempfile.TemporaryDirectory() as tmp:
        try:
            # -b pipeline：纯 CPU 后端，不依赖 GPU
            proc = subprocess.run(
                [str(MINERU_BIN), "-p", str(path), "-o", tmp, "-b", "pipeline"],
                capture_output=True, text=True, timeout=900, env=env,
            )
        except subprocess.TimeoutExpired:
            return "", "MinerU 解析超时（超过 15 分钟）"
        except Exception as e:
            return "", f"MinerU 调用失败：{e}"

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            return "", "MinerU 解析失败：" + " / ".join(tail)

        mds = sorted(Path(tmp).rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
        if not mds:
            return "", "MinerU 未产出 Markdown"
        return mds[0].read_text(encoding="utf-8", errors="ignore"), ""


def _parse_pdf(path: Path) -> tuple[str, str]:
    """返回 (正文, 错误说明)。

    优先级：MinerU（效果好）→ pypdf（轻量）→ 给出可操作的安装提示。
    任何一步失败都不会静默返回空，而是把原因带出去给用户看。
    """
    # 1) MinerU：面向 RAG 的解析引擎，扫描件/复杂版式效果最好
    text, err = _parse_pdf_with_mineru(path)
    if text:
        return text, ""
    mineru_err = err if err != "未安装" else ""

    # 2) pypdf：轻量，纯文本 PDF 够用
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
        text = "\n\n".join(pages).strip()
        if text:
            return text, ""
        fallback_note = "PDF 未提取到文本（可能是扫描件，需要 OCR）"
    except ImportError:
        fallback_note = "未安装 pypdf"
    except Exception as e:
        fallback_note = f"pypdf 解析失败：{e}"

    # 3) 都没成 —— 明确告诉用户装什么，而不是静默失败
    hint = (
        "解析 PDF 需要额外依赖：\n"
        "  · 轻量（纯文本 PDF）：pip install pypdf\n"
        "  · 扫描件/复杂版式（推荐）：bash tools/setup_mineru.sh"
    )
    detail = "；".join(x for x in (mineru_err, fallback_note) if x)
    return "", f"{detail}\n{hint}"


PARSERS = {
    ".txt": _parse_txt,
    ".text": _parse_txt,
    ".md": _parse_txt,
    ".markdown": _parse_txt,
    ".epub": _parse_epub,
}


def parse_file(path: Path) -> tuple[str, str]:
    """解析文件为纯文本，返回 (正文, 错误说明)。"""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(path)
    parser = PARSERS.get(ext)
    if not parser:
        return "", f"暂不支持的文件类型：{ext}（支持 .txt/.md/.epub/.pdf）"
    try:
        return parser(path), ""
    except Exception as e:
        return "", f"解析失败：{e}"


# ---------------------------------------------------------------- 入库

def _persist(title: str, content: str, source_type: str,
             source_path: Optional[str] = None) -> int:
    """写入 materials 表，返回 id。

    同一个文件路径重复导入时**更新原记录**而不是新增，
    否则重复导入会让材料列表堆满副本，蒸馏时也会把同一份内容算两遍。
    """
    now = int(time.time())
    paras = split_paragraphs(content)
    sents = split_sentences(content)
    wc = count_words(content)
    with get_db() as db:
        if source_path:
            row = db.execute(
                "SELECT id FROM materials WHERE source_path=?", (source_path,)
            ).fetchone()
            if row:
                db.execute(
                    """UPDATE materials SET title=?, content=?, word_count=?,
                       para_count=?, sentence_count=?, parse_status='parsed',
                       error=NULL, updated_at=? WHERE id=?""",
                    (title, content, wc, len(paras), len(sents), now, row["id"]),
                )
                return row["id"]

        cur = db.execute(
            """INSERT INTO materials
               (title, source_type, source_path, content, word_count,
                para_count, sentence_count, parse_status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (title, source_type, source_path, content, wc,
             len(paras), len(sents), "parsed", now, now),
        )
        return cur.lastrowid


def import_text(title: str, content: str) -> dict:
    """导入粘贴的纯文本。"""
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "内容为空"}
    mid = _persist(title or "未命名材料", content, "paste")
    return {"ok": True, "material_id": mid, "word_count": count_words(content)}


def import_file(path: str | Path, title: Optional[str] = None) -> dict:
    """导入本地文件。"""
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": False, "error": f"文件不存在：{p}"}

    text, err = parse_file(p)
    if err or not text.strip():
        # 失败也记一条，方便前端展示原因（用户能看懂为什么没成功）
        now = int(time.time())
        with get_db() as db:
            cur = db.execute(
                """INSERT INTO materials
                   (title, source_type, source_path, parse_status, error, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (title or p.stem, "file", str(p), "failed", err, now, now),
            )
            mid = cur.lastrowid
        return {"ok": False, "material_id": mid, "error": err}

    mid = _persist(title or p.stem, text, "file", str(p))
    return {"ok": True, "material_id": mid, "word_count": count_words(text)}


def import_book(book_id: int) -> dict:
    """把书架里已下载的书导入为材料（去掉 Gutenberg 页眉页脚）。"""
    from app.services.bookshelf import get_book, strip_gutenberg_boilerplate

    book = get_book(book_id)
    if not book:
        return {"ok": False, "error": "书籍不存在"}
    lp = book.get("local_path")
    if not lp or not Path(lp).exists():
        return {"ok": False, "error": "本地文件缺失，请重新下载"}

    p = Path(lp)
    text, err = parse_file(p)
    if err or not text.strip():
        return {"ok": False, "error": err or "解析为空"}
    if book.get("source") == "gutenberg" and p.suffix.lower() == ".txt":
        text = strip_gutenberg_boilerplate(text)

    mid = _persist(book["title"], text, "book", str(p))
    with get_db() as db:
        db.execute("UPDATE books SET status='imported', updated_at=? WHERE id=?",
                   (int(time.time()), book_id))
    return {"ok": True, "material_id": mid, "word_count": count_words(text)}


# ---------------------------------------------------------------- 查询

def list_materials() -> list[dict]:
    """列表不返回 content（可能很大），只给统计信息。"""
    with get_db() as db:
        rows = db.execute(
            """SELECT id, title, source_type, source_path, word_count, para_count,
                      sentence_count, parse_status, error, created_at
               FROM materials ORDER BY created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_material(material_id: int, with_content: bool = False) -> Optional[dict]:
    cols = "*" if with_content else (
        "id, title, source_type, source_path, word_count, para_count, "
        "sentence_count, parse_status, error, created_at"
    )
    with get_db() as db:
        row = db.execute(f"SELECT {cols} FROM materials WHERE id=?", (material_id,)).fetchone()
        return dict(row) if row else None


def delete_material(material_id: int) -> bool:
    with get_db() as db:
        cur = db.execute("DELETE FROM materials WHERE id=?", (material_id,))
        return cur.rowcount > 0
