#!/usr/bin/env python3
"""Generate a local quality report for polished exam OCR outputs."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORT_HTML_NAMES = {"quality_report.html"}
SUPPORT_MARKDOWN_NAMES = {"ocr_warnings.md"}
OCR_WARNING_PREFIX = "[OCR\u5f02\u5e38"
RAW_TEX_PATTERN = re.compile(
    r"(?s)(\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<!\\)(?<!\$)\$(?!\$)(.+?)(?<!\\)\$(?!\$))"
)
IMG_MD_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
IMG_HTML_PATTERN = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
TAG_PATTERN = re.compile(r"<[^>]+>")
SCRIPT_STYLE_PATTERN = re.compile(r"(?is)<(script|style)\b.*?</\1>")
SET_BUILDER_LEFT_BAR_PATTERN = re.compile(r"\\left\\\{\s*[^{}$]*?\\left\|")
LEFT_COMMAND_PATTERN = re.compile(r"\\left\b")
RIGHT_COMMAND_PATTERN = re.compile(r"\\right\b")


def load_json(path: Path | None) -> Any | None:
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def visible_html_text(value: str) -> str:
    value = SCRIPT_STYLE_PATTERN.sub(" ", value)
    value = TAG_PATTERN.sub(" ", value)
    return html_lib.unescape(re.sub(r"\s+", " ", value)).strip()


def tex_fragment_count(value: str) -> int:
    return len(RAW_TEX_PATTERN.findall(value))


def tex_delimiter_error_risk_count(value: str) -> int:
    count = 0
    for match in RAW_TEX_PATTERN.finditer(value):
        tex = ""
        token = match.group(0)
        if token.startswith("$$"):
            tex = match.group(2) or ""
        elif token.startswith("\\["):
            tex = match.group(3) or ""
        elif token.startswith("\\("):
            tex = match.group(4) or ""
        else:
            tex = match.group(5) or ""
        if (
            SET_BUILDER_LEFT_BAR_PATTERN.search(tex)
            or len(LEFT_COMMAND_PATTERN.findall(tex)) != len(RIGHT_COMMAND_PATTERN.findall(tex))
        ):
            count += 1
    return count


def suspicious_repetition_count(value: str) -> int:
    compact = compact_text(value)
    count = 0
    if compact.count("p(x)>0") >= 6:
        count += 1
    if compact.count("e^{w}") >= 6 or compact.count("e^w") >= 6:
        count += 1
    if compact.count(r"p(x)\geqe^{-u}(x)") >= 2 or compact.count("p(x)\u2265e^{-u}(x)") >= 2:
        count += 1
    return count


def markdown_image_refs(markdown_path: Path) -> list[str]:
    return IMG_MD_PATTERN.findall(markdown_path.read_text(encoding="utf-8"))


def missing_markdown_images(markdown_paths: list[Path]) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for markdown_path in markdown_paths:
        for ref in markdown_image_refs(markdown_path):
            if ref.startswith(("http://", "https://", "data:")):
                continue
            if not (markdown_path.parent / ref).exists():
                missing.append({"file": str(markdown_path), "ref": ref})
    return missing


def long_markdown_units(text: str, threshold: int = 3200) -> int:
    units = [unit.strip() for unit in re.split(r"\n\s*\n", text) if unit.strip()]
    return sum(1 for unit in units if len(unit) > threshold)


def long_html_paragraphs(text: str, threshold: int = 3200) -> int:
    paragraphs = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", text)
    return sum(1 for paragraph in paragraphs if len(visible_html_text(paragraph)) > threshold)


def find_audit_report(output_dir: Path, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit if explicit.exists() else None
    candidates = [
        output_dir / "audit_report.json",
        output_dir / "mineru_cache" / "audit_report.json",
    ]
    candidates.extend(output_dir.rglob("audit_report.json"))
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def summarize_pair(stem: str, markdown_path: Path | None, html_path: Path | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "stem": stem,
        "markdown": str(markdown_path) if markdown_path else None,
        "html": str(html_path) if html_path else None,
        "markdown_exists": bool(markdown_path and markdown_path.exists()),
        "html_exists": bool(html_path and html_path.exists()),
    }
    if markdown_path and markdown_path.exists():
        text = markdown_path.read_text(encoding="utf-8")
        summary.update(
            {
                "markdown_bytes": markdown_path.stat().st_size,
                "markdown_chars": len(text),
                "markdown_tex_fragments": tex_fragment_count(text),
                "markdown_tex_delimiter_error_risks": tex_delimiter_error_risk_count(text),
                "markdown_image_refs": len(markdown_image_refs(markdown_path)),
                "markdown_ocr_warning_markers": text.count(OCR_WARNING_PREFIX),
                "markdown_long_units": long_markdown_units(text),
                "markdown_suspicious_repetition": suspicious_repetition_count(text),
            }
        )
    if html_path and html_path.exists():
        raw_html = html_path.read_text(encoding="utf-8")
        visible_text = visible_html_text(raw_html)
        summary.update(
            {
                "html_bytes": html_path.stat().st_size,
                "html_text_chars": len(visible_text),
                "html_tex_fragments": tex_fragment_count(raw_html),
                "html_tex_delimiter_error_risks": tex_delimiter_error_risk_count(raw_html),
                "html_img_tags": len(IMG_HTML_PATTERN.findall(raw_html)),
                "html_mathjax_enabled": "MathJax" in raw_html or "mathjax" in raw_html.lower(),
                "html_ocr_warning_markers": visible_text.count(OCR_WARNING_PREFIX),
                "html_long_paragraphs": long_html_paragraphs(raw_html),
                "html_suspicious_repetition": suspicious_repetition_count(visible_text),
            }
        )
    return summary


def build_report(output_dir: Path, audit_report_path: Path | None) -> dict[str, Any]:
    markdown_paths = sorted(path for path in output_dir.glob("*.md") if path.name not in SUPPORT_MARKDOWN_NAMES)
    html_paths = sorted(path for path in output_dir.glob("*.html") if path.name not in SUPPORT_HTML_NAMES)
    markdown_by_stem = {path.stem: path for path in markdown_paths}
    html_by_stem = {path.stem: path for path in html_paths}
    stems = sorted(set(markdown_by_stem) | set(html_by_stem))

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    files = [
        summarize_pair(stem, markdown_by_stem.get(stem), html_by_stem.get(stem))
        for stem in stems
    ]

    if not stems:
        failures.append({"type": "no_outputs", "message": "No Markdown or HTML output files found."})

    for item in files:
        if not item["markdown_exists"]:
            failures.append({"type": "missing_markdown", "stem": item["stem"]})
        if not item["html_exists"]:
            failures.append({"type": "missing_html", "stem": item["stem"]})
        if item.get("markdown_bytes") == 0:
            failures.append({"type": "empty_markdown", "file": item["markdown"]})
        if item.get("html_bytes") == 0:
            failures.append({"type": "empty_html", "file": item["html"]})
        if item.get("markdown_long_units", 0) or item.get("html_long_paragraphs", 0):
            warnings.append(
                {
                    "type": "long_content_unit",
                    "severity": "warn",
                    "stem": item["stem"],
                    "markdown_long_units": item.get("markdown_long_units", 0),
                    "html_long_paragraphs": item.get("html_long_paragraphs", 0),
                }
            )
        suspicious = item.get("markdown_suspicious_repetition", 0) + item.get("html_suspicious_repetition", 0)
        if suspicious:
            warnings.append(
                {
                    "type": "suspicious_repetition",
                    "severity": "warn",
                    "stem": item["stem"],
                    "count": suspicious,
                }
            )
        tex_delimiter_risks = item.get("markdown_tex_delimiter_error_risks", 0) + item.get("html_tex_delimiter_error_risks", 0)
        if tex_delimiter_risks:
            failures.append(
                {
                    "type": "tex_delimiter_error_risk",
                    "stem": item["stem"],
                    "count": tex_delimiter_risks,
                    "message": "TeX contains delimiter patterns that can trigger MathJax errors, for example \\left| without matching \\right|.",
                }
            )

    missing_images = missing_markdown_images(markdown_paths)
    for missing in missing_images:
        failures.append({"type": "missing_markdown_image", **missing})

    ocr_warnings_path = output_dir / "ocr_warnings.json"
    ocr_warnings_markdown_path = output_dir / "ocr_warnings.md"
    ocr_warnings = load_json(ocr_warnings_path)
    if ocr_warnings and ocr_warnings.get("warning_count", 0):
        warnings.append(
            {
                "type": "ocr_warning_sidecar",
                "severity": "warn",
                "file": str(ocr_warnings_path),
                "count": ocr_warnings.get("warning_count", 0),
            }
        )

    audit_report = load_json(audit_report_path)
    if audit_report:
        if audit_report.get("status") == "fail" or audit_report.get("ok") is False:
            failures.append({"type": "audit_failed", "file": str(audit_report_path)})
        elif audit_report.get("status") == "warn":
            warnings.append({"type": "audit_warning", "severity": "warn", "file": str(audit_report_path)})

    status = "fail" if failures else "warn" if warnings else "pass"
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "ok": status != "fail",
        "output_dir": str(output_dir),
        "audit_report": str(audit_report_path) if audit_report_path else None,
        "ocr_warnings": str(ocr_warnings_path) if ocr_warnings_path.exists() else None,
        "ocr_warnings_markdown": str(ocr_warnings_markdown_path) if ocr_warnings_markdown_path.exists() else None,
        "file_count": len(files),
        "files": files,
        "missing_markdown_image_refs": missing_images,
        "failures": failures,
        "warnings": warnings,
    }


def write_html_report(report: dict[str, Any], path: Path) -> None:
    status = report["status"]
    status_color = {"pass": "#166534", "warn": "#92400e", "fail": "#991b1b"}.get(status, "#111827")
    rows = []
    for item in report["files"]:
        rows.append(
            "<tr>"
            f"<td>{html_lib.escape(item['stem'])}</td>"
            f"<td>{'yes' if item['markdown_exists'] else 'no'}</td>"
            f"<td>{'yes' if item['html_exists'] else 'no'}</td>"
            f"<td>{item.get('markdown_tex_fragments', 0)}</td>"
            f"<td>{item.get('html_tex_fragments', 0)}</td>"
            f"<td>{item.get('markdown_image_refs', 0)}</td>"
            f"<td>{item.get('html_img_tags', 0)}</td>"
            f"<td>{item.get('markdown_ocr_warning_markers', 0) + item.get('html_ocr_warning_markers', 0)}</td>"
            "</tr>"
        )
    failures = "".join(f"<li>{html_lib.escape(json.dumps(item, ensure_ascii=False))}</li>" for item in report["failures"])
    warnings = "".join(f"<li>{html_lib.escape(json.dumps(item, ensure_ascii=False))}</li>" for item in report["warnings"])
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Exam OCR Quality Report</title>
<style>
html{{font-family:"Microsoft YaHei",Arial,sans-serif;background:#f3f4f6;color:#111827;}}
body{{margin:0;padding:28px;}}
main{{max-width:1080px;margin:auto;background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:28px;}}
h1{{font-size:24px;margin:0 0 16px;}}
.status{{display:inline-block;color:#fff;background:{status_color};border-radius:4px;padding:4px 10px;font-weight:700;}}
table{{width:100%;border-collapse:collapse;margin-top:18px;font-size:14px;}}
th,td{{border:1px solid #d1d5db;padding:8px;text-align:left;vertical-align:top;}}
th{{background:#f3f4f6;}}
code{{background:#f3f4f6;padding:2px 4px;border-radius:4px;}}
li{{margin:6px 0;}}
</style>
</head>
<body><main>
<h1>Exam OCR Quality Report <span class="status">{html_lib.escape(status.upper())}</span></h1>
<p>Output: <code>{html_lib.escape(report['output_dir'])}</code></p>
<p>Generated: <code>{html_lib.escape(report['generated_at'])}</code></p>
<table>
<thead><tr><th>File</th><th>MD</th><th>HTML</th><th>MD TeX</th><th>HTML TeX</th><th>MD Images</th><th>HTML Images</th><th>OCR Warnings</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<h2>Failures</h2>
<ul>{failures or '<li>None</li>'}</ul>
<h2>Warnings</h2>
<ul>{warnings or '<li>None</li>'}</ul>
</main></body></html>
"""
    path.write_text(document, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local quality report for polished exam OCR outputs.")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--audit-report", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--html-out", type=Path)
    parser.add_argument("--strict", action="store_true", help="Return non-zero for warn status as well as fail")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    if not output_dir.exists():
        print(f"Error: output directory not found: {output_dir}", file=sys.stderr)
        return 1

    audit_report_path = find_audit_report(output_dir, args.audit_report.resolve() if args.audit_report else None)
    report = build_report(output_dir, audit_report_path)
    default_report_dir = output_dir / "_reports"
    json_out = (args.json_out or default_report_dir / "quality_report.json").resolve()
    html_out = (args.html_out or default_report_dir / "quality_report.html").resolve()
    json_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html_report(report, html_out)
    print(json.dumps({"status": report["status"], "json": str(json_out), "html": str(html_out)}, ensure_ascii=False))
    if args.strict:
        return 0 if report["status"] == "pass" else 1
    return 0 if report["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
