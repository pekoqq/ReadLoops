#!/usr/bin/env python3
"""绕过 MinerU CLI/API 编排层，直接调用 pipeline Python 接口。

MinerU 3.4.5 的 `mineru` CLI 在本机临时 API 服务上轮询任务状态时会 404。
这个脚本不启动 HTTP 服务：PDF bytes -> doc_analyze_streaming -> middle JSON -> Markdown。

由 app.services.materials 通过独立 Python 3.13 环境调用。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct MinerU pipeline parser")
    parser.add_argument("input", type=Path, help="input PDF")
    parser.add_argument("output", type=Path, help="output directory")
    parser.add_argument("--lang", default="ch", help="OCR language hint")
    parser.add_argument("--method", choices=("auto", "txt", "ocr"), default="auto")
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"输入文件不存在：{args.input}", file=sys.stderr)
        return 2

    # 避免 MinerU 内部并行 worker 在受限环境下引发 semaphore 泄漏/崩溃。
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    from mineru.backend.pipeline.pipeline_analyze import doc_analyze_streaming
    from mineru.backend.pipeline.pipeline_middle_json_mkcontent import union_make
    from mineru.data.data_reader_writer.filebase import FileBasedDataWriter
    from mineru.utils.enum_class import MakeMode

    args.output.mkdir(parents=True, exist_ok=True)
    images_dir = args.output / "images"
    writer = FileBasedDataWriter(str(images_dir))
    pdf_bytes = args.input.read_bytes()
    result: dict[str, object] = {}

    def on_doc_ready(doc_index, model_list, middle_json, ocr_enable):
        result["middle_json"] = middle_json
        result["ocr_enable"] = ocr_enable

    # 关闭表格与公式：英语教材/真题阅读不需要数学公式，减少模型负担与失败面。
    doc_analyze_streaming(
        [pdf_bytes],
        [writer],
        [args.lang],
        on_doc_ready,
        parse_method=args.method,
        formula_enable=False,
        table_enable=False,
        client_side_output_generation=False,
    )

    middle = result.get("middle_json")
    if not isinstance(middle, dict) or not isinstance(middle.get("pdf_info"), list):
        print("MinerU 未返回有效的 middle JSON", file=sys.stderr)
        return 1

    markdown = union_make(middle["pdf_info"], MakeMode.MM_MD, "images")
    out = args.output / f"{args.input.stem}.md"
    out.write_text(markdown, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
