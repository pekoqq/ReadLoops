"""命令行入口测试。"""
import pytest

from app import cli, config


def test_init_creates_database(capsys):
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "初始化完成" in out
    assert "已建表" in out
    assert config.DB_PATH.exists()


def test_init_warns_when_word_list_empty(capsys):
    cli.main(["init"])
    out = capsys.readouterr().out
    # 隔离测试库里没有词，应给出提示
    assert "词库为空" in out


def test_doctor_runs_and_reports(capsys):
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "环境检查" in out
    assert "Python" in out
    assert "结论" in out


def test_doctor_returns_nonzero_when_not_ready():
    """词库为空时 doctor 应返回非零，便于脚本判断。"""
    assert cli.main(["doctor"]) == 1


def test_default_command_routes_to_serve(monkeypatch):
    """不带子命令时应默认走 serve。"""
    called = {}

    def fake_serve(args):
        called["host"] = args.host
        called["port"] = args.port
        return 0

    monkeypatch.setattr(cli, "cmd_serve", fake_serve)
    assert cli.main([]) == 0
    assert called["port"] == 8000


def test_serve_accepts_custom_port(monkeypatch):
    called = {}

    def fake_serve(args):
        called["port"] = args.port
        called["no_browser"] = args.no_browser
        return 0

    monkeypatch.setattr(cli, "cmd_serve", fake_serve)
    cli.main(["serve", "--port", "9001", "--no-browser"])
    assert called["port"] == 9001
    assert called["no_browser"] is True


def test_help_survives_non_utf8_console(monkeypatch):
    """Windows 控制台可能是 cp1252：打印中文帮助不得 UnicodeEncodeError（回归测试）。"""
    import io
    import sys

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
