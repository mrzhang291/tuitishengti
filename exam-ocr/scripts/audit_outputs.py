#!/usr/bin/env python3
"""Audit polished exam OCR outputs against raw MinerU files and local cache."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


RAW_TEX_PATTERN = re.compile(
    r"(?s)(\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<!\\)(?<!\$)\$(?!\$)(.+?)(?<!\\)\$(?!\$))"
)
SKIP_TEX_PARENT_TAGS = {"annotation", "math", "script", "style", "textarea"}
SUPPORT_HTML_NAMES = {"quality_report.html"}
SUPPORT_MARKDOWN_NAMES = {"ocr_warnings.md"}
OCR_POLLUTION_WARNING_PREFIX = "[OCR\u5f02\u5e38"
COMMON_OCR_TYPO_PATTERN = re.compile(r"(?<![A-Za-z])Qg['\u2019]\(x\)")
SET_BUILDER_LEFT_BAR_PATTERN = re.compile(r"\\left\\\{\s*[^{}$]*?\\left\|")
LEFT_COMMAND_PATTERN = re.compile(r"\\left\b")
RIGHT_COMMAND_PATTERN = re.compile(r"\\right\b")


def get_beautiful_soup():
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4", "-q"])
        from bs4 import BeautifulSoup
    return BeautifulSoup


def get_markdown_module():
    try:
        import markdown
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown", "-q"])
        import markdown
    return markdown


def sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def suspicious_ocr_pollution_count(value: str) -> int:
    compact = compact_text(value)
    count = 0
    if compact.count("p(x)>0") >= 6:
        count += 1
    if compact.count("e^{w}") >= 6 or compact.count("e^w") >= 6:
        count += 1
    if compact.count(r"p(x)\geqe^{-u}(x)") >= 2 or compact.count("p(x)\u2265e^{-u}(x)") >= 2:
        count += 1
    if ("Qg'(x)" in compact or "Qg\u2019(x)" in compact) and count:
        count += 1
    return count


def common_ocr_typo_count(value: str) -> int:
    return len(COMMON_OCR_TYPO_PATTERN.findall(value))


def tex_delimiter_error_risk_count(value: str) -> int:
    count = 0
    for _, _, tex, _ in raw_tex_segments(value):
        if (
            SET_BUILDER_LEFT_BAR_PATTERN.search(tex)
            or len(LEFT_COMMAND_PATTERN.findall(tex)) != len(RIGHT_COMMAND_PATTERN.findall(tex))
        ):
            count += 1
    return count


def soup_for(path: Path):
    return get_beautiful_soup()(path.read_text(encoding="utf-8"), "html.parser")


def markdown_soup_for(path: Path):
    markdown = get_markdown_module()
    protected_markdown, math_replacements = protect_math_for_markdown(path.read_text(encoding="utf-8"))
    html = markdown.markdown(
        protected_markdown,
        extensions=["tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )
    for token, replacement in math_replacements.items():
        html = html.replace(token, replacement)
    return get_beautiful_soup()(html, "html.parser")


def math_equivalent_text(math_tag) -> str:
    raw_tex = math_tag.get("data-raw-tex")
    if raw_tex:
        return f"${raw_tex}$"
    annotation = math_tag.find("annotation")
    if annotation:
        return annotation.get_text()
    return math_tag.get_text("", strip=True)


def equivalent_text(root) -> str:
    parts: list[str] = []

    def visit(node) -> None:
        name = getattr(node, "name", None)
        if name in {"script", "style"}:
            return
        if name == "math":
            parts.append(math_equivalent_text(node))
            return
        if isinstance(node, str):
            parts.append(str(node))
            return
        for child in getattr(node, "contents", []):
            visit(child)

    visit(root)
    return normalize_text(" ".join(parts))


def body_text(path: Path) -> str:
    soup = soup_for(path)
    if soup.body:
        return equivalent_text(soup.body)
    return equivalent_text(soup)


def annotations(path: Path) -> list[str]:
    return [item.get_text() for item in soup_for(path).find_all("annotation")]


def image_sources(path: Path) -> list[str]:
    return [item.get("src", "") for item in soup_for(path).find_all("img")]


def table_texts(path: Path) -> list[list[str]]:
    tables: list[list[str]] = []
    for table in soup_for(path).find_all("table"):
        tables.append([equivalent_text(cell) for cell in table.find_all(["td", "th"])])
    return tables


def raw_tex_fragment_count(soup) -> int:
    count = 0
    for text_node in soup.find_all(string=True):
        parent = text_node.parent
        if parent is None or parent.name in SKIP_TEX_PARENT_TAGS:
            continue
        count += len(RAW_TEX_PATTERN.findall(str(text_node)))
    return count


def raw_tex_segments(text: str) -> list[tuple[int, int, str, str]]:
    segments: list[tuple[int, int, str, str]] = []
    for match in RAW_TEX_PATTERN.finditer(text):
        token = match.group(0)
        display = "inline"
        if token.startswith("$$"):
            tex = match.group(2) or ""
            display = "block"
        elif token.startswith("\\["):
            tex = match.group(3) or ""
            display = "block"
        elif token.startswith("\\("):
            tex = match.group(4) or ""
        else:
            tex = match.group(5) or ""
        tex = tex.strip()
        if tex:
            segments.append((match.start(), match.end(), tex, display))
    return segments


def math_html(tex: str, display: str) -> str:
    escaped = html.escape(tex, quote=False)
    if display == "block":
        return f"\\[{escaped}\\]"
    return f"\\({escaped}\\)"


def protect_math_for_markdown(markdown_text: str) -> tuple[str, dict[str, str]]:
    pieces: list[str] = []
    replacements: dict[str, str] = {}
    cursor = 0
    for index, (start, end, tex, display) in enumerate(raw_tex_segments(markdown_text)):
        token = f"@@EXAM_OCR_MATH_{index}@@"
        pieces.append(markdown_text[cursor:start])
        pieces.append(token)
        replacements[token] = math_html(tex, display)
        cursor = end
    pieces.append(markdown_text[cursor:])
    return "".join(pieces), replacements


def mathjax_enabled(soup) -> bool:
    if soup.find(attrs={"data-exam-ocr-mathjax": "config"}):
        return True
    for script in soup.find_all("script"):
        src = script.get("src", "")
        if "mathjax" in src.lower():
            return True
        if "window.MathJax" in script.get_text():
            return True
    return False


def html_source(soup) -> str:
    html_tag = soup.find("html")
    if html_tag:
        return html_tag.get("data-exam-ocr-html-source", "mineru")
    return "mineru"


def html_counts(path: Path) -> dict[str, Any]:
    soup = soup_for(path)
    option_labels = soup.select(".option-label")
    raw_tex_fragments = raw_tex_fragment_count(soup)
    has_mathjax = mathjax_enabled(soup)
    visible_text = equivalent_text(soup.body or soup)
    raw_html = path.read_text(encoding="utf-8")
    return {
        "bytes": path.stat().st_size,
        "sha16": sha16(path),
        "html_source": html_source(soup),
        "mathjax_enabled": has_mathjax,
        "h1": len(soup.find_all("h1")),
        "h2": len(soup.find_all("h2")),
        "p": len(soup.find_all("p")),
        "ol": len(soup.find_all("ol")),
        "li": len(soup.find_all("li")),
        "table": len(soup.find_all("table")),
        "img": len(soup.find_all("img")),
        "math": len(soup.find_all("math")),
        "annotation": len(soup.find_all("annotation")),
        "options_boxes": len(soup.select(".options")),
        "option_labels": len(option_labels),
        "empty_option_labels": sum(1 for item in option_labels if not item.get_text(strip=True)),
        "answer_labels": len(soup.select(".answer-label")),
        "main_exam_document": bool(soup.select_one("main.exam-document")),
        "raw_tex_fragments": raw_tex_fragments,
        "unprotected_raw_tex_fragments": 0 if has_mathjax else raw_tex_fragments,
        "suspicious_ocr_pollution": suspicious_ocr_pollution_count(visible_text),
        "common_ocr_typos": common_ocr_typo_count(visible_text),
        "tex_delimiter_error_risks": tex_delimiter_error_risk_count(raw_html),
        "ocr_pollution_warnings": visible_text.count(OCR_POLLUTION_WARNING_PREFIX),
    }


def markdown_image_refs(markdown_path: Path) -> list[str]:
    text = markdown_path.read_text(encoding="utf-8")
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)


def verify_markdown_images(output_dir: Path) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for markdown_path in sorted(path for path in output_dir.glob("*.md") if path.name not in SUPPORT_MARKDOWN_NAMES):
        for ref in markdown_image_refs(markdown_path):
            if ref.startswith(("http://", "https://", "data:")):
                continue
            if not (markdown_path.parent / ref).exists():
                missing.append({"file": str(markdown_path), "ref": ref})
    return missing


def find_cache_full_html(cache_dir: Path | None) -> list[Path]:
    if not cache_dir or not cache_dir.exists():
        return []
    return sorted(cache_dir.rglob("full.html"))


def raw_exists_in_cache(raw_file: Path, cache_full_html: list[Path]) -> bool | None:
    if not cache_full_html:
        return None
    raw_bytes = raw_file.read_bytes()
    return any(raw_bytes == cache_file.read_bytes() for cache_file in cache_full_html)


def warning_records_for_counts(file_path: Path, counts: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if counts["suspicious_ocr_pollution"]:
        warnings.append(
            {
                "type": "suspicious_ocr_pollution",
                "severity": "warn",
                "file": str(file_path),
                "count": counts["suspicious_ocr_pollution"],
                "message": "Suspicious repeated OCR hallucination text remains in the polished output.",
            }
        )
    if counts["common_ocr_typos"]:
        warnings.append(
            {
                "type": "common_ocr_typo",
                "severity": "warn",
                "file": str(file_path),
                "count": counts["common_ocr_typos"],
                "message": "Known OCR glyph glue remains, for example Qg'(x).",
            }
        )
    if counts["ocr_pollution_warnings"]:
        warnings.append(
            {
                "type": "ocr_pollution_warning_marker",
                "severity": "warn",
                "file": str(file_path),
                "count": counts["ocr_pollution_warnings"],
                "message": "The polished output contains OCR异常 markers; verify those parts against the source PDF.",
            }
        )
    if counts["tex_delimiter_error_risks"]:
        warnings.append(
            {
                "type": "tex_delimiter_error_risk",
                "severity": "fail",
                "file": str(file_path),
                "count": counts["tex_delimiter_error_risks"],
                "message": "TeX contains delimiter patterns that can trigger MathJax errors, for example \\left| without matching \\right|.",
            }
        )
    return warnings


def audit_html_file(current: Path, raw_file: Path, cache_full_html: list[Path]) -> dict[str, Any]:
    current_text = body_text(current)
    raw_text = body_text(raw_file)
    current_annotations = annotations(current)
    raw_annotations = annotations(raw_file)
    current_images = image_sources(current)
    raw_images = image_sources(raw_file)
    current_tables = table_texts(current)
    raw_tables = table_texts(raw_file)
    counts = html_counts(current)
    raw_in_cache = raw_exists_in_cache(raw_file, cache_full_html)
    markdown_file = current.with_suffix(".md")
    markdown_reference_text = None
    markdown_text_equal = None
    if counts["html_source"] == "markdown" and markdown_file.exists():
        markdown_reference_text = normalize_text(equivalent_text(markdown_soup_for(markdown_file)))
        markdown_text_equal = current_text == markdown_reference_text

    if markdown_reference_text is not None:
        reference_text = markdown_reference_text
        reference_kind = "markdown"
        annotation_ok = True
        table_ok = True
        image_ok = True
    else:
        reference_text = raw_text
        reference_kind = "raw_html"
        annotation_ok = current_annotations == raw_annotations
        table_ok = current_tables == raw_tables
        image_ok = current_images == raw_images

    text_equal = current_text == reference_text
    compact_equal = compact_text(current_text) == compact_text(reference_text)
    warnings = warning_records_for_counts(current, counts)
    blocking_ok = (
        compact_equal
        and annotation_ok
        and image_ok
        and table_ok
        and counts["empty_option_labels"] == 0
        and counts["unprotected_raw_tex_fragments"] == 0
        and counts["tex_delimiter_error_risks"] == 0
        and raw_in_cache is not False
    )

    return {
        "file": str(current),
        "raw_file": str(raw_file),
        "reference_kind": reference_kind,
        "markdown_reference_file": str(markdown_file) if markdown_reference_text is not None else None,
        "raw_backup_exactly_in_cache": raw_in_cache,
        "text_equal": text_equal,
        "compact_text_equal": compact_equal,
        "markdown_text_equal": markdown_text_equal,
        "annotations_equal": current_annotations == raw_annotations,
        "images_equal": current_images == raw_images,
        "tables_equal": current_tables == raw_tables,
        "current_counts": counts,
        "raw_counts": html_counts(raw_file),
        "blocking_ok": blocking_ok,
        "strict_ok": blocking_ok and not warnings,
        "critical_ok": blocking_ok,
        "warnings": warnings,
    }


def audit_output_dir(output_dir: Path, raw_dir: Path, cache_dir: Path | None) -> dict[str, Any]:
    cache_full_html = find_cache_full_html(cache_dir)
    html_results: list[dict[str, Any]] = []
    missing_raw: list[str] = []

    for current in sorted(path for path in output_dir.glob("*.html") if path.name not in SUPPORT_HTML_NAMES):
        raw_file = raw_dir / current.name
        if not raw_file.exists():
            missing_raw.append(str(raw_file))
            continue
        html_results.append(audit_html_file(current, raw_file, cache_full_html))

    missing_images = verify_markdown_images(output_dir)
    blocking_ok = (
        not missing_raw
        and not missing_images
        and all(item["blocking_ok"] for item in html_results)
    )
    warnings = [warning for item in html_results for warning in item.get("warnings", [])]
    status = "fail" if not blocking_ok else "warn" if warnings else "pass"
    return {
        "ok": blocking_ok,
        "status": status,
        "output_dir": str(output_dir),
        "raw_dir": str(raw_dir),
        "cache_dir": str(cache_dir) if cache_dir else None,
        "cache_full_html_count": len(cache_full_html),
        "missing_raw_html": missing_raw,
        "missing_markdown_image_refs": missing_images,
        "warnings": warnings,
        "html": html_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit polished exam OCR outputs against raw MinerU HTML/MD.")
    parser.add_argument("output_dir", type=Path, help="Directory containing polished .md/.html outputs")
    parser.add_argument("--raw-dir", type=Path, help="Directory containing raw MinerU .md/.html backups")
    parser.add_argument("--cache-dir", type=Path, help="MinerU cache directory; defaults to <output_dir>/mineru_cache")
    parser.add_argument("--json-out", type=Path, help="Optional path for JSON audit report")
    parser.add_argument("--strict", action="store_true", help="Return non-zero for warnings as well as failures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    raw_dir = (args.raw_dir or output_dir / "_raw_mineru").resolve()
    cache_dir = (args.cache_dir or output_dir / "mineru_cache").resolve()

    if not output_dir.exists():
        print(f"Error: output directory not found: {output_dir}", file=sys.stderr)
        return 1
    if not raw_dir.exists():
        print(f"Error: raw backup directory not found: {raw_dir}", file=sys.stderr)
        return 1

    report = audit_output_dir(output_dir, raw_dir, cache_dir if cache_dir.exists() else None)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict:
        return 0 if report["status"] == "pass" else 1
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
