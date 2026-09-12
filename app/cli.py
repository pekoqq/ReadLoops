"""ReadLoops 命令行入口。

用法：
    readloops              启动服务并自动打开浏览器
    readloops serve        只启动服务（不打开浏览器）
    readloops init         初始化数据库并检查状态
    readloops doctor       检查运行环境是否就绪
"""
import argparse
import sys
import threading
import webbrowser


def _masked(key):
    if not key:
        return "(未设置)"
    return key[:8] + "..." if len(key) > 8 else "***"


def cmd_init(args):
    """初始化数据库并报告状态。"""
    from app import config
    from app.database import get_db, init_db

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    with get_db() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        phrases = conn.execute("SELECT COUNT(*) FROM phrases").fetchone()[0]

    print("ReadLoops 初始化完成")
    print(f"  数据目录：{config.DATA_DIR}")
    print(f"  数据库　：{config.DB_PATH}")
    print(f"  已建表　：{len(tables)} 张")
    print(f"  词库　　：{words} 词")
    print(f"  短语库　：{phrases} 条")

    if words == 0:
        print()
        print("⚠️  词库为空，暂时无法生成文章。")
        print("   请导入一份词表，推荐开源词典 ECDICT：")
        print("     https://github.com/skywind3000/ECDICT")
        print("   导入方法见 README 的「数据准备」章节。")
    return 0


def cmd_doctor(args):
    """检查运行环境。"""
    from app import config

    ok = True
    print("ReadLoops 环境检查")
    print(f"  Python　　　：{sys.version.split()[0]}")

    print(f"  数据目录　　：{config.DATA_DIR} "
          f"{'✅' if config.DATA_DIR.exists() else '⚠️  不存在'}")

    if config.DB_PATH.exists():
        print(f"  数据库　　　：{config.DB_PATH} ✅")
        try:
            from app.database import get_db
            with get_db() as conn:
                words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
            if words:
                print(f"  词库　　　　：{words} 词 ✅")
            else:
                print("  词库　　　　：0 词 ⚠️  需导入词表")
                ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"  数据库读取失败：{exc}")
            ok = False
    else:
        print(f"  数据库　　　：{config.DB_PATH} ⚠️  不存在（运行 readloops init）")
        ok = False

    corpus = config.CORPUS_DIR / "真题阅读纯文本" / "all_passages_lazynote.json"
    print(f"  真题语料　　：{'✅ 已提供' if corpus.exists() else '— 未提供（可选，仅影响相似度匹配）'}")

    try:
        from app.database import get_db
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='ai_api_key'").fetchone()
        print(f"  AI Key　　　：{_masked(row[0] if row else '')}")
    except Exception:  # noqa: BLE001
        pass

    print()
    print("结论：" + ("环境就绪 ✅" if ok else "还需完成上述 ⚠️ 项"))
    return 0 if ok else 1


def cmd_import_dict(args):
    """导入词典 / 词表文件。"""
    from app.services.dict_import import import_dictionary

    print(f"导入词表：{args.path}")
    if args.level:
        print(f"  仅导入等级：{args.level}")

    def progress(n):
        print(f"  已导入 {n} 词...")

    try:
        result = import_dictionary(args.path, level_filter=args.level, on_progress=progress)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        return 1

    from app.database import get_db
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]

    print()
    print("导入完成")
    print(f"  识别格式　：{result['format']}")
    print(f"  新增　　　：{result['inserted']} 词")
    print(f"  已存在跳过：{result['skipped']} 词")
    print(f"  词库总量　：{total} 词")
    return 0


def cmd_serve(args):
    """启动服务。"""
    import uvicorn

    url = f"http://{args.host}:{args.port}"

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    print(f"ReadLoops 运行于 {url}")
    print("按 Ctrl+C 停止")
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="readloops",
        description="ReadLoops — AI 驱动的英语阅读训练器",
    )
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="启动服务（默认命令）")
    p_serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p_serve.add_argument("--port", type=int, default=8000, help="端口（默认 8000）")
    p_serve.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("init", help="初始化数据库").set_defaults(func=cmd_init)
    sub.add_parser("doctor", help="检查运行环境").set_defaults(func=cmd_doctor)

    p_import = sub.add_parser("import-dict", help="导入词典 / 词表文件")
    p_import.add_argument("path", help="文件路径（支持 ECDICT CSV/JSON、纯文本词表）")
    p_import.add_argument("--level", help="只导入指定等级，如 CET4")
    p_import.set_defaults(func=cmd_import_dict)

    args = parser.parse_args(argv)

    # 无子命令 → 默认启动服务
    if not getattr(args, "command", None):
        args = parser.parse_args(["serve"] + (argv or []))

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
