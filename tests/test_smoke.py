"""冒烟测试：应用可启动、关键路由可用。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_manifest_served():
    resp = client.get("/manifest.json")
    assert resp.status_code == 200


def test_icon_served():
    resp = client.get("/icon.svg")
    assert resp.status_code == 200
