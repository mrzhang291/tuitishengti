#!/usr/bin/env python3
"""Normalize common HTML math forms and convert to DOCX with Pandoc."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from diagnose_html_math import read_text, strip_non_content_blocks
from validate_docx_math import count_omml


SCRIPT_MATH_TAG_RE = re.compile(
    r"<script\b(?=[^>]*\btype=([\"']?)math/tex(?:;\s*mode=display)?\1)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
SCRIPT_MATH_MODE_RE = re.compile(r"type=([\"']?)math/tex;\s*mode=display\1", re.IGNORECASE)


def normalize_script_math(match: re.Match[str]) -> str:
    tag = match.group(0)
    body = html.unescape(match.group(2)).strip()
    if SCRIPT_MATH_MODE_RE.search(tag):
        return f"\\[{body}\\]"
    return f"\\({body}\\)"


def ensure_utf8_meta(source: str) -> str:
    if re.search(r"<meta\b[^>]*charset=", source, re.IGNORECASE):
        return source
    if re.search(r"<head\b[^>]*>", source, re.IGNORECASE):
        return re.sub(
            r"(<head\b[^>]*>)",
            "\\1\n<meta charset=\"utf-8\">",
            source,
            count=1,
            flags=re.IGNORECASE,
        )
    return "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>\n" + source + "\n</body></html>"


def normalize_html(source: str) -> str:
    source = SCRIPT_MATH_TAG_RE.sub(normalize_script_math, source)
    source = strip_non_content_blocks(source)
    return ensure_utf8_meta(source)


def run_pandoc(input_html: Path, output_docx: Path, extra_args: list[str]) -> None:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError("pandoc was not found on PATH.")

    reader = "html+raw_tex+tex_math_dollars+tex_math_single_backslash+tex_math_double_backslash"
    cmd = [
        pandoc,
        str(input_html),
        "--from",
        reader,
        "--to",
        "docx",
        "--output",
        str(output_docx),
    ]
    cmd.extend(extra_args)
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            "pandoc failed\n"
            f"command: {' '.join(cmd)}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    if result.stderr.strip():
        print(result.stderr.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_file", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="Output DOCX path.")
    parser.add_argument("--keep-normalized", type=Path, help="Write normalized intermediate HTML here.")
    parser.add_argument("--pandoc-arg", action="append", default=[], help="Additional argument passed to pandoc.")
    args = parser.parse_args()

    input_file = args.html_file
    output_docx = args.output or input_file.with_suffix(".docx")
    source = read_text(input_file)
    normalized = normalize_html(source)

    with tempfile.TemporaryDirectory(prefix="html-math-to-docx-") as tmp:
        normalized_file = Path(tmp) / (input_file.stem + ".normalized.html")
        normalized_file.write_text(normalized, encoding="utf-8")
        if args.keep_normalized:
            args.keep_normalized.write_text(normalized, encoding="utf-8")
        run_pandoc(normalized_file, output_docx, args.pandoc_arg)

    report = count_omml(output_docx)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["omath_count"]:
        print(f"Created {output_docx} with native Word OMML formulas.")
    else:
        print(
            "Created DOCX, but no native Word OMML formulas were detected. "
            "Run diagnose_html_math.py on the source and inspect formula sources."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
