#!/usr/bin/env python3
"""Build a structured question bank from converted Markdown/HTML files."""

import argparse
import base64
import hashlib
import html
import json
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse


QUESTION_START_RE = re.compile(
    r"(?m)^(?P<prefix>(?:#{1,6}\s*)?(?:<img\b[^>]*>\s*)?)(?P<number>\d{1,2})(?:[．.。:：、]|\uff0e|��)\s*"
)
IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
HTML_TABLE_START_RE = re.compile(r"<table\b", re.IGNORECASE)
HTML_TABLE_END_RE = re.compile(r"</table>", re.IGNORECASE)
MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
IMG_SRC_RE = re.compile(r"""\bsrc\s*=\s*["'](?P<src>[^"']+)["']""", re.IGNORECASE)
IMG_ALT_RE = re.compile(r"""\balt\s*=\s*["'](?P<alt>[^"']*)["']""", re.IGNORECASE)
DATA_URI_RE = re.compile(r"^data:(?P<mime>image/[A-Za-z0-9.+-]+);base64,(?P<data>.+)$", re.DOTALL)
MATHJAX_HEAD = """<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
    processEscapes: true
  },
  svg: { fontCache: 'global' }
};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>"""
STYLE_HEAD = """<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; margin: 24px; }
.question-table { border-collapse: collapse; margin: 12px 0; }
.question-table td, .question-table th { border: 1px solid #555; padding: 4px 10px; text-align: center; min-width: 44px; }
</style>"""


def main():
    parser = argparse.ArgumentParser(description="Build a structured question bank from html-homework-database.")
    parser.add_argument(
        "--input-root",
        required=True,
        help="Root containing converted exam folders.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory for structured artifacts.",
    )
    parser.add_argument("--scope", choices=["pilot", "all"], default="all")
    parser.add_argument(
        "--pilot-case",
        action="append",
        default=[],
        help="Folder name, exam code, or exam title to include in pilot mode; repeat as needed.",
    )
    parser.add_argument("--pilot-limit", type=int, default=2, help="Pilot case count when --pilot-case is omitted.")
    args = parser.parse_args()

    input_root = Path(args.input_root).resolve()
    output_dir = Path(args.output).resolve()
    if not input_root.is_dir():
        raise SystemExit(f"Input root not found or not a directory: {input_root}")
    cases = discover_cases(input_root)
    if args.scope == "pilot":
        requested = {value.strip() for value in args.pilot_case if value.strip()}
        if requested:
            cases = [
                case
                for case in cases
                if requested.intersection({case["folder"], case["exam_code"], case["source_exam"]})
            ]
        else:
            cases = cases[: max(1, args.pilot_limit)]
    if not cases:
        raise SystemExit("No usable exam Markdown folders were discovered for the selected scope.")

    all_questions = []
    report_rows = []
    for case in cases:
        question_path = input_root / case["folder"] / case["question_md"]
        solution_path = input_root / case["folder"] / case["solution_md"] if case.get("solution_md") else None
        questions, report = build_case(case, question_path, solution_path)
        all_questions.extend(questions)
        report_rows.append(report)

    write_outputs(output_dir, all_questions, report_rows)
    print(json.dumps({"questions": len(all_questions), "output": str(output_dir)}, ensure_ascii=False, indent=2))


def discover_cases(input_root):
    cases = []
    for folder in sorted(path for path in input_root.iterdir() if path.is_dir() and not path.name.startswith("_")):
        markdown_files = sorted(folder.glob("*.md"))
        if not markdown_files:
            continue
        question_path = choose_question_markdown(markdown_files)
        if not question_path:
            continue
        solution_path = choose_solution_markdown(markdown_files, question_path)
        source_exam = question_path.stem
        cases.append(
            {
                "exam_code": exam_code_from_folder(folder.name),
                "source_exam": source_exam,
                "folder": folder.name,
                "question_md": question_path.name,
                "solution_md": solution_path.name if solution_path else "",
                "expected": [],
            }
        )
    return cases


