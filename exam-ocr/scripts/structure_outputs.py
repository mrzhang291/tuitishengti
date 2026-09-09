#!/usr/bin/env python3
"""Create a fresh structured question bank from polished exam OCR Markdown."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import markdown as markdown_lib
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pip install markdown") from exc

try:
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: pip install beautifulsoup4") from exc


SCHEMA_VERSION = "1.0"
SOLUTION_WORDS = ("参考答案", "答案", "解析")
QUESTION_WORDS = ("试卷", "试题", "周测", "作业")
SECTION_NAMES = "单选题|选择题|多选题|填空题|解答题"
SECTION_START_RE = re.compile(
    rf"(?m)^\s*#+\s*(?P<ordinal>[一二三四五六七八九十]+)[、，,.．]\s*(?P<name>{SECTION_NAMES})(?P<rest>[^\n]*)"
)
QUESTION_START_RE = re.compile(
    r"(?m)^(?P<prefix>\s*(?:#{1,6}\s*)?)(?P<number>[1-9]\d?)[．.。:：、]\s*"
)
IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
DETAIL_MARKER_RE = re.compile(r"(?:\*\*)?(?:【)?(?:详解|解析)(?:】)?(?:\*\*)?", re.I)
MATH_FRAGMENT_RE = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|(?<!\\)\$(?!\$)(?:\\.|[^$])*?(?<!\\)\$",
    re.S,
)
ANSWER_CONTAMINATION_RE = re.compile(
    r"(?:^|\s)#{1,6}\s*[1-9]\d?[．.]?|"
    r"(?:^|\s)[1-9]\d?[．.]\s*[A-D](?:\b|\s)|"
    r"(?:^|\s)(?:\*\*(?:详解|解析)\*\*|【(?:详解|解析)】)",
    re.I,
)
MATHJAX_HEAD = r"""
<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    displayMath: [['$$', '$$'], ['\\[', '\\]']],
    processEscapes: true
  },
  svg: {fontCache: 'global'}
};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
"""
STYLE = """
<style>
:root{--ink:#172033;--muted:#697386;--line:#dbe2ea;--surface:#fff;--page:#f4f7fb;--primary:#2458b8;--primary-soft:#eaf1ff;--success:#147a4b;--success-soft:#e8f7ef;--warning:#9a5b00;--warning-soft:#fff4d6;--danger:#b42318;--danger-soft:#fff0ee;--shadow:0 10px 30px rgba(27,45,78,.08)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;margin:0;background:var(--page);color:var(--ink);line-height:1.72}
a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}.page-shell{max-width:1480px;margin:0 auto;padding:28px 24px 56px}.page-shell.narrow{max-width:1060px}
.hero{background:linear-gradient(135deg,#173f86,#2f69c5);color:#fff;border-radius:18px;padding:26px 30px;box-shadow:var(--shadow);margin-bottom:20px}.hero h1{margin:0 0 8px;font-size:28px;line-height:1.3}.hero p{margin:0;color:#e5edff}.hero a{color:#fff;text-decoration:underline}
.summary-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:14px;margin:18px 0}.summary-card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:0 4px 14px rgba(27,45,78,.04)}.summary-card .label{font-size:13px;color:var(--muted)}.summary-card .value{font-size:26px;font-weight:750;margin-top:4px}.summary-card .hint{font-size:12px;color:var(--muted)}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin:18px 0;box-shadow:0 4px 18px rgba(27,45,78,.04)}.panel h2{font-size:20px;margin:0 0 14px}.meta{color:var(--muted)}
.toolbar{display:grid;grid-template-columns:minmax(220px,2fr) repeat(3,minmax(150px,1fr));gap:12px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px;margin:16px 0;position:sticky;top:8px;z-index:5;box-shadow:var(--shadow)}.toolbar input,.toolbar select{width:100%;border:1px solid #c8d1dc;border-radius:9px;background:#fff;padding:10px 12px;font:inherit;color:var(--ink)}.toolbar-status{grid-column:1/-1;color:var(--muted);font-size:13px}
.table-wrap{overflow:auto;background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}table{width:100%;border-collapse:separate;border-spacing:0;margin:0;background:#fff}th{position:sticky;top:0;z-index:2;background:#eef3fa;color:#3b485c;text-align:left;font-size:13px;white-space:nowrap}td,th{border-bottom:1px solid var(--line);padding:11px 12px;vertical-align:top}tbody tr:hover{background:#f8fbff}tbody tr:last-child td{border-bottom:0}
.id-link{font-family:Consolas,"SFMono-Regular",monospace;font-size:13px;font-weight:650;white-space:nowrap}.source-cell{min-width:210px}.number-cell{text-align:center;font-weight:700}.answer-cell{min-width:310px;max-width:520px}.answer-content{overflow-x:auto;line-height:1.65}.answer-content p{margin:0 0 7px}.answer-content p:last-child{margin-bottom:0}.answer-details summary{cursor:pointer;color:var(--primary);font-weight:650}.answer-details[open] summary{margin-bottom:10px}.answer-empty{color:var(--danger);font-weight:650}
.badge{display:inline-flex;align-items:center;gap:4px;border-radius:999px;padding:3px 9px;margin:2px 4px 2px 0;font-size:12px;font-weight:650;white-space:nowrap;border:1px solid transparent}.badge.info{background:var(--primary-soft);color:var(--primary);border-color:#bfd2f7}.badge.success{background:var(--success-soft);color:var(--success);border-color:#b9e4ce}.badge.warning{background:var(--warning-soft);color:var(--warning);border-color:#efd394}.badge.danger{background:var(--danger-soft);color:var(--danger);border-color:#f1beb8}.badge.muted{background:#f1f3f6;color:#5d6674;border-color:#d8dde5}
.status-banner{border-radius:12px;padding:13px 16px;margin:14px 0}.status-banner.success{background:var(--success-soft);color:var(--success);border:1px solid #b9e4ce}.status-banner.warning{background:var(--warning-soft);color:var(--warning);border:1px solid #efd394}.status-banner.danger{background:var(--danger-soft);color:var(--danger);border:1px solid #f1beb8}
.question-content img,.solution-content img{max-width:100%;height:auto}.question-content,.solution-content,.answer-content{overflow-x:auto;overflow-y:hidden;padding-bottom:5px}.question-content> :first-child,.solution-content> :first-child,.answer-content> :first-child{margin-top:0}.question-content> :last-child,.solution-content> :last-child,.answer-content> :last-child{margin-bottom:0}mjx-container[display="true"]{text-align:left!important;margin:.75em 0!important}.back-link{display:inline-block;margin-bottom:12px;color:#e5edff}.flag-list{display:flex;flex-wrap:wrap;gap:4px}.repair-list{display:flex;flex-wrap:wrap;gap:4px}.empty-state{text-align:center;color:var(--muted);padding:34px}.math-scroll{overflow-x:auto}.content-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,.38fr);gap:18px}.content-grid .wide{grid-column:1/-1}.section-kicker{font-size:12px;font-weight:750;letter-spacing:.08em;color:var(--primary);text-transform:uppercase;margin-bottom:5px}.preview-id{font-family:Consolas,"SFMono-Regular",monospace;font-size:13px;opacity:.9}.report-note{font-size:13px;color:var(--muted);margin:8px 0 0}.diagnostic{min-width:150px}.diagnostic-line{display:flex;align-items:flex-start;gap:6px;margin:2px 0}.diagnostic-label{min-width:44px;color:var(--muted);font-size:12px;padding-top:3px}.repair-note{display:block;color:var(--muted);font-size:12px;margin-top:4px}.nowrap{white-space:nowrap}.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:currentColor}.table-caption{padding:12px 15px;color:var(--muted);font-size:13px;border-bottom:1px solid var(--line)}
@media(max-width:900px){.page-shell{padding:16px 10px 36px}.summary-grid{grid-template-columns:repeat(2,minmax(130px,1fr))}.toolbar{position:static;grid-template-columns:1fr 1fr}.toolbar input{grid-column:1/-1}.source-cell{min-width:170px}.answer-cell{min-width:260px}.hero{border-radius:12px;padding:22px 18px}}
@media(max-width:760px){.content-grid{grid-template-columns:1fr}.content-grid .wide{grid-column:auto}}@media(max-width:560px){.summary-grid,.toolbar{grid-template-columns:1fr}.toolbar input,.toolbar-status{grid-column:auto}.hero h1{font-size:23px}}
@media print{body{background:#fff}.page-shell{max-width:none;padding:0}.hero{background:#fff;color:#000;box-shadow:none;border:1px solid #aaa}.hero p,.hero a{color:#333}.toolbar{display:none}.panel,.table-wrap,.summary-card{box-shadow:none}}
</style>
"""

TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "fill_blank": "填空题",
    "solution": "解答题",
    "unknown": "未识别",
}
FLAG_LABELS = {
    "contains_images": "含图片",
    "missing_image_asset": "图片缺失",
    "missing_answer": "缺答案",
    "missing_solution": "缺解析",
    "option_count_mismatch": "选项不完整",
    "unknown_question_type": "题型未识别",
    "answer_contamination": "答案疑似串题",
    "answer_math_display_repaired": "答案公式边界已修复",
    "markdown_math_loss": "公式在 HTML 转换中受损",
    "embedded_supplement_question": "题干含未拆分补充题",
}
REPAIR_LABELS = {
    "removed_answer_card": "移除答题卡",
    "removed_trailing_answer_card": "移除末尾答题卡",
    "joined_broken_decimals": "修复跨行小数",
}
REPAIR_DESCRIPTIONS = {
    "removed_answer_card": "已在切题前移除答题卡区域，避免把表格内容混入题干。",
    "removed_trailing_answer_card": "已移除文档末尾答题卡区域。",
    "joined_broken_decimals": "已合并被 OCR 错误断开的 0.x / 1.x 小数。",
}
BENIGN_FLAGS = {"contains_images", "answer_math_display_repaired"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def is_solution_path(path: Path) -> bool:
    return any(word in nfkc(path.stem) for word in SOLUTION_WORDS)


def exam_identity(stem: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", "", nfkc(stem))
    base = re.sub(r"参考答案|答案|解析|试卷|试题", "", normalized)
    base = base or normalized or "untitled-exam"
    token = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return base, f"exam_{token}"


def choose_solution(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda path: (
            0 if "参考答案" in nfkc(path.stem) else 1,
            0 if "答案" in nfkc(path.stem) else 1,
            len(path.name),
        ),
    )[0]


def title_from_markdown(path: Path) -> str:
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            return compact(match.group(1))
    return path.stem


def discover_cases(ocr_dir: Path) -> list[dict[str, Any]]:
    markdown_paths = [
        path
        for path in sorted(ocr_dir.glob("*.md"))
        if path.name != "ocr_warnings.md" and not path.name.startswith("_")
    ]
    question_paths = [
        path
        for path in markdown_paths
        if not is_solution_path(path) and any(word in nfkc(path.stem) for word in QUESTION_WORDS)
    ]
    solutions: dict[str, list[Path]] = {}
    for path in markdown_paths:
        if is_solution_path(path):
            key, _ = exam_identity(path.stem)
            solutions.setdefault(key, []).append(path)

    cases = []
    for question_path in question_paths:
        key, exam_code = exam_identity(question_path.stem)
        solution_path = choose_solution(solutions.get(key, []))
        cases.append(
            {
                "key": key,
                "exam_code": exam_code,
                "source_exam": title_from_markdown(question_path),
                "question_path": question_path,
                "solution_path": solution_path,
            }
        )
    return sorted(cases, key=lambda case: case["key"])


def put_inline_sections_on_new_lines(text: str) -> str:
    return re.sub(
        rf"(?<!\n)(?=#+\s*[一二三四五六七八九十]+[、，,.．]\s*(?:{SECTION_NAMES}))",
        "\n",
        text,
    )


def remove_answer_card(text: str) -> tuple[str, list[str]]:
    repairs: list[str] = []
    start = re.search(r"(?m)^\s*#+\s*答题卡[^\n]*", text)
    if not start:
        return text, repairs
    following = text[start.end() :]
    section = re.search(r"#+\s*四[、，,.．]\s*解答题", following)
    if section:
        section_pos = start.end() + section.start()
        text = text[: start.start()] + "\n\n" + text[section_pos:]
        repairs.append("removed_answer_card")
    else:
        text = text[: start.start()].rstrip() + "\n"
        repairs.append("removed_trailing_answer_card")
    return text, repairs


def split_embedded_first_solution_question(text: str) -> tuple[str, list[str]]:
    repairs: list[str] = []
    section = re.search(r"(?m)^\s*#+\s*四[、，,.．]\s*解答题[^\n]*", text)
    if not section:
        return text, repairs
    before_numbers = [int(match.group("number")) for match in QUESTION_START_RE.finditer(text[: section.start()])]
    if not before_numbers:
        return text, repairs
    expected = max(before_numbers) + 1
    line = section.group(0)
    marker = re.search(rf"(?<!\d){expected}[．.]\s*(?=[\u4e00-\u9fffA-Za-z])", line)
    if not marker:
        return text, repairs
    replacement = line[: marker.start()].rstrip() + f"\n\n### {expected}. " + line[marker.end() :]
    text = text[: section.start()] + replacement + text[section.end() :]
    repairs.append(f"split_embedded_question_{expected}")
    return text, repairs


def normalize_exam_markdown(text: str) -> tuple[str, list[str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = put_inline_sections_on_new_lines(text)
    text, repairs = remove_answer_card(text)
    text = put_inline_sections_on_new_lines(text)
    text, extra = split_embedded_first_solution_question(text)
    repairs.extend(extra)
    decimal_fixed = re.sub(r"\b([01])\.\s+(\d)\b", r"\1.\2", text)
    if decimal_fixed != text:
        repairs.append("joined_broken_decimals")
    return decimal_fixed, repairs


def normalize_solution_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\b([01])\.\s+(\d)\b", r"\1.\2", text)


def section_kind(name: str) -> str:
    return {
        "单选题": "single_choice",
        "选择题": "single_choice",
        "多选题": "multiple_choice",
        "填空题": "fill_blank",
        "解答题": "solution",
    }.get(name, "unknown")


def parse_sections(text: str) -> list[dict[str, Any]]:
    sections = []
    for match in SECTION_START_RE.finditer(text):
        score_match = re.search(r"每小题\s*(\d+(?:\.\d+)?)\s*分", match.group("rest"))
        count_match = re.search(
            r"[：:]?\s*(?:本题共\s*)?(\d+)\s*小题",
            match.group("rest"),
        )
        score: float | int | None = None
        if score_match:
            numeric = float(score_match.group(1))
            score = int(numeric) if numeric.is_integer() else numeric
        sections.append(
            {
                "start": match.start(),
                "name": match.group("name"),
                "question_type": section_kind(match.group("name")),
                "score": score,
                "declared_count": int(count_match.group(1)) if count_match else None,
            }
        )
    return sections


def section_for_position(sections: list[dict[str, Any]], position: int) -> dict[str, Any]:
    current = {"question_type": "unknown", "score": None, "name": ""}
    if sections and position < sections[0]["start"] and sections[0]["question_type"] == "multiple_choice":
        return {"question_type": "single_choice", "score": None, "name": "inferred_single_choice"}
    for section in sections:
        if section["start"] > position:
            break
        current = section
    return current


def trim_trailing_section(block: str) -> str:
    match = re.search(
        rf"(?m)^\s*#+\s*[一二三四五六七八九十]+[、，,.．]\s*(?:{SECTION_NAMES})[^\n]*\s*$",
        block,
    )
    return block[: match.start()].rstrip() if match else block.rstrip()


def split_question_blocks(text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sections = parse_sections(text)
    matches = list(QUESTION_START_RE.finditer(text))
    blocks = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = trim_trailing_section(text[match.start() : end].strip())
        section = section_for_position(sections, match.start())
        blocks.append(
            {
                "number": int(match.group("number")),
                "body": body,
                "question_type": section["question_type"],
                "score": section["score"],
            }
        )

    numbers = [block["number"] for block in blocks]
    duplicates = sorted(number for number, count in Counter(numbers).items() if count > 1)
    seen = sorted(set(numbers))
    declared_counts = [section.get("declared_count") for section in sections]
    declared_total = (
        sum(declared_counts)
        if declared_counts and all(count is not None for count in declared_counts)
        else None
    )
    expected_end = max(seen) if seen else 0
    if declared_total and declared_total >= expected_end:
        expected_end = declared_total
    missing = sorted(set(range(1, expected_end + 1)) - set(seen)) if expected_end else []
    return blocks, {
        "seen": seen,
        "duplicates": duplicates,
        "missing": missing,
        "declared_total": declared_total,
        "expected_total": expected_end or None,
    }


def extract_options(markdown_text: str) -> dict[str, str]:
    pattern = re.compile(r"(?<![A-Za-z0-9])([A-D])[．.、]\s*")
    matches = list(pattern.finditer(markdown_text))
    options: dict[str, str] = {}
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        options[match.group(1)] = markdown_text[match.end() : end].strip()
    return options


def rows_from_html_table(table) -> list[list[str]]:
    rows = []
    for row in table.find_all("tr"):
        cells = [compact(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def map_answer_rows(rows: list[list[str]], answers: dict[int, str]) -> None:
    for idx, row in enumerate(rows):
        if not row or not any("题号" in cell for cell in row):
            continue
        answer_row = next((candidate for candidate in rows[idx + 1 :] if candidate and "答案" in candidate[0]), None)
        if not answer_row:
            continue
        for number, answer in zip(row[1:], answer_row[1:]):
            if number.isdigit() and answer:
                answers[int(number)] = answer
    for idx, row in enumerate(rows[:-1]):
        if row and all(cell.isdigit() for cell in row):
            for number, answer in zip(row, rows[idx + 1]):
                if answer:
                    answers[int(number)] = answer


def extract_table_answers(solution_text: str) -> dict[int, str]:
    answers: dict[int, str] = {}
    soup = BeautifulSoup(solution_text, "html.parser")
    for table in soup.find_all("table"):
        map_answer_rows(rows_from_html_table(table), answers)

    lines = solution_text.splitlines()
    idx = 0
    while idx < len(lines):
        if not lines[idx].strip().startswith("|"):
            idx += 1
            continue
        block = []
        while idx < len(lines) and lines[idx].strip().startswith("|"):
            block.append(lines[idx].strip())
            idx += 1
        rows = [[compact(cell) for cell in line.strip("|").split("|")] for line in block]
        rows = [row for row in rows if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in row if cell)]
        map_answer_rows(rows, answers)
    return answers


def split_chinese_values(value: str) -> list[str]:
    return [compact(item) for item in re.split(r"\s*[，；;]\s*", value) if compact(item)]


def truncate_answer_contamination(value: str) -> str:
    value = compact(value)
    match = ANSWER_CONTAMINATION_RE.search(value)
    if match and match.start() > 0:
        value = value[: match.start()]
    return value.rstrip("，；; ")


def extract_numbered_answer_values(value: str, valid_numbers: set[int]) -> dict[int, str]:
    marker_pattern = re.compile(r"(?<![\dA-Za-z])([1-9]\d?)[．.]\s*(?!\d)")
    markers = list(marker_pattern.finditer(value))
    answers: dict[int, str] = {}
    for idx, marker in enumerate(markers):
        number = int(marker.group(1))
        if number not in valid_numbers:
            continue
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(value)
        answer = truncate_answer_contamination(value[marker.end() : end])
        answer = re.sub(r"^#+\s*", "", answer).strip()
        if answer:
            answers[number] = answer
    return answers


def answer_summary_header(solution_text: str, valid_fill_numbers: set[int]) -> str:
    limit = min(len(solution_text), 3000)
    detail = DETAIL_MARKER_RE.search(solution_text, 0, limit)
    if detail:
        limit = min(limit, detail.start())
    for heading in re.finditer(r"(?m)^\s*#{1,6}\s*([1-9]\d?)[．.]", solution_text[:limit]):
        if int(heading.group(1)) not in valid_fill_numbers:
            limit = min(limit, heading.start())
            break
    return solution_text[:limit]


def extract_summary_answers(solution_text: str, type_numbers: dict[str, list[int]]) -> dict[int, str]:
    answers: dict[int, str] = {}
    single = re.search(r"单选题[：:]\s*([^；;\n]+)", solution_text)
    if single:
        letters = re.sub(r"[^A-D]", "", single.group(1).upper())
        for number, answer in zip(type_numbers.get("single_choice", []), letters):
            answers[number] = answer
    multiple = re.search(r"多选题[：:]\s*([^\n]+)", solution_text)
    if multiple:
        values = [re.sub(r"[^A-D]", "", item.upper()) for item in split_chinese_values(multiple.group(1))]
        values = [value for value in values if value]
        for number, answer in zip(type_numbers.get("multiple_choice", []), values):
            answers[number] = answer
    valid_fill_numbers = set(type_numbers.get("fill_blank", []))
    fill = re.search(r"填空题[：:]\s*([^\n]+)", solution_text)
    if fill:
        numbered = extract_numbered_answer_values(fill.group(1), valid_fill_numbers)
        if numbered:
            answers.update(numbered)
        else:
            values = [truncate_answer_contamination(value) for value in split_chinese_values(fill.group(1))]
            for number, answer in zip(type_numbers.get("fill_blank", []), values):
                if answer:
                    answers[number] = answer
    header = answer_summary_header(solution_text, valid_fill_numbers)
    for number, answer in extract_numbered_answer_values(header, valid_fill_numbers).items():
        answers.setdefault(number, answer)
    return answers


def strong_solution_markers(
    text: str,
    valid_numbers: set[int],
    question_types: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    pattern = re.compile(r"(?m)^\s*(?:#{1,6}\s*)?([1-9]\d?)[．.]\s*")
    raw_matches = list(pattern.finditer(text))
    selected = []
    for idx, match in enumerate(raw_matches):
        number = int(match.group(1))
        if number not in valid_numbers:
            continue
        end = raw_matches[idx + 1].start() if idx + 1 < len(raw_matches) else len(text)
        candidate_block = text[match.end() : end]
        embedded_numbers = [
            int(item.group(1))
            for item in re.finditer(r"(?<![\dA-Za-z])([1-9]\d?)[．.]\s*(?!\d)", candidate_block)
            if int(item.group(1)) in valid_numbers
        ]
        if (
            (question_types or {}).get(number) == "fill_blank"
            and embedded_numbers
            and not DETAIL_MARKER_RE.search(candidate_block)
        ):
            # A heading such as "### 12. ... 13. ... 14. ..." is a compact
            # fill-answer summary, not the beginning of question 12's solution.
            continue
        if selected and number <= selected[-1]["number"]:
            continue
        selected.append({"number": number, "start": match.start(), "content_start": match.end()})
    return selected


def find_inline_marker(text: str, number: int, start: int, end: int) -> dict[str, Any] | None:
    pattern = re.compile(rf"(?<![\dA-Za-z]){number}[．.]\s*(?!\d)(?=\S)")
    match = pattern.search(text, start, end)
    if not match:
        return None
    return {"number": number, "start": match.start(), "content_start": match.end()}


def solution_markers(
    text: str,
    valid_numbers: set[int],
    question_types: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    strong = strong_solution_markers(text, valid_numbers, question_types)
    if not strong:
        return []
    selected: list[dict[str, Any]] = []
    for idx, marker in enumerate(strong):
        selected.append(marker)
        next_marker = strong[idx + 1] if idx + 1 < len(strong) else None
        upper_pos = next_marker["start"] if next_marker else len(text)
        upper_number = next_marker["number"] if next_marker else max(valid_numbers) + 1
        expected = marker["number"] + 1
        search_pos = marker["content_start"]
        while expected < upper_number and expected in valid_numbers:
            inline = find_inline_marker(text, expected, search_pos, upper_pos)
            if not inline:
                break
            selected.append(inline)
            search_pos = inline["content_start"]
            expected += 1
    unique = {(item["number"], item["start"]): item for item in selected}
    return sorted(unique.values(), key=lambda item: item["start"])


def split_solution_blocks(
    text: str,
    valid_numbers: set[int],
    question_types: dict[int, str] | None = None,
) -> dict[int, str]:
    markers = solution_markers(text, valid_numbers, question_types)
    blocks: dict[int, str] = {}
    for idx, marker in enumerate(markers):
        end = markers[idx + 1]["start"] if idx + 1 < len(markers) else len(text)
        blocks[marker["number"]] = text[marker["start"] : end].strip()
    return blocks


def answer_from_solution_block(block: str, question_type: str) -> str:
    if not block:
        return ""
    text = re.sub(r"^\s*(?:#{1,6}\s*)?[1-9]\d?[．.]\s*", "", block).strip()
    detail = DETAIL_MARKER_RE.search(text)
    prefix = compact(text[: detail.start()] if detail else "")
    if question_type in {"single_choice", "multiple_choice"}:
        match = re.match(r"^([A-D]+)(?:\b|\s|$)", prefix)
        return match.group(1) if match else ""
    if prefix and len(prefix) <= 500:
        return prefix
    if question_type == "solution" and block:
        return "见解析"
    return ""


def markdown_to_html(value: str) -> str:
    value = value or ""
    fragments: list[str] = []

    def stash(match: re.Match[str]) -> str:
        token = f"EXAMOCRMATHPLACEHOLDER{len(fragments):05d}TOKEN"
        fragments.append(match.group(0))
        return token

    protected = MATH_FRAGMENT_RE.sub(stash, value)
    rendered = markdown_lib.markdown(protected, extensions=["tables", "fenced_code", "sane_lists"])
    for idx, fragment in enumerate(fragments):
        token = f"EXAMOCRMATHPLACEHOLDER{idx:05d}TOKEN"
        rendered = rendered.replace(token, html.escape(fragment, quote=False))
    return rendered


def markdown_math_is_preserved(value: str, rendered: str) -> bool:
    return all(
        html.escape(match.group(0), quote=False) in rendered
        for match in MATH_FRAGMENT_RE.finditer(value or "")
    )


def protect_answer_math_for_display(value: str) -> str:
    """Balance one missing inline-math delimiter for display without changing stored OCR text."""
    value = value or ""
    dollar_count = len(re.findall(r"(?<!\\)\$", value))
    if dollar_count % 2 == 0:
        return value
    leading = len(value) - len(value.lstrip())
    trailing = len(value) - len(value.rstrip())
    core_end = len(value) - trailing if trailing else len(value)
    core = value[leading:core_end]
    if core.endswith("$") and not core.startswith("$"):
        return value[:leading] + "$" + value[leading:]
    if core.startswith("$") and not core.endswith("$"):
        return value[:core_end] + "$" + value[core_end:]
    return value


def display_question_id(exam_code: str, question_number: int) -> str:
    short_exam = re.sub(r"[^A-Za-z0-9]+", "-", exam_code).strip("-").upper() or "EXAM"
    return f"{short_exam}-Q{question_number:03d}"


def type_label(value: str) -> str:
    return TYPE_LABELS.get(value, value or "未识别")


def flag_label(value: str) -> str:
    return FLAG_LABELS.get(value, value)


def flag_level(value: str) -> str:
    if value in BENIGN_FLAGS:
        return "info"
    if value in {"missing_image_asset"}:
        return "danger"
    return "warning"


def repair_label(value: str) -> str:
    embedded = re.fullmatch(r"split_embedded_question_(\d+)", value)
    if embedded:
        return f"拆分行内第 {embedded.group(1)} 题"
    return REPAIR_LABELS.get(value, value)


def repair_description(value: str) -> str:
    embedded = re.fullmatch(r"split_embedded_question_(\d+)", value)
    if embedded:
        return f"已从章节标题同行内容中恢复第 {embedded.group(1)} 题的独立题号。"
    return REPAIR_DESCRIPTIONS.get(value, "已执行结构清理。")


def render_badges(values: list[str], *, kind: str = "flag") -> str:
    if not values:
        return "<span class='badge success'>无</span>"
    if kind == "repair":
        return "".join(
            f"<span class='badge info' title='{html.escape(repair_description(value), quote=True)}'>"
            f"{html.escape(repair_label(value))}</span>"
            for value in values
        )
    return "".join(
        f"<span class='badge {flag_level(value)}'>{html.escape(flag_label(value))}</span>"
        for value in values
    )


def format_number_ranges(values: list[int]) -> str:
    if not values:
        return "无"
    numbers = sorted(set(values))
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}–{previous}")
    return "、".join(ranges)


def materialize_images(
    text: str,
    source_path: Path,
    question_dir: Path,
    assets: list[dict[str, Any]],
) -> tuple[str, bool]:
    assets_dir = question_dir / "assets"
    missing = False
    replacements: dict[str, str] = {}
    for match in IMAGE_RE.finditer(text or ""):
        raw_ref = match.group("src").strip().strip("<>")
        if raw_ref in replacements:
            continue
        candidate = Path(raw_ref)
        if not candidate.is_absolute():
            candidate = (source_path.parent / candidate).resolve()
        record: dict[str, Any] = {"original_ref": raw_ref, "alt": match.group("alt"), "saved": False}
        if not candidate.exists() or not candidate.is_file():
            record["error"] = "source_image_not_found"
            missing = True
            assets.append(record)
            continue
        assets_dir.mkdir(parents=True, exist_ok=True)
        file_name = candidate.name
        target = assets_dir / file_name
        if not target.exists():
            shutil.copyfile(candidate, target)
        record.update(
            {
                "saved": True,
                "file_name": file_name,
                "relative_path": f"assets/{file_name}",
                "sha256": sha256_file(target),
            }
        )
        assets.append(record)
        replacements[raw_ref] = f"assets/{file_name}"
    for old, new in replacements.items():
        text = text.replace(f"]({old})", f"]({new})")
    return text, missing


def render_preview(question: dict[str, Any]) -> str:
    flags = render_badges(question["quality_flags"])
    answer_html = markdown_to_html(protect_answer_math_for_display(str(question.get("answer") or "")))
    answer_body = answer_html or '<p class="answer-empty">暂无答案。</p>'
    solution_html = question.get("solution_html") or "<p>暂无解析。</p>"
    score = f" · {question['score']} 分" if question.get("score") is not None else ""
    display_id = question.get("display_id") or question["question_id"]
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{MATHJAX_HEAD}{STYLE}<title>{html.escape(display_id)}</title></head>
<body><main class="page-shell narrow">
<header class="hero"><a class="back-link" href="../../index.html">← 返回题库总览</a><div class="preview-id" title="内部 ID：{html.escape(question['question_id'], quote=True)}">{html.escape(display_id)}</div><h1>第 {question['question_number']} 题 · {html.escape(type_label(question['question_type']))}</h1><p>{html.escape(question['source_exam'])}{score}</p></header>
<div class="content-grid">
<section class="panel wide"><div class="section-kicker">Question</div><h2>题干</h2><div class="question-content">{question['stem_html']}</div></section>
<section class="panel"><div class="section-kicker">Answer</div><h2>答案</h2><div class="answer-content">{answer_body}</div></section>
<section class="panel"><div class="section-kicker">Quality</div><h2>质量标记</h2><div class="flag-list">{flags}</div></section>
<section class="panel wide"><div class="section-kicker">Solution</div><h2>解析</h2><div class="solution-content">{solution_html}</div></section>
</div></main></body></html>"""


def build_case(case: dict[str, Any], output_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    question_path: Path = case["question_path"]
    solution_path: Path | None = case["solution_path"]
    raw_question_text = question_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    _, structure_before = split_question_blocks(raw_question_text)
    question_text, repairs = normalize_exam_markdown(raw_question_text)
    solution_text = normalize_solution_markdown(solution_path.read_text(encoding="utf-8-sig")) if solution_path else ""
    blocks, structure = split_question_blocks(question_text)
    valid_numbers = {block["number"] for block in blocks}
    type_numbers: dict[str, list[int]] = {}
    for block in blocks:
        type_numbers.setdefault(block["question_type"], []).append(block["number"])

    answers = extract_table_answers(solution_text)
    for number, answer in extract_summary_answers(solution_text, type_numbers).items():
        answers.setdefault(number, answer)
    question_types = {block["number"]: block["question_type"] for block in blocks}
    solution_blocks = split_solution_blocks(solution_text, valid_numbers, question_types)

    questions = []
    for block in blocks:
        number = block["number"]
        qid = f"{case['exam_code']}_q{number:03d}"
        display_id = display_question_id(case["exam_code"], number)
        question_dir = output_dir / "questions" / qid
        question_dir.mkdir(parents=True, exist_ok=True)
        assets: list[dict[str, Any]] = []
        stem_markdown, stem_image_missing = materialize_images(block["body"], question_path, question_dir, assets)
        solution_markdown = solution_blocks.get(number, "")
        solution_markdown, solution_image_missing = materialize_images(
            solution_markdown,
            solution_path or question_path,
            question_dir,
            assets,
        )
        answer = compact(answers.get(number, "")) or answer_from_solution_block(solution_markdown, block["question_type"])
        answer = truncate_answer_contamination(answer)
        options = extract_options(stem_markdown)
        stem_html = markdown_to_html(stem_markdown)
        solution_html = markdown_to_html(solution_markdown)
        flags = []
        if assets:
            flags.append("contains_images")
        if stem_image_missing or solution_image_missing:
            flags.append("missing_image_asset")
        if not answer:
            flags.append("missing_answer")
        if not solution_markdown:
            flags.append("missing_solution")
        if block["question_type"] in {"single_choice", "multiple_choice"} and set(options) != {"A", "B", "C", "D"}:
            flags.append("option_count_mismatch")
        if block["question_type"] == "unknown":
            flags.append("unknown_question_type")
        if re.search(r"(?:^|\n)\s*(?:#+\s*)?补充题[：:]", stem_markdown):
            flags.append("embedded_supplement_question")
        if ANSWER_CONTAMINATION_RE.search(answer):
            flags.append("answer_contamination")
        if protect_answer_math_for_display(answer) != answer:
            flags.append("answer_math_display_repaired")
        if not markdown_math_is_preserved(stem_markdown, stem_html) or not markdown_math_is_preserved(
            solution_markdown, solution_html
        ):
            flags.append("markdown_math_loss")

        question = {
            "schema_version": SCHEMA_VERSION,
            "question_id": qid,
            "display_id": display_id,
            "source_exam": case["source_exam"],
            "source_file": str(question_path.resolve()),
            "solution_file": str(solution_path.resolve()) if solution_path else "",
            "question_number": number,
            "question_type": block["question_type"],
            "stem_markdown": stem_markdown,
            "stem_html": stem_html,
            "options": options,
            "answer": answer,
            "solution_markdown": solution_markdown,
            "solution_html": solution_html,
            "score": block["score"],
            "assets": assets,
            "quality_flags": sorted(set(flags)),
            "provenance": {
                "structured_by": "exam-ocr/scripts/structure_outputs.py",
                "structured_at": now_iso(),
                "question_source_sha256": sha256_file(question_path),
                "solution_source_sha256": sha256_file(solution_path) if solution_path else "",
            },
        }
        write_json(question_dir / "question.json", question)
        (question_dir / "preview.html").write_text(render_preview(question), encoding="utf-8")
        questions.append(question)

    resolved_missing = sorted(set(structure_before["missing"]) - set(structure["missing"]))
    resolved_duplicates = sorted(set(structure_before["duplicates"]) - set(structure["duplicates"]))
    return questions, {
        "exam_code": case["exam_code"],
        "source_exam": case["source_exam"],
        "question_file": str(question_path.resolve()),
        "solution_file": str(solution_path.resolve()) if solution_path else "",
        "question_count": len(questions),
        "seen": structure["seen"],
        "missing": structure["missing"],
        "duplicates": structure["duplicates"],
        "repairs": repairs,
        "repair_details": [
            {
                "code": repair,
                "label": repair_label(repair),
                "description": repair_description(repair),
            }
            for repair in repairs
        ],
        "structure_before_repair": structure_before,
        "structure_after_repair": structure,
        "resolved_missing": resolved_missing,
        "resolved_duplicates": resolved_duplicates,
    }


def render_index(questions: list[dict[str, Any]]) -> str:
    rows = []
    for question in questions:
        flags = question["quality_flags"]
        display_id = question.get("display_id") or question["question_id"]
        answer = str(question.get("answer") or "").strip()
        answer_html = markdown_to_html(protect_answer_math_for_display(answer))
        if not answer:
            answer_body = '<span class="answer-empty">暂无答案</span>'
        elif len(compact(answer)) > 180 or answer.count("$") >= 8:
            answer_body = (
                '<details class="answer-details"><summary>展开完整答案</summary>'
                f'<div class="answer-content">{answer_html}</div></details>'
            )
        else:
            answer_body = f'<div class="answer-content">{answer_html}</div>'
        search_text = " ".join(
            [
                question["question_id"],
                display_id,
                question["source_exam"],
                str(question["question_number"]),
                type_label(question["question_type"]),
                compact(answer),
                " ".join(flag_label(flag) for flag in flags),
            ]
        ).lower()
        review_required = any(flag not in BENIGN_FLAGS for flag in flags)
        rows.append(
            f"<tr data-search='{html.escape(search_text, quote=True)}' "
            f"data-exam='{html.escape(question['source_exam'], quote=True)}' "
            f"data-type='{html.escape(question['question_type'], quote=True)}' "
            f"data-quality='{'review' if review_required else 'ok'}'>"
            f"<td><a class='id-link' title='内部 ID：{html.escape(question['question_id'], quote=True)}' href='questions/{question['question_id']}/preview.html'>{html.escape(display_id)}</a></td>"
            f"<td class='source-cell'>{html.escape(question['source_exam'])}</td><td class='number-cell'>{question['question_number']}</td>"
            f"<td><span class='badge muted'>{html.escape(type_label(question['question_type']))}</span></td>"
            f"<td class='answer-cell'>{answer_body}</td><td><div class='flag-list'>{render_badges(flags)}</div></td></tr>"
        )
    exams = sorted({question["source_exam"] for question in questions})
    exam_options = "".join(
        f"<option value='{html.escape(exam, quote=True)}'>{html.escape(exam)}</option>" for exam in exams
    )
    type_options = "".join(
        f"<option value='{value}'>{html.escape(label)}</option>"
        for value, label in TYPE_LABELS.items()
        if any(question["question_type"] == value for question in questions)
    )
    review_count = sum(
        1 for question in questions if any(flag not in BENIGN_FLAGS for flag in question["quality_flags"])
    )
    formula_answer_count = sum(1 for question in questions if "$" in str(question.get("answer") or ""))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{MATHJAX_HEAD}{STYLE}<title>结构化题库</title></head><body>
<main class="page-shell"><header class="hero"><h1>结构化题库</h1><p>按试卷、题型与质量状态浏览；点击题目 ID 可查看完整题干和解析。</p></header>
<section class="summary-grid"><div class="summary-card"><div class="label">题目总数</div><div class="value">{len(questions)}</div><div class="hint">已结构化题目</div></div><div class="summary-card"><div class="label">试卷</div><div class="value">{len(exams)}</div><div class="hint">独立来源</div></div><div class="summary-card"><div class="label">待复核</div><div class="value">{review_count}</div><div class="hint">不含“含图片”提示</div></div><div class="summary-card"><div class="label">公式答案</div><div class="value">{formula_answer_count}</div><div class="hint">已启用 MathJax 渲染</div></div></section>
<section class="toolbar" aria-label="题库筛选"><input id="search" type="search" placeholder="搜索 ID、试卷、题号或答案…" aria-label="搜索"><select id="exam-filter" aria-label="按试卷筛选"><option value="">全部试卷</option>{exam_options}</select><select id="type-filter" aria-label="按题型筛选"><option value="">全部题型</option>{type_options}</select><select id="quality-filter" aria-label="按质量筛选"><option value="">全部质量状态</option><option value="ok">结构正常</option><option value="review">需要复核</option></select><div id="toolbar-status" class="toolbar-status">显示 {len(questions)} / {len(questions)} 题</div></section>
<section class="table-wrap"><div class="table-caption">显示 ID 采用“学校年份-年级-试卷-题号”格式；长答案默认折叠，展开后公式仍会自动排版。</div><table><thead><tr><th>题目 ID</th><th>来源</th><th>题号</th><th>题型</th><th>答案</th><th>质量标记</th></tr></thead><tbody id="question-rows">{''.join(rows)}</tbody></table><div id="empty-state" class="empty-state" hidden>没有符合当前筛选条件的题目。</div></section></main>
<script>
(() => {{
  const search = document.getElementById('search');
  const exam = document.getElementById('exam-filter');
  const type = document.getElementById('type-filter');
  const quality = document.getElementById('quality-filter');
  const rows = [...document.querySelectorAll('#question-rows tr')];
  const status = document.getElementById('toolbar-status');
  const empty = document.getElementById('empty-state');
  const apply = () => {{
    const needle = search.value.trim().toLowerCase();
    let shown = 0;
    rows.forEach(row => {{
      const visible = (!needle || row.dataset.search.includes(needle)) && (!exam.value || row.dataset.exam === exam.value) && (!type.value || row.dataset.type === type.value) && (!quality.value || row.dataset.quality === quality.value);
      row.hidden = !visible;
      if (visible) shown += 1;
    }});
    status.textContent = `显示 ${{shown}} / ${{rows.length}} 题`;
    empty.hidden = shown !== 0;
  }};
  [search, exam, type, quality].forEach(control => control.addEventListener(control === search ? 'input' : 'change', apply));
  document.querySelectorAll('details.answer-details').forEach(details => details.addEventListener('toggle', () => {{
    if (details.open && window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([details]);
  }}));
}})();
</script></body></html>"""


def build_structure_report(
    questions: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    flag_counts = Counter(flag for question in questions for flag in question["quality_flags"])
    failures = []
    for case in cases:
        if case["duplicates"]:
            failures.append(f"{case['source_exam']}：存在重复题号 {format_number_ranges(case['duplicates'])}")
        if case["missing"]:
            failures.append(f"{case['source_exam']}：缺失题号 {format_number_ranges(case['missing'])}")
    if flag_counts.get("missing_image_asset"):
        failures.append(f"缺失图片资源：{flag_counts['missing_image_asset']} 题")
    if flag_counts.get("answer_contamination"):
        failures.append(f"答案疑似串题：{flag_counts['answer_contamination']} 题")
    if flag_counts.get("markdown_math_loss"):
        failures.append(f"公式在 Markdown→HTML 转换中受损：{flag_counts['markdown_math_loss']} 题")
    ids = [question["question_id"] for question in questions]
    duplicate_ids = sorted(value for value, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        failures.append(f"存在重复题目 ID：{'、'.join(duplicate_ids)}")
    display_ids = [question.get("display_id") or question["question_id"] for question in questions]
    duplicate_display_ids = sorted(value for value, count in Counter(display_ids).items() if count > 1)
    if duplicate_display_ids:
        failures.append(f"存在重复显示 ID：{'、'.join(duplicate_display_ids)}")
    if not questions:
        failures.append("题库为空")
    warning_flags = {
        key: value
        for key, value in flag_counts.items()
        if key not in BENIGN_FLAGS | {"missing_image_asset", "answer_contamination", "markdown_math_loss"} and value
    }
    repair_counts = Counter(repair for case in cases for repair in case.get("repairs", []))
    status = "fail" if failures else ("warn" if warning_flags else "pass")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "status": status,
        "output_dir": str(output_dir.resolve()),
        "question_count": len(questions),
        "case_count": len(cases),
        "cases": cases,
        "quality_flag_counts": dict(sorted(flag_counts.items())),
        "warning_flag_counts": dict(sorted(warning_flags.items())),
        "repair_counts": dict(sorted(repair_counts.items())),
        "unresolved_missing_count": sum(len(case["missing"]) for case in cases),
        "unresolved_duplicate_count": sum(len(case["duplicates"]) for case in cases),
        "resolved_missing_count": sum(len(case.get("resolved_missing", [])) for case in cases),
        "resolved_duplicate_count": sum(len(case.get("resolved_duplicates", [])) for case in cases),
        "display_id_count": len(set(display_ids)),
        "duplicate_display_ids": duplicate_display_ids,
        "failures": failures,
    }


def render_structure_report(report: dict[str, Any]) -> str:
    status_label = {"pass": "通过", "warn": "需复核", "fail": "未通过"}.get(report["status"], report["status"])
    status_level = {"pass": "success", "warn": "warning", "fail": "danger"}.get(report["status"], "warning")

    def diagnostic(case: dict[str, Any], key: str) -> str:
        unresolved = case.get(key, [])
        resolved_key = "resolved_missing" if key == "missing" else "resolved_duplicates"
        resolved = case.get(resolved_key, [])
        pieces = []
        if unresolved:
            pieces.append(
                f"<span class='badge danger'>未解决：{html.escape(format_number_ranges(unresolved))}</span>"
            )
        if resolved:
            pieces.append(
                f"<span class='badge success'>已修复：{html.escape(format_number_ranges(resolved))}</span>"
            )
        return "".join(pieces) or "<span class='badge success'>无</span>"

    case_rows = []
    for case in report["cases"]:
        repairs = render_badges(case.get("repairs", []), kind="repair")
        repair_note = ""
        if case.get("repair_details"):
            repair_note = "<span class='repair-note'>" + " ".join(
                html.escape(detail["description"]) for detail in case["repair_details"]
            ) + "</span>"
        seen_summary = format_number_ranges(case["seen"])
        declared = case.get("structure_after_repair", {}).get("declared_total")
        declared_note = f"；卷面声明 {declared} 题" if declared else ""
        case_rows.append(
            f"<tr><td class='source-cell'>{html.escape(case['source_exam'])}</td><td class='number-cell'>{case['question_count']}</td>"
            f"<td><span class='nowrap'>{html.escape(seen_summary)}</span><span class='repair-note'>识别 {len(case['seen'])} 个唯一题号{declared_note}</span></td>"
            f"<td class='diagnostic'>{diagnostic(case, 'missing')}</td><td class='diagnostic'>{diagnostic(case, 'duplicates')}</td>"
            f"<td><div class='repair-list'>{repairs}</div>{repair_note}</td></tr>"
        )
    flag_items = "".join(
        f"<div class='summary-card'><div class='label'>{html.escape(flag_label(name))}</div><div class='value'>{count}</div><div class='hint'>{html.escape(name)}</div></div>"
        for name, count in report["quality_flag_counts"].items()
    ) or "<div class='empty-state'>无质量标记</div>"
    failures = "".join(f"<li>{html.escape(item)}</li>" for item in report["failures"])
    failure_body = f"<ul>{failures}</ul>" if failures else "<div class='status-banner success'>无阻断问题。</div>"
    repair_total = sum(report.get("repair_counts", {}).values())
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{STYLE}<title>结构质量报告</title></head><body>
<main class="page-shell"><header class="hero"><a class="back-link" href="index.html">← 返回题库总览</a><h1>结构质量报告</h1><p>展示切题修复前后的诊断结果；最终验收以修复后仍未解决的问题为准。</p></header>
<div class="status-banner {status_level}"><span class="status-dot"></span>总体状态：<strong>{html.escape(status_label)}</strong></div>
<section class="summary-grid"><div class="summary-card"><div class="label">试卷</div><div class="value">{report['case_count']}</div><div class="hint">已配对来源</div></div><div class="summary-card"><div class="label">题目</div><div class="value">{report['question_count']}</div><div class="hint">结构化记录</div></div><div class="summary-card"><div class="label">未解决结构问题</div><div class="value">{report.get('unresolved_missing_count', 0) + report.get('unresolved_duplicate_count', 0)}</div><div class="hint">缺失 + 重复题号</div></div><div class="summary-card"><div class="label">自动修复</div><div class="value">{repair_total}</div><div class="hint">已执行的清理动作</div></div></section>
<section class="panel"><h2>逐卷结构诊断</h2><p class="report-note">“缺失”和“重复”同时显示已修复与未解决项；空结果统一显示“无”，不再暴露内部数组格式。</p><div class="table-wrap"><table><thead><tr><th>来源</th><th>题数</th><th>识别题号</th><th>缺失</th><th>重复</th><th>自动修复</th></tr></thead><tbody>{''.join(case_rows)}</tbody></table></div></section>
<section class="panel"><h2>题目质量标记</h2><div class="summary-grid">{flag_items}</div><p class="report-note">“含图片”是信息提示，不会单独把总体状态降为“需复核”。</p></section>
<section class="panel"><h2>阻断问题</h2>{failure_body}</section></main></body></html>"""


def parse_args() -> argparse.Namespace:
    default_tagger = Path(__file__).resolve().parents[2] / "smart-question-tagger" / "scripts"
    parser = argparse.ArgumentParser(description="Build a fresh structured bank from polished exam OCR Markdown.")
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory containing top-level polished exam/answer Markdown and images/",
    )
    parser.add_argument("--structured-output", type=Path, required=True, help="New structured-bank directory")
    parser.add_argument("--tag", action="store_true", help="Run the smart-question-tagger bridge after structure validation")
    parser.add_argument("--tagger-dir", type=Path, default=default_tagger, help="Sibling smart-question-tagger scripts directory")
    parser.add_argument("--tag-threshold", type=float, default=0.8, help="Teacher-review confidence threshold")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    ocr_dir = args.output_dir.resolve()
    structured_dir = args.structured_output.resolve()
    if not ocr_dir.is_dir():
        raise SystemExit(f"OCR directory not found: {ocr_dir}")
    if structured_dir.exists() and any(structured_dir.iterdir()):
        raise SystemExit(f"Structured output must be new or empty: {structured_dir}")
    structured_dir.mkdir(parents=True, exist_ok=True)

    cases = discover_cases(ocr_dir)
    if not cases:
        raise SystemExit(f"No top-level exam Markdown files found in {ocr_dir}")
    all_questions: list[dict[str, Any]] = []
    case_reports = []
    for case in cases:
        questions, report = build_case(case, structured_dir)
        all_questions.extend(questions)
        case_reports.append(report)

    write_jsonl(structured_dir / "questions.jsonl", all_questions)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "source_ocr_dir": str(ocr_dir),
        "questions": len(all_questions),
        "cases": case_reports,
    }
    write_json(structured_dir / "manifest.json", manifest)
    (structured_dir / "index.html").write_text(render_index(all_questions), encoding="utf-8")
    report = build_structure_report(all_questions, case_reports, structured_dir)
    write_json(structured_dir / "structure_report.json", report)
    (structured_dir / "structure_report.html").write_text(render_structure_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "questions": len(all_questions),
                "cases": len(cases),
                "output": str(structured_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["status"] == "fail":
        return 1
    if args.tag:
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("tag_structured_outputs.py")),
                str(structured_dir),
                "--tagger-dir",
                str(args.tagger_dir.resolve()),
                "--threshold",
                str(args.tag_threshold),
            ],
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
