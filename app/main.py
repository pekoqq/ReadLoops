"""FastAPI 入口。"""
import re

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import articles, reading, stats, words
from app.config import WEB_DIR

app = FastAPI(title="ReadLoops", version="2.2.1")

# 注册路由
app.include_router(articles.router)
app.include_router(words.router)
app.include_router(reading.router)
app.include_router(stats.router)

# 静态文件
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# 前端资源版本号占位符，如 `app.js?v=37`；由服务端按文件 mtime 动态替换。
_ASSET_VERSION_RE = re.compile(r"\?v=\d+")


def _asset_version() -> str:
    """以前端资源的最新修改时间作为缓存版本号。

    这样每次改动 app.js / style.css，URL 上的 ?v= 会自动变化，
    浏览器必定重新拉取，无需手动改版本号——彻底避免
    「改了代码但界面没变」的缓存问题。
    """
    latest = 0.0
    for path in (WEB_DIR / "js" / "app.js", WEB_DIR / "css" / "style.css"):
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            pass
    return str(int(latest))


@app.get("/")
async def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = _ASSET_VERSION_RE.sub(lambda m: f"?v={_asset_version()}", html)
    # no-cache：每次使用前都向服务端校验，确保拿到最新的版本号
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
async def manifest():
    return FileResponse(str(WEB_DIR / "manifest.json"))


@app.get("/icon.svg")
async def icon():
    return FileResponse(str(WEB_DIR / "icon.svg"))


@app.get("/api/health")
async def health():
    return {"status": "ok"}
