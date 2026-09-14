"""FastAPI 入口。"""
import re
from importlib import metadata

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import articles, books, graph, materials, reading, stats, words
from app.config import WEB_DIR

try:
    __version__ = metadata.version('readloops')
except metadata.PackageNotFoundError:  # 源码直接运行、未 pip install 时
    __version__ = '0.0.0.dev'

app = FastAPI(title="ReadLoops", version=__version__)

# 注册路由
app.include_router(articles.router)
app.include_router(words.router)
app.include_router(reading.router)
app.include_router(stats.router)
app.include_router(books.router)
app.include_router(materials.router)
app.include_router(graph.router)

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


@app.get("/icon.svg", include_in_schema=False)
async def icon():
    return FileResponse(str(WEB_DIR / "icon.svg"), headers={"Cache-Control": "no-cache"})


# favicon / iOS 主屏 / PWA 各尺寸 PNG（与 manifest.json 中的路径对应）
_ICON_ROUTES = {
    '/favicon.ico': 'favicon.ico',
    '/favicon-16.png': 'favicon-16.png',
    '/favicon-32.png': 'favicon-32.png',
    '/apple-touch-icon.png': 'apple-touch-icon.png',
    '/icon-192.png': 'icon-192.png',
    '/icon-512.png': 'icon-512.png',
    '/icon-maskable-512.png': 'icon-maskable-512.png',
}
for _url, _fname in _ICON_ROUTES.items():
    def _icon_endpoint(_fname: str = _fname):
        return FileResponse(str(WEB_DIR / _fname), headers={"Cache-Control": "no-cache"})
    app.get(_url, include_in_schema=False)(_icon_endpoint)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