def choose_question_markdown(paths):
    question_words = ("试卷", "试题", "周测", "作业")
    solution_words = ("答案", "解析", "参考答案")
    candidates = [path for path in paths if not any(word in path.stem for word in solution_words)]
    preferred = [path for path in candidates if any(word in path.stem for word in question_words)]
    if preferred:
        candidates = preferred
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (0 if any(word in path.stem for word in ("试卷", "试题")) else 1, len(path.name)))[0]


def choose_solution_markdown(paths, question_path):
    solution_words = ("参考答案", "答案", "解析")
    candidates = [path for path in paths if path != question_path and any(word in path.stem for word in solution_words)]
    return sorted(candidates, key=lambda path: (0 if "参考答案" in path.stem else 1, 0 if "答案" in path.stem else 1, len(path.name)))[0] if candidates else None


def exam_code_from_folder(folder_name):
    normalized = unicodedata.normalize("NFKC", str(folder_name or "")).strip() or "untitled-exam"
    return "exam_" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def build_case(case, question_path, solution_path):
    question_text = strip_answer_card_sections(question_path.read_text(encoding="utf-8-sig"))
    solution_text = strip_answer_card_sections(solution_path.read_text(encoding="utf-8-sig")) if solution_path and solution_path.exists() else ""
    question_blocks = split_numbered_blocks(question_text)
    solution_blocks = block_map(split_numbered_blocks(solution_text))
    answer_map = extract_answers(solution_text)
    inline_answers = extract_inline_answers(solution_text)

    questions = []
    seen_numbers = []
    for block in question_blocks:
        number = block["number"]
        seen_numbers.append(number)
        qid = f"{case['exam_code']}_q{number:03d}"
        raw_markdown = strip_trailing_section_heading(block["body"].strip())
        stem_markdown, assets = sanitize_images(raw_markdown, qid, question_path)
        solution_markdown = solution_blocks.get(number, "").strip()
        solution_markdown, assets = sanitize_images(solution_markdown, qid, solution_path or question_path, assets)
        answer = answer_map.get(number) or inline_answers.get(number) or infer_answer_from_solution(solution_markdown)
        question_type, score = infer_type_and_score(stem_markdown, answer)
        quality_flags = []
        if assets:
            quality_flags.append("contains_images")
        if not answer:
            quality_flags.append("missing_answer")
        if not solution_markdown:
            quality_flags.append("missing_solution")
        if any(asset.get("data_uri_present") for asset in assets):
            quality_flags.append("contains_base64_image")

        question = {
            "question_id": qid,
            "source_exam": case["source_exam"],
            "source_file": str(question_path),
            "solution_file": str(solution_path) if solution_path else "",
            "question_number": number,
            "question_type": question_type,
            "stem_markdown": stem_markdown,
            "stem_html": markdown_to_basic_html(stem_markdown),
            "options": extract_options(stem_markdown),
            "answer": answer,
            "solution_markdown": solution_markdown,
            "solution_html": markdown_to_basic_html(solution_markdown),
            "score": score,
            "assets": assets,
            "quality_flags": sorted(set(quality_flags)),
        }
        apply_generic_question_repairs(question)
        questions.append(question)

    expected = set(case["expected"] or range(1, max(seen_numbers) + 1) if seen_numbers else [])
    seen = set(seen_numbers)
    duplicates = sorted(number for number in seen if seen_numbers.count(number) > 1)
    return questions, {
        "source_exam": case["source_exam"],
        "expected": case["expected"],
        "seen": sorted(seen),
        "missing": sorted(expected - seen),
        "extra": sorted(seen - expected),
        "duplicates": duplicates,
    }


def split_numbered_blocks(text):
    matches = list(QUESTION_START_RE.finditer(text))
    blocks = []
    for idx, match in enumerate(matches):
        number = int(match.group("number"))
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if is_non_question_tail(body):
            continue
        blocks.append({"number": number, "body": body})
    return blocks


