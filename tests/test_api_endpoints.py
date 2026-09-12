"""接口冒烟测试：所有只读 / 无副作用端点都不得返回 5xx。

存在意义：曾因 `tests` 表缺列，`POST /api/words/test/start` 返回 500，
而当时的测试只覆盖 schema 与纯函数，没能发现。接口级测试才能网住这类问题。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 只读、无副作用、不调用外部 AI 的端点
GET_ENDPOINTS = [
    "/api/health",
    "/api/articles/",
    "/api/words/test/generate",
    "/api/words/review-due",
    "/api/words/vocab",
    "/api/words/srs-stats",
    "/api/stats/overview",
    "/api/stats/activity",
    "/api/stats/recent",
    "/api/settings",
    "/api/reading/lookups",
]


@pytest.mark.parametrize("path", GET_ENDPOINTS)
def test_get_endpoint_no_server_error(path):
    resp = client.get(path)
    assert resp.status_code < 500, f"{path} 返回 {resp.status_code}: {resp.text[:200]}"


def test_start_test_creates_record():
    """POST /api/words/test/start 必须能建记录（曾因 tests 表缺 user_id 列而 500）。"""
    resp = client.post("/api/words/test/start")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["test_id"], int)


def test_stats_test_start_no_server_error():
    resp = client.post("/api/stats/test/start")
    assert resp.status_code < 500, resp.text


def test_stats_test_submit_no_server_error():
    test_id = client.post("/api/stats/test/start").json()["test_id"]
    resp = client.post(f"/api/stats/test/{test_id}/submit", params={"correct": 8, "total": 10})
    assert resp.status_code < 500, resp.text


def test_submit_and_report_flow_no_server_error():
    test_id = client.post("/api/words/test/start").json()["test_id"]
    submit = client.post(
        f"/api/words/test/{test_id}/submit",
        params={"word_id": 1, "is_correct": True, "reaction_time": 1200},
    )
    assert submit.status_code < 500, submit.text
    report = client.get(f"/api/words/test/{test_id}/report")
    assert report.status_code < 500, report.text
