"""FastAPI 入口。"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
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


@app.get("/")
async def index():
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/manifest.json")
async def manifest():
    return FileResponse(str(WEB_DIR / "manifest.json"))


@app.get("/icon.svg")
async def icon():
    return FileResponse(str(WEB_DIR / "icon.svg"))


@app.get("/api/health")
async def health():
    return {"status": "ok"}