def strip_answer_card_sections(text):
    text = re.sub(
        r"(?ms)^#{1,6}\s*[^\n]*答题卡[^\n]*\n.*?(?=^#{1,6}\s*[^\n]*解答题|^\s*15[．.]|$)",
        "",
        text,
    )
    # Some OCR/encoding paths lose the literal "答题卡" heading, but still keep
    # the answer-card structure: a heading, a class/name/score underline, a
    # choice table, and a fill-in answer line before the solution section.
    text = re.sub(
        r"(?ms)^#{1,6}\s*[^\n]*_________________[^\n]*\n"
        r"(?:\s*\|.*\|\s*\n){2,}"
        r"\s*^#{1,6}\s*12[．.][^\n]*14[．.][^\n]*(?=^#{1,6}\s*[^\n]*|^\s*15[．.]|$)",
        "",
        text,
    )
    return text


SECTION_INSTRUCTION_RE = re.compile(
    r"^\s*#{0,6}\s*[一二三四五六七八九十]+[、，,.．]\s*"
    r"(?:单选题|选择题|多选题|填空题|解答题)[：:].*$"
)


def strip_trailing_section_heading(text):
    lines = text.strip().splitlines()
    while lines:
        last_idx = next((idx for idx in range(len(lines) - 1, -1, -1) if lines[idx].strip()), None)
        if last_idx is None:
            return ""
        line = lines[last_idx].strip()
        nonempty_before = any(item.strip() for item in lines[:last_idx])
        is_markdown_section_heading = line.startswith("#") and not re.match(r"^#{1,6}\s*\d{1,2}\D", line)
        is_plain_section_instruction = bool(SECTION_INSTRUCTION_RE.match(line))
        if not nonempty_before or not (is_markdown_section_heading or is_plain_section_instruction):
            break
        lines = lines[:last_idx]
    return "\n".join(lines).strip()


def block_map(blocks):
    mapped = {}
    for block in blocks:
        mapped.setdefault(block["number"], block["body"])
    return mapped


def is_non_question_tail(body):
    head = body[:120]
    return (
        "答题卡" in head
        or "注意事项" in head
        or ("________________" in head and "解答题" in head)
        or bool(re.fullmatch(r"(?s)#{0,6}\s*\d{1,2}[．.]\s*_+(?:\s+\d{1,2}[．.]\s*_+)*\s*", body))
    )


def extract_answers(solution_text):
    answers = {}
    for table in markdown_table_blocks(solution_text):
        rows = [split_table_row(line) for line in table]
        rows = [row for row in rows if row and not is_separator_row(row)]
        for idx, row in enumerate(rows):
            normalized = [clean_cell(cell) for cell in row]
            if any("题号" in cell for cell in normalized):
                answer_row = next((r for r in rows[idx + 1 :] if any("答案" in clean_cell(c) for c in r)), None)
                if answer_row:
                    map_answer_cells(normalized[1:], [clean_cell(c) for c in answer_row][1:], answers)
            elif all(is_int_cell(cell) or not cell for cell in normalized):
                if idx + 1 < len(rows):
                    map_answer_cells(normalized, [clean_cell(c) for c in rows[idx + 1]], answers)
    return answers


def extract_inline_answers(solution_text):
    answers = {}
    first_detail = re.search(r"(?m)^(?:<img\b[^>]*>\s*)?15[．.]", solution_text)
    header = solution_text[: first_detail.start()] if first_detail else solution_text[:2000]
    for match in re.finditer(r"(?m)(\d{1,2})[．.]\s*([^\n|]+?)(?=(?:\s+\d{1,2}[．.])|\n|$)", header):
        number = int(match.group(1))
        value = match.group(2).strip()
        value = re.split(r"【详解】|【解析】", value, maxsplit=1)[0].strip()
        if value and "参考答案" not in value:
            answers[number] = value
    return answers


def markdown_table_blocks(text):
    blocks = []
    current = []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            current.append(line.strip())
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    return blocks


def split_table_row(line):
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return cells


def is_separator_row(row):
    return all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in row if cell.strip())


def clean_cell(cell):
    cell = re.sub(r"#+", "", str(cell))
    return cell.strip()


def is_int_cell(cell):
    return bool(re.fullmatch(r"\d{1,2}", clean_cell(cell)))


