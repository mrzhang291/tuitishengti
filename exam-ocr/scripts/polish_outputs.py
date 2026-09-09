#!/usr/bin/env python3
"""Polish MinerU Markdown and HTML outputs for exam reading/printing."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ANSWER = "\u7b54\u6848"
DETAIL = "\u8be6\u89e3"
REFERENCE_ANSWER = "\u53c2\u8003\u7b54\u6848\u4e0e\u89e3\u6790"
CN_NUMS = "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341"
COMMA = "\u3001"
QUESTION = "\u9898"
SUPPORT_HTML_NAMES = {"quality_report.html"}
SUPPORT_MARKDOWN_NAMES = {"ocr_warnings.md"}
OCR_POLLUTION_WARNING = (
    "[OCR\u5f02\u5e38\uff1a\u8be5\u6bb5\u7591\u4f3c\u91cd\u590d/\u5e7b\u89c9\u6c61\u67d3\uff0c"
    "\u5df2\u622a\u65ad\uff0c\u8bf7\u6838\u5bf9\u539f PDF\u3002]"
)

STYLE = r"""
:root { color-scheme: light; --page-bg:#f3f4f6; --paper:#fff; --ink:#111827; --muted:#111827; --line:#d1d5db; --soft:#f9fafb; --accent:#4b5563; --accent-soft:#f3f4f6; --table-head:#f3f4f6; --label-bg:#f3f4f6; --label-ink:#111827; }
* { box-sizing: border-box; }
html { background:var(--page-bg); color:var(--ink); font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Source Han Sans SC",Arial,sans-serif; font-size:16px; }
body { margin:0; background:var(--page-bg); font-size:16px; line-height:1.78; letter-spacing:0; text-rendering:optimizeLegibility; }
.exam-document { width:min(100% - 32px, 980px); margin:28px auto; padding:42px 56px 56px; background:var(--paper); border:1px solid var(--line); border-radius:8px; box-shadow:0 18px 45px rgba(15,23,42,.08); font-size:1rem; }
h1 { margin:0 0 18px; padding-bottom:18px; border-bottom:2px solid var(--ink); text-align:center; font-size:1.5rem; line-height:1.5; font-weight:700; }
h2 { margin:32px 0 16px; padding:10px 14px; border-left:5px solid var(--accent); border-radius:6px; background:var(--accent-soft); color:var(--ink); font-size:1rem; line-height:1.65; font-weight:700; }
p { margin:.56rem 0; color:var(--ink); font-size:1rem; line-height:1.78; }
.exam-document > p:nth-of-type(-n+5) { color:var(--muted); margin:.28rem 0; }
a { color:inherit; text-decoration:none; }
ol { margin:.78rem 0 1rem; padding-left:2.25rem; color:var(--ink); font-size:1rem; line-height:1.78; }
ol > li { margin:.48rem 0 .88rem; padding-left:.15rem; color:var(--ink); font-size:1rem; line-height:1.78; }
ol > li::marker { color:var(--ink); font-weight:700; }
.answer-heading { margin-top:1.45rem; padding-top:1rem; border-top:1px dashed var(--line); font-weight:700; }
.solution-start { margin-top:.8rem; color:var(--ink); font-weight:700; }
.subquestion { margin-left:1.2rem; }
.options { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px 18px; margin:.7rem 0 1rem; padding:10px 14px; color:var(--ink); background:var(--soft); border:1px solid var(--line); border-radius:6px; font-size:1rem; line-height:1.72; }
.option { display:grid; grid-template-columns:2.1rem 1fr; align-items:start; min-width:0; color:var(--ink); font-size:1rem; line-height:1.72; }
.option-label { color:var(--ink); font-weight:700; }
table { width:auto; min-width:46%; max-width:100%; margin:1rem auto 1.15rem; border-collapse:collapse; overflow:hidden; border:1px solid var(--line); border-radius:6px; font-size:1rem; line-height:1.65; }
th,td { border:1px solid var(--line); padding:.48rem .76rem; color:var(--ink); text-align:center; vertical-align:middle; font-size:1rem; line-height:1.65; }
tr:first-child td, th { background:var(--table-head); font-weight:700; }
img { display:block; max-width:min(100%,720px); height:auto; margin:1.1rem auto; border:1px solid var(--line); border-radius:6px; background:#fff; }
math, math * { color:var(--ink); }
math { font-family:"Cambria Math","STIX Two Math","Noto Sans Math","Times New Roman",serif; font-size:1em; line-height:1; }
math[display="block"] { display:block; overflow-x:auto; margin:.55rem 0; padding:0; background:transparent; border-radius:0; font-size:1em; line-height:1.3; text-align:left; }
math[display="inline"] { margin:0 .04rem; font-size:1em; line-height:1; vertical-align:-.06em; }
mjx-container[jax="CHTML"] { color:var(--ink); font-size:1em; line-height:1; }
mjx-container[display="true"] { overflow-x:auto; overflow-y:hidden; margin:.55rem 0 !important; padding:0; background:transparent; border-radius:0; text-align:left !important; }
.MathJax { color:var(--ink); }
.answer-label { display:inline-block; margin:0 .28rem; padding:.06rem .42rem; border-radius:999px; background:var(--label-bg); color:var(--label-ink); border:1px solid var(--line); font-size:.86em; font-weight:700; }
@media (max-width:720px) { .exam-document{width:100%;margin:0;padding:22px 18px 32px;border:0;border-radius:0;box-shadow:none;} h1{font-size:1.36rem;} h2{font-size:1.03rem;} .options{grid-template-columns:1fr;} table{width:100%;font-size:.92rem;} th,td{padding:.42rem .5rem;} }
@media print { html,body{background:#fff;} .exam-document{width:100%;margin:0;padding:0;border:0;border-radius:0;box-shadow:none;} h2,table,img,ol{break-inside:avoid;} h2{break-after:avoid;} .options{break-inside:avoid;} }
"""

MATHJAX_HEAD = r"""
<script data-exam-ocr-mathjax="config">
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    displayMath: [['$$', '$$'], ['\\[', '\\]']],
    processEscapes: true,
    packages: {'[+]': ['ams']}
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  },
  chtml: {
    matchFontHeight: false,
    mtextInheritFont: true,
    displayAlign: 'left',
    displayIndent: '0'
  },
  startup: {
    typeset: true
  }
};
</script>
<script defer data-exam-ocr-mathjax="loader" src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
"""


def get_beautiful_soup():
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4", "-q"])
        from bs4 import BeautifulSoup
    return BeautifulSoup


def get_latex2mathml_converter():
    try:
        from latex2mathml.converter import convert
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "latex2mathml", "-q"])
        from latex2mathml.converter import convert
    return convert


def get_markdown_module():
    try:
        import markdown
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown", "-q"])
        import markdown
    return markdown


OPTION_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-D])\.\s*")
TEXT_OPTION_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-D]\.\s*")
SECTION_PATTERN = re.compile("^[" + CN_NUMS + "]+" + COMMA + ".+" + QUESTION)
ANSWER_HEADING_PATTERN = re.compile(r"^\d+\.\s*(" + ANSWER + ")?")
SUBQUESTION_PATTERN = re.compile(r"^\(\d+\)")
INLINE_QUESTION_START_PATTERN = re.compile(
    r"(?<=[\u3002\uff1b;])(?=(?:[1-9]|1[0-9]|2[0-9]|3[0-9])\.\s*(?:[A-D]{1,4}|\$|\\\(|" + DETAIL + r"|\*\*" + DETAIL + r"\*\*))"
)
RAW_TEX_PATTERN = re.compile(
    r"(?s)(\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<!\\)(?<!\$)\$(?!\$)(.+?)(?<!\\)\$(?!\$))"
)
SKIP_TEX_PARENT_TAGS = {"annotation", "math", "script", "style", "textarea"}
STRAY_TEX_CLOSER_PATTERN = re.compile(r"\\end\{(?:aligned|align|array|matrix|pmatrix|bmatrix|cases)\}")
COMMON_OCR_TYPO_PATTERN = re.compile(r"(?<![A-Za-z])Q(?=g['\u2019]\(x\))")
SET_BUILDER_LEFT_BAR_PATTERN = re.compile(r"(\\left\\\{\s*[^{}$]*?)\\left\|")
LEFT_COMMAND_PATTERN = re.compile(r"\\left\b")
RIGHT_COMMAND_PATTERN = re.compile(r"\\right\b")
LEFT_RIGHT_DELIMITER_PATTERN = re.compile(r"\\(?:left|right)\s*(\\[{}]|[()\[\]{}|])")
LEFT_RIGHT_DOT_PATTERN = re.compile(r"\\(?:left|right)\s*\.")
OCR_POLLUTION_CUT_PATTERNS = [
    re.compile(r"\uff0c?\s*\u5f53\s*0\s*<\s*x\s*<\s*1\s*\u65f6[\uff0c,]\s*\\?\(?\s*p['\u2019]\(x\)\s*<\s*0"),
    re.compile(r"[\uff0c,]\s*\u2234\s*p\(x\)\s*>\s*0\s*[\uff0c,]\s*\u2234\s*p['\u2019]\(x\)\s*>\s*0"),
    re.compile(r"p\(x\)\\geq\s*e\^\{-x\}\s*\+\s*e\^\{x\}\s*\+"),
    re.compile(r"(?:e\^\{w\}\s*\+\s*){2,}"),
    re.compile(r"p\(x\)\\geq\s*e\^\{-u\}\(x\)"),
]


def fix_common_ocr_typos(value: str) -> str:
    return COMMON_OCR_TYPO_PATTERN.sub("", value)


def fix_tex_delimiter_errors(tex: str) -> str:
    tex = SET_BUILDER_LEFT_BAR_PATTERN.sub(lambda match: match.group(1).rstrip() + r" \mid ", tex)
    if len(LEFT_COMMAND_PATTERN.findall(tex)) == len(RIGHT_COMMAND_PATTERN.findall(tex)):
        return tex
    tex = LEFT_RIGHT_DOT_PATTERN.sub("", tex)
    tex = LEFT_RIGHT_DELIMITER_PATTERN.sub(lambda match: match.group(1), tex)
    return re.sub(r"\\(?:left|right)\b\s*", "", tex)


def ocr_pollution_score(value: str) -> int:
    compact = re.sub(r"\s+", "", value)
    score = 0
    if compact.count("p(x)>0") >= 6:
        score += 2
    if compact.count("e^{w}") >= 6 or compact.count("e^w") >= 6:
        score += 2
    if compact.count(r"p(x)\geqe^{-u}(x)") >= 2 or compact.count("p(x)\u2265e^{-u}(x)") >= 2:
        score += 2
    if "Qg'(x)" in compact or "Qg\u2019(x)" in compact:
        score += 1
    return score


def find_ocr_pollution_cut(value: str) -> int | None:
    if ocr_pollution_score(value) < 2:
        return None
    candidates = [match.start() for pattern in OCR_POLLUTION_CUT_PATTERNS if (match := pattern.search(value))]
    return min(candidates) if candidates else None


def sanitize_ocr_pollution_text(value: str, *, markdown: bool = False) -> str:
    cleaned, _ = sanitize_ocr_pollution_text_with_warning(value, markdown=markdown)
    return cleaned


def sanitize_ocr_pollution_text_with_warning(
    value: str,
    *,
    markdown: bool = False,
) -> tuple[str, dict[str, object] | None]:
    if OCR_POLLUTION_WARNING in value:
        return value, None

    cut = find_ocr_pollution_cut(value)
    if cut is None:
        return value, None

    prefix = value[:cut].rstrip(" \t\uff0c,\u3002\uff1b;")
    warning = f"**{OCR_POLLUTION_WARNING}**" if markdown else OCR_POLLUTION_WARNING
    if not prefix:
        cleaned = warning
    else:
        end = "" if prefix.endswith(("\u3002", ".", "\uff01", "!", "\uff1f", "?")) else "\u3002"
        cleaned = f"{prefix}{end} {warning}"

    removed_text = value[cut:].strip()
    record = {
        "type": "ocr_pollution",
        "severity": "warn",
        "action": "truncated_in_output",
        "cut_offset": cut,
        "removed_char_count": len(value) - cut,
        "removed_text": removed_text[:12000],
        "removed_text_truncated": len(removed_text) > 12000,
        "message": OCR_POLLUTION_WARNING,
    }
    return cleaned, record


def marker_warning_records(output_dir: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    html_paths = [path for path in output_dir.glob("*.html") if path.name not in SUPPORT_HTML_NAMES]
    markdown_paths = [path for path in output_dir.glob("*.md") if path.name not in SUPPORT_MARKDOWN_NAMES]
    for path in sorted(markdown_paths + html_paths):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if OCR_POLLUTION_WARNING in line:
                records.append(
                    {
                        "type": "ocr_pollution_marker",
                        "severity": "warn",
                        "action": "marker_present",
                        "file": str(path),
                        "line": line_number,
                        "original_fragment_available": False,
                        "message": OCR_POLLUTION_WARNING,
                    }
                )
    return records


def warning_type_label(value: object) -> str:
    labels = {
        "ocr_pollution": "\u7591\u4f3c OCR \u91cd\u590d/\u5e7b\u89c9\u6c61\u67d3",
        "ocr_pollution_marker": "OCR \u5f02\u5e38\u6807\u8bb0",
    }
    return labels.get(str(value), str(value or "\u672a\u77e5\u7c7b\u578b"))


def warning_action_label(value: object) -> str:
    labels = {
        "truncated_in_output": "\u5df2\u5728\u4e3b\u6587\u6863\u4e2d\u622a\u65ad\u6c61\u67d3\u7247\u6bb5",
        "marker_present": "\u4e3b\u6587\u6863\u4e2d\u5df2\u6709 OCR \u5f02\u5e38\u6807\u8bb0",
    }
    return labels.get(str(value), str(value or "\u672a\u8bb0\u5f55"))


def md_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def warning_location(record: dict[str, object]) -> str:
    parts: list[str] = []
    if record.get("file"):
        parts.append(str(record["file"]))
    if record.get("line"):
        parts.append(f"\u7b2c {record['line']} \u884c")
    if record.get("html_text_node"):
        parts.append(f"HTML \u6587\u672c\u8282\u70b9 {record['html_text_node']}")
    if record.get("cut_offset") is not None:
        parts.append(f"\u622a\u65ad\u4f4d\u7f6e {record['cut_offset']}")
    return "\uff1b".join(parts) if parts else "\u672a\u8bb0\u5f55"


def write_ocr_warnings_markdown(output_dir: Path, payload: dict[str, object]) -> None:
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []

    lines = [
        "# OCR \u5f02\u5e38\u8b66\u544a",
        "",
        f"- \u751f\u6210\u65f6\u95f4\uff1a`{payload.get('generated_at', '')}`",
        f"- \u8b66\u544a\u6570\u91cf\uff1a{len(warnings)}",
        "",
        "\u8bf4\u660e\uff1a`ocr_warnings.json` \u4fdd\u7559\u7ed9\u7a0b\u5e8f\u8bfb\u53d6\uff0c\u672c\u6587\u6863\u7ed9\u4eba\u5de5\u6838\u5bf9\u4f7f\u7528\u3002`_reports` \u662f\u6574\u4f53\u9a8c\u6536\u62a5\u544a\uff0c\u4e0d\u66ff\u4ee3\u672c\u6587\u6863\u7684\u5f02\u5e38\u660e\u7ec6\u3002",
        "",
    ]
    if not warnings:
        lines.extend(["\u672a\u53d1\u73b0\u9700\u8981\u4eba\u5de5\u6838\u5bf9\u7684 OCR \u5f02\u5e38\u3002", ""])
    else:
        lines.extend(
            [
                "| \u5e8f\u53f7 | \u7c7b\u578b | \u7ea7\u522b | \u4f4d\u7f6e | \u5904\u7406 | \u5220\u9664\u5b57\u7b26\u6570 | \u8bf4\u660e |",
                "| --- | --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for index, record in enumerate(warnings, 1):
            if not isinstance(record, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        md_cell(warning_type_label(record.get("type"))),
                        md_cell(record.get("severity", "")),
                        md_cell(warning_location(record)),
                        md_cell(warning_action_label(record.get("action"))),
                        md_cell(record.get("removed_char_count", "")),
                        md_cell(record.get("message", "")),
                    ]
                )
                + " |"
            )
        lines.append("")
        for index, record in enumerate(warnings, 1):
            if not isinstance(record, dict) or not record.get("removed_text"):
                continue
            removed_text = str(record["removed_text"])
            if len(removed_text) > 4000:
                removed_text = removed_text[:4000] + "\n...\n[\u6458\u5f55\u5df2\u622a\u65ad\uff0c\u5b8c\u6574\u5185\u5bb9\u89c1 ocr_warnings.json]"
            lines.extend(
                [
                    f"## \u5f02\u5e38 {index}\uff1a\u539f\u59cb\u7247\u6bb5\u6458\u5f55",
                    "",
                    f"- \u4f4d\u7f6e\uff1a{warning_location(record)}",
                    f"- \u5904\u7406\uff1a{warning_action_label(record.get('action'))}",
                    "",
                    "```text",
                    removed_text,
                    "```",
                    "",
                ]
            )

    (output_dir / "ocr_warnings.md").write_text("\n".join(lines), encoding="utf-8")


def write_ocr_warnings(output_dir: Path, warnings: list[dict[str, object]]) -> None:
    if not warnings:
        warnings = marker_warning_records(output_dir)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    (output_dir / "ocr_warnings.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_ocr_warnings_markdown(output_dir, payload)


def sanitize_ocr_pollution(markdown_text: str) -> str:
    return "\n".join(
        sanitize_ocr_pollution_text(line, markdown=True) for line in markdown_text.split("\n")
    )


def backup_outputs(output_dir: Path, backup_dir: Path, refresh: bool) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    markdown_paths = [path for path in output_dir.glob("*.md") if path.name not in SUPPORT_MARKDOWN_NAMES]
    html_paths = [path for path in output_dir.glob("*.html") if path.name not in SUPPORT_HTML_NAMES]
    for source in markdown_paths + html_paths:
        target = backup_dir / source.name
        if refresh or not target.exists():
            shutil.copy2(source, target)


def append_html(beautiful_soup, parent, html: str) -> None:
    fragment = beautiful_soup(html, "html.parser")
    for node in list(fragment.contents):
        parent.append(node)


def raw_tex_segments(text: str) -> list[tuple[int, int, str, str]]:
    segments: list[tuple[int, int, str, str]] = []
    for match in RAW_TEX_PATTERN.finditer(text):
        token = match.group(0)
        tex = ""
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


def replace_outside_raw_tex(text: str, transform) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end, _, _ in raw_tex_segments(text):
        pieces.append(transform(text[cursor:start]))
        pieces.append(text[start:end])
        cursor = end
    pieces.append(transform(text[cursor:]))
    return "".join(pieces)


def normalize_tex_source(text: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end, tex, display in raw_tex_segments(text):
        token = text[start:end]
        pieces.append(text[cursor:start])
        tex = tex.replace(r"\_", "_").replace("\u2019", "'")
        tex = fix_common_ocr_typos(tex)
        tex = fix_tex_delimiter_errors(tex)
        if token.startswith("$$"):
            pieces.append(f"$${tex}$$")
        elif token.startswith("\\["):
            pieces.append(f"\\[{tex}\\]")
        elif token.startswith("\\("):
            pieces.append(f"\\({tex}\\)")
        else:
            pieces.append(f"${tex}$")
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def convert_raw_tex_in_html(beautiful_soup, soup) -> int:
    converter = None
    converted = 0

    for text_node in list(soup.find_all(string=True)):
        parent = text_node.parent
        if parent is None or parent.name in SKIP_TEX_PARENT_TAGS:
            continue

        text = str(text_node)
        segments = raw_tex_segments(text)
        if not segments:
            continue

        fragment = beautiful_soup("", "html.parser")
        cursor = 0
        changed = False
        for start, end, tex, display in segments:
            if start > cursor:
                fragment.append(text[cursor:start])
            try:
                if converter is None:
                    converter = get_latex2mathml_converter()
                mathml = converter(tex, display=display)
            except Exception:
                fragment.append(text[start:end])
                cursor = end
                continue

            math_soup = beautiful_soup(mathml, "html.parser")
            math_tag = math_soup.find("math")
            if not math_tag:
                fragment.append(text[start:end])
                cursor = end
                continue

            math_tag["data-raw-tex"] = tex
            math_tag["data-render-source"] = "latex2mathml"
            math_tag["display"] = display
            fragment.append(math_tag)
            converted += 1
            changed = True
            cursor = end

        if cursor < len(text):
            fragment.append(text[cursor:])

        if changed:
            text_node.replace_with(*list(fragment.contents))

    return converted


def ensure_mathjax(beautiful_soup, soup) -> None:
    if soup.head is None:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)

    for tag in soup.find_all(attrs={"data-exam-ocr-mathjax": True}):
        tag.decompose()
    append_html(beautiful_soup, soup.head, MATHJAX_HEAD)


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


def render_html_from_markdown(markdown_path: Path, html_path: Path) -> None:
    markdown = get_markdown_module()
    markdown_text = markdown_path.read_text(encoding="utf-8")
    protected_markdown, math_replacements = protect_math_for_markdown(markdown_text)
    body_html = markdown.markdown(
        protected_markdown,
        extensions=["tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )
    for token, replacement in math_replacements.items():
        body_html = body_html.replace(token, replacement)
    title = html.escape(markdown_path.stem)
    document = (
        '<!doctype html>\n'
        '<html lang="zh-CN" data-exam-ocr-html-source="markdown">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f"<style>{STYLE}</style>\n"
        f"{MATHJAX_HEAD}\n"
        "</head>\n"
        '<body><main class="exam-document">\n'
        f"{body_html}\n"
        "</main></body>\n"
        "</html>\n"
    )
    html_path.write_text(document, encoding="utf-8")


def split_options_paragraph(beautiful_soup, soup, paragraph) -> bool:
    if paragraph.parent is None or paragraph.find("img"):
        return False
    text = paragraph.get_text(" ", strip=True)
    if len(TEXT_OPTION_PATTERN.findall(text)) < 2:
        return False

    html = paragraph.decode_contents()
    matches = list(OPTION_PATTERN.finditer(html))
    if len(matches) < 2:
        return False

    lead = html[: matches[0].start()].strip()
    options: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html)
        body = html[match.end() : end].strip()
        if body:
            options.append((match.group(1), body))
    if len(options) < 2:
        return False

    box = soup.new_tag("div")
    box["class"] = "options"
    for label, body in options:
        option = soup.new_tag("div")
        option["class"] = "option"
        option_label = soup.new_tag("span")
        option_label["class"] = "option-label"
        option_label.string = f"{label}."
        option_content = soup.new_tag("span")
        option_content["class"] = "option-content"
        append_html(beautiful_soup, option_content, body)
        option.append(option_label)
        option.append(option_content)
        box.append(option)

    if lead:
        new_paragraph = soup.new_tag("p")
        append_html(beautiful_soup, new_paragraph, lead)
        paragraph.replace_with(new_paragraph)
        new_paragraph.insert_after(box)
    else:
        paragraph.replace_with(box)
    return True


def polish_html(path: Path) -> list[dict[str, object]]:
    beautiful_soup = get_beautiful_soup()
    soup = beautiful_soup(path.read_text(encoding="utf-8"), "html.parser")
    warnings: list[dict[str, object]] = []

    if soup.title:
        h1 = soup.find("h1")
        soup.title.string = h1.get_text(" ", strip=True) if h1 else path.stem

    for style in soup.find_all("style"):
        style.decompose()
    if soup.head is None:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)
    style = soup.new_tag("style")
    style.string = STYLE
    soup.head.append(style)
    ensure_mathjax(beautiful_soup, soup)

    for node_index, text_node in enumerate(list(soup.find_all(string=True)), 1):
        parent = text_node.parent
        if parent is None or parent.name in SKIP_TEX_PARENT_TAGS:
            continue
        cleaned, warning = sanitize_ocr_pollution_text_with_warning(
            fix_common_ocr_typos(str(text_node))
        )
        if cleaned != str(text_node):
            text_node.replace_with(cleaned)
        if warning:
            warning.update({"file": str(path), "html_text_node": node_index})
            warnings.append(warning)

    for anchor in soup.find_all("a"):
        if anchor.get("href") == "1" and ANSWER in anchor.get_text():
            span = soup.new_tag("span")
            span["class"] = "answer-label"
            span.string = anchor.get_text(strip=True)
            anchor.replace_with(span)

    for paragraph in list(soup.find_all("p")):
        text = paragraph.get_text("", strip=True)
        if text == REFERENCE_ANSWER:
            heading = soup.new_tag("h2")
            heading.string = text
            paragraph.replace_with(heading)
        elif SECTION_PATTERN.match(text) and len(text) < 120:
            heading = soup.new_tag("h2")
            heading.string = text
            paragraph.replace_with(heading)

    for paragraph in list(soup.find_all("p")):
        split_options_paragraph(beautiful_soup, soup, paragraph)

    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        classes = paragraph.get("class", [])
        if ANSWER_HEADING_PATTERN.match(text):
            classes.append("answer-heading")
        if text.startswith("[" + DETAIL + "]") or text.startswith(DETAIL):
            classes.append("solution-start")
        if SUBQUESTION_PATTERN.match(text):
            classes.append("subquestion")
        if classes:
            paragraph["class"] = classes

    convert_raw_tex_in_html(beautiful_soup, soup)

    body = soup.body
    if body and not body.find("main", class_="exam-document"):
        main = soup.new_tag("main")
        main["class"] = "exam-document"
        for child in list(body.contents):
            main.append(child.extract())
        body.append(main)

    path.write_text(str(soup), encoding="utf-8")
    return warnings


def split_options_markdown(line: str) -> str:
    matches = list(OPTION_PATTERN.finditer(line))
    if len(matches) < 2:
        return line
    lead = line[: matches[0].start()].rstrip()
    parts: list[str] = []
    if lead:
        parts.append(lead)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        parts.append(f"{match.group(1)}. {line[match.end() : end].strip()}")
    return "\n\n".join(parts)


def polish_markdown(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    text = normalize_tex_source(text)
    text = replace_outside_raw_tex(text, fix_common_ocr_typos)
    warnings: list[dict[str, object]] = []
    cleaned_lines: list[str] = []
    for line_number, raw_line in enumerate(text.split("\n"), 1):
        cleaned_line, warning = sanitize_ocr_pollution_text_with_warning(raw_line, markdown=True)
        cleaned_lines.append(cleaned_line)
        if warning:
            warning.update({"file": str(path), "line": line_number})
            warnings.append(warning)
    text = "\n".join(cleaned_lines)
    text = replace_outside_raw_tex(text, lambda value: STRAY_TEX_CLOSER_PATTERN.sub("", value))
    text = INLINE_QUESTION_START_PATTERN.sub("\n\n", text)
    text = re.sub("(?m)^(" + "\u56db" + COMMA + ".+" + QUESTION + ".*)$", r"## \1", text)
    text = re.sub(r"(?m)^(\d+)\.\[" + ANSWER + r"\]", r"### \1. " + ANSWER + "\n", text)
    text = text.replace("\n" + REFERENCE_ANSWER + "\n", "\n## " + REFERENCE_ANSWER + "\n", 1)
    text = text.replace("[" + DETAIL + "]", "**" + DETAIL + "**")
    text = re.sub(r"(?m)^(\d+\.)\s*(\*\*" + DETAIL + r"\*\*)", r"\1 \2", text)

    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if re.match(r"^\d+\.", line):
            line = re.sub(r"^(\d+)\.\s*", r"### \1. ", line, count=1)
            line = re.sub(r"(###\s+\d+\.\s+.*?)(\*\*" + DETAIL + r"\*\*)", r"\1\n\n\2", line, count=1)
        elif re.match(r"^###\s+\d+\.", line):
            line = re.sub(r"(###\s+\d+\.\s+.*?)(\*\*" + DETAIL + r"\*\*)", r"\1\n\n\2", line, count=1)
        lines.append(split_options_markdown(line))

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?m)^(#{2,3} .+)$", r"\n\1\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    path.write_text(text, encoding="utf-8")
    return warnings


def markdown_image_refs(markdown_path: Path) -> list[str]:
    text = markdown_path.read_text(encoding="utf-8")
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)


def verify_markdown_images(output_dir: Path) -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for markdown_path in sorted(path for path in output_dir.glob("*.md") if path.name not in SUPPORT_MARKDOWN_NAMES):
        for ref in markdown_image_refs(markdown_path):
            if ref.startswith(("http://", "https://", "data:")):
                continue
            if not (markdown_path.parent / ref).exists():
                missing.append((str(markdown_path), ref))
    return missing


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polish MinerU exam Markdown/HTML outputs without calling MinerU.")
    parser.add_argument("output_dir", type=Path, help="Directory containing MinerU .md/.html outputs")
    parser.add_argument("--backup-dir", type=Path, help="Backup directory for raw MinerU .md/.html files")
    parser.add_argument("--no-backup", action="store_true", help="Do not back up original outputs before polishing")
    parser.add_argument("--refresh-backup", action="store_true", help="Overwrite existing backup files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    if not output_dir.exists():
        print(f"Error: output directory not found: {output_dir}", file=sys.stderr)
        return 1

    backup_dir = args.backup_dir.resolve() if args.backup_dir else output_dir / "_raw_mineru"
    if not args.no_backup:
        backup_outputs(output_dir, backup_dir, refresh=args.refresh_backup)

    ocr_warnings: list[dict[str, object]] = []
    for markdown_path in sorted(path for path in output_dir.glob("*.md") if path.name not in SUPPORT_MARKDOWN_NAMES):
        ocr_warnings.extend(polish_markdown(markdown_path))
    for html_path in sorted(path for path in output_dir.glob("*.html") if path.name not in SUPPORT_HTML_NAMES):
        markdown_path = html_path.with_suffix(".md")
        if markdown_path.exists():
            render_html_from_markdown(markdown_path, html_path)
        else:
            ocr_warnings.extend(polish_html(html_path))
    write_ocr_warnings(output_dir, ocr_warnings)

    missing = verify_markdown_images(output_dir)
    print(f"polished: {output_dir}")
    print(f"ocr warnings json: {output_dir / 'ocr_warnings.json'}")
    print(f"ocr warnings md: {output_dir / 'ocr_warnings.md'}")
    if not args.no_backup:
        print(f"raw backup: {backup_dir}")
    if missing:
        print(f"missing image refs: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