def map_answer_cells(number_cells, answer_cells, answers):
    for num_cell, ans_cell in zip(number_cells, answer_cells):
        num_cell = clean_cell(num_cell)
        ans_cell = clean_cell(ans_cell)
        if num_cell.isdigit() and ans_cell:
            answers[int(num_cell)] = ans_cell


def infer_answer_from_solution(solution_markdown):
    if not solution_markdown:
        return ""
    text = solution_markdown.strip()
    text = re.sub(r"^\d{1,2}[．.]\s*", "", text)
    answer_match = re.search(r"【答案】\s*([\s\S]*?)(?=【详解】|\n\n|$)", text)
    if answer_match:
        return compact(answer_match.group(1))
    leading = re.match(r"([A-D]+)(?:【详解】|$)", text)
    if leading:
        return leading.group(1)
    detail_split = re.split(r"【详解】", text, maxsplit=1)
    if len(detail_split) > 1:
        prefix = compact(detail_split[0])
        if prefix:
            return prefix
    fallback = compact(text)
    if fallback and len(fallback) <= 500:
        return fallback
    if fallback:
        return "见解析"
    return ""


def infer_type_and_score(stem_markdown, answer):
    """Infer shape from the question itself; scoring remains teacher/workbook data."""
    options = extract_options(stem_markdown or "")
    if options:
        labels = re.findall(r"[A-D]", str(answer or "").upper())
        declared_multiple = bool(re.search(r"多选|不止一项|所有正确", str(stem_markdown or "")))
        return ("multiple_choice" if declared_multiple or len(set(labels)) >= 2 else "single_choice"), None
    if re.search(r"_{2,}|（\s*）|\(\s*\)|\\quad|填空", str(stem_markdown or "")):
        return "fill_blank", None
    return "solution", None


def sanitize_images(markdown_text, question_id, source_path, assets=None):
    assets = list(assets or [])

    def next_asset(src, alt, raw):
        idx = len(assets) + 1
        placeholder = f"[image:{question_id}_img{idx:02d}]"
        assets.append(
            {
                "asset_id": f"{question_id}_img{idx:02d}",
                "placeholder": placeholder,
                "kind": "image",
                "original_src": src,
                "alt": alt or "image",
                "source_file": str(source_path),
                "data_uri_present": src.startswith("data:image"),
                "raw_img_tag": raw[:500],
            }
        )
        return placeholder

    def replace_html_img(match):
        tag = match.group(0)
        src_match = IMG_SRC_RE.search(tag)
        alt_match = IMG_ALT_RE.search(tag)
        src = html.unescape(src_match.group("src")) if src_match else ""
        alt = html.unescape(alt_match.group("alt")) if alt_match else "image"
        return next_asset(src, alt, tag)

    def replace_markdown_img(match):
        src = html.unescape(match.group("src").strip())
        alt = html.unescape(match.group("alt").strip() or "image")
        return next_asset(src, alt, match.group(0))

    text = IMG_RE.sub(replace_html_img, markdown_text)
    text = MD_IMAGE_RE.sub(replace_markdown_img, text)
    return text, assets


def extract_options(stem_markdown):
    matches = list(re.finditer(r"(?<![A-Za-z])([A-D])[．.]\s*", stem_markdown))
    labels = [match.group(1) for match in matches]
    if len(matches) < 2 or labels != sorted(labels):
        return {}
    options = {}
    for idx, match in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(stem_markdown)
        value = compact(stem_markdown[match.end() : next_start])
        if value:
            options[match.group(1)] = value
    return options


def markdown_to_basic_html(markdown_text):
    lines = str(markdown_text).splitlines()
    html_blocks = []
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            idx += 1
            continue
        if HTML_TABLE_START_RE.search(line):
            table_lines = [line]
            idx += 1
            while idx < len(lines):
                table_lines.append(lines[idx].strip())
                if HTML_TABLE_END_RE.search(lines[idx]):
                    idx += 1
                    break
                idx += 1
            html_blocks.append(render_html_table("\n".join(table_lines)))
            continue
        if is_markdown_table_start(lines, idx):
            table_lines = []
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                table_lines.append(lines[idx].strip())
                idx += 1
            html_blocks.append(render_markdown_table(table_lines))
            continue
        html_blocks.append(f"<p>{markdown_line_to_html(line)}</p>")
        idx += 1
    return "\n".join(html_blocks)


def markdown_line_to_html(line):
    escaped = html.escape(line, quote=False)

    def replace_image(match):
        alt = html.escape(match.group("alt"), quote=True)
        src = html.escape(match.group("src"), quote=True)
        return f'<img src="{src}" alt="{alt}" style="max-width:100%; max-height:360px; width:auto; height:auto; vertical-align:middle;">'

    return MD_IMAGE_RE.sub(replace_image, escaped)


def render_html_table(table_html):
    table_html = re.sub(r"<table\b", '<table class="question-table"', table_html, count=1, flags=re.IGNORECASE)
    return table_html


def is_markdown_table_start(lines, idx):
    if idx + 1 >= len(lines):
        return False
    current = lines[idx].strip()
    next_line = lines[idx + 1].strip()
    return current.startswith("|") and next_line.startswith("|") and bool(re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?", next_line))


def render_markdown_table(table_lines):
    rows = []
    for line in table_lines:
        cells = split_table_row(line)
        if not cells or is_separator_row(cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    rendered_rows = []
    for row in rows:
        cells = "".join(f"<td>{markdown_line_to_html(cell)}</td>" for cell in row)
        rendered_rows.append(f"<tr>{cells}</tr>")
    return '<table class="question-table">\n' + "\n".join(rendered_rows) + "\n</table>"


def apply_generic_question_repairs(question):
    """Apply only content-independent normalisation; never patch by exam or question id."""
    replace_bad_e_half(question)
    normalize_subquestion_markers(question)
    question["stem_markdown"] = re.sub(
        r"^#{1,6}\s*", "", str(question.get("stem_markdown") or "")
    ).strip()
    question["stem_html"] = markdown_to_basic_html(question.get("stem_markdown") or "")
    question["solution_html"] = markdown_to_basic_html(question.get("solution_markdown") or "")
    question["options"] = extract_options(question.get("stem_markdown") or "")


def replace_bad_e_half(question):
    for field in ("stem_markdown", "solution_markdown", "answer"):
        value = question.get(field)
        if not isinstance(value, str):
            continue
        value = value.replace("$\\displaystyle \\frac{e2}{}$", "$\\displaystyle \\frac{e}{2}$")
        value = value.replace("\\frac{e2}{}", "\\frac{e}{2}")
        question[field] = value


def normalize_subquestion_markers(question):
    for field in ("stem_markdown", "solution_markdown"):
        value = question.get(field)
        if isinstance(value, str):
            question[field] = re.sub(r"(?m)^\((\d+)\)", r"（\1）", value)


def compact(text):
    return " ".join(str(text).split()).strip()


def write_outputs(output_dir, questions, report_rows):
    output_dir.mkdir(parents=True, exist_ok=True)
    questions_dir = output_dir / "questions"
    for question in questions:
        question_dir = questions_dir / question["question_id"]
        question_dir.mkdir(parents=True, exist_ok=True)
        materialize_question_assets(question, question_dir)
        write_json(question_dir / "question.json", question)
        (question_dir / "preview.html").write_text(render_question_preview(question), encoding="utf-8")

    write_jsonl(output_dir / "questions.jsonl", questions)
    write_json(output_dir / "manifest.json", {"questions": len(questions), "cases": report_rows})
    (output_dir / "index.html").write_text(render_index(questions), encoding="utf-8")


def materialize_question_assets(question, question_dir):
    if not question.get("assets"):
        return

    assets_dir = question_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    stem_markdown = question.get("stem_markdown", "")
    solution_markdown = question.get("solution_markdown", "")

    for asset in question["assets"]:
        file_name, status = save_image_asset(asset, assets_dir)
        if file_name:
            relative_path = f"assets/{file_name}"
            asset["file_name"] = file_name
            asset["relative_path"] = relative_path
            asset["saved"] = True
            if asset.get("data_uri_present"):
                asset.pop("original_src", None)
            replacement = f"![{asset.get('alt') or 'image'}]({relative_path})"
            stem_markdown = stem_markdown.replace(asset["placeholder"], replacement)
            solution_markdown = solution_markdown.replace(asset["placeholder"], replacement)
        else:
            asset["saved"] = False
            asset["save_error"] = status
            stem_markdown = stem_markdown.replace(asset["placeholder"], f"[image-missing:{asset['asset_id']}]")
            solution_markdown = solution_markdown.replace(asset["placeholder"], f"[image-missing:{asset['asset_id']}]")
        asset.pop("raw_img_tag", None)

    question["stem_markdown"] = stem_markdown
    question["stem_html"] = markdown_to_basic_html(stem_markdown)
    question["options"] = extract_options(stem_markdown)
    question["solution_markdown"] = solution_markdown
    question["solution_html"] = markdown_to_basic_html(solution_markdown)


def save_image_asset(asset, assets_dir):
    src = asset.get("original_src") or ""
    if not src:
        return "", "missing_src"

    data_match = DATA_URI_RE.match(src)
    if data_match:
        extension = image_extension(data_match.group("mime"))
        file_name = f"{asset['asset_id']}{extension}"
        try:
            data = base64.b64decode(data_match.group("data"), validate=True)
        except Exception as exc:
            return "", f"invalid_data_uri:{exc}"
        (assets_dir / file_name).write_bytes(data)
        return file_name, "saved"

    source_file = Path(asset.get("source_file", "")).resolve()
    source_dir = source_file.parent
    parsed = urlparse(src)
    if parsed.scheme and parsed.scheme not in ("file",):
        return "", f"unsupported_image_src:{parsed.scheme}"
    candidate = Path(unquote(parsed.path if parsed.scheme == "file" else src))
    if not candidate.is_absolute():
        candidate = source_dir / candidate
    if not candidate.exists() or not candidate.is_file():
        return "", "source_image_not_found"
    file_name = f"{asset['asset_id']}{candidate.suffix or '.img'}"
    shutil.copyfile(candidate, assets_dir / file_name)
    return file_name, "saved"


def image_extension(mime):
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }.get(mime.lower(), ".img")


def render_question_preview(question):
    answer_html = markdown_to_basic_html(str(question.get("answer") or "")) or "<p></p>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
{MATHJAX_HEAD}
{STYLE_HEAD}
<title>{html.escape(question['question_id'])}</title>
</head>
<body>
<h1>{html.escape(question['question_id'])}</h1>
<p>{html.escape(question['source_exam'])} / 第 {question['question_number']} 题 / {question['question_type']}</p>
<h2>题干</h2>
{question['stem_html']}
<h2>答案</h2>
{answer_html}
<h2>解析</h2>
{question['solution_html'] or '<p>暂无解析。</p>'}
<h2>质量标记</h2>
<p>{html.escape(', '.join(question.get('quality_flags') or []))}</p>
</body>
</html>
"""


def render_index(questions):
    rows = "\n".join(
        f"<tr><td><a href='questions/{q['question_id']}/preview.html'>{html.escape(q['question_id'])}</a></td>"
        f"<td>{html.escape(q['source_exam'])}</td><td>{q['question_number']}</td>"
        f"<td>{html.escape(q['question_type'])}</td><td>{html.escape(str(q.get('answer') or ''))}</td>"
        f"<td>{html.escape(', '.join(q.get('quality_flags') or []))}</td></tr>"
        for q in questions
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
{MATHJAX_HEAD}
{STYLE_HEAD}
<title>zcj tagging pilot</title>
</head>
<body>
<h1>zcj tagging pilot</h1>
<table border="1" cellspacing="0" cellpadding="6">
<tr><th>ID</th><th>来源</th><th>题号</th><th>题型</th><th>答案</th><th>质量标记</th></tr>
{rows}
</table>
</body>
</html>
"""


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
