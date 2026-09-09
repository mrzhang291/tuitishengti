#!/usr/bin/env python3
"""Import a configurable per-student score table and refresh downstream artifacts."""

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import openpyxl

TAGGER_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TAGGER_SCRIPTS))

from tagging import (  # noqa: E402
    difficulty_from_score_rate,
    _difficulty_basis,
    tag_question,
    _flat_question_tags,
    _write_json,
    _write_jsonl,
    _write_review_csv,
    calculate_student_mastery,
)


def _stable_exam_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return "exam_" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _question_number(value) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a per-student item-score workbook.")
    parser.add_argument("workspace", type=Path, help="Project root containing output/full")
    parser.add_argument("xlsx", type=Path, help="Per-student item-detail workbook")
    parser.add_argument("--exam-id", help="Stable exam id; defaults to a hash of the exam name")
    parser.add_argument("--exam-name", help="Display name; defaults to the workbook filename")
    parser.add_argument("--question-id-prefix", help="Question id prefix used to select this exam")
    parser.add_argument("--sheet", help="Worksheet name; defaults to the active sheet")
    parser.add_argument("--subject", default="数学")
    parser.add_argument("--header-row", type=int, default=2)
    parser.add_argument("--student-start-row", type=int, default=3)
    parser.add_argument("--question-start-column", type=int, default=9)
    parser.add_argument("--name-column", type=int, default=2)
    parser.add_argument("--student-id-column", type=int, default=4)
    parser.add_argument("--class-column", type=int, default=5)
    parser.add_argument("--total-score-column", type=int, default=6)
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    xlsx = args.xlsx.resolve()
    questions_path = workspace / "output" / "full" / "questions.jsonl"
    tags_path = workspace / "output" / "full" / "tags" / "all_question_tags.json"
    records_path = workspace / "test-data" / "student_exam_records.jsonl"
    mastery_path = workspace / "output" / "full" / "student" / "student_mastery.jsonl"
    if not xlsx.is_file():
        raise SystemExit(f"Workbook not found: {xlsx}")
    if not questions_path.is_file() or not tags_path.is_file():
        raise SystemExit("Workspace is missing output/full/questions.jsonl or tags/all_question_tags.json")
    exam_name = str(args.exam_name or xlsx.stem).strip()
    exam_id = str(args.exam_id or _stable_exam_id(exam_name)).strip()
    question_prefix = str(args.question_id_prefix or exam_id).strip()

    # 1. Load questions and select the target exam without school-specific IDs.
    questions = [json.loads(line) for line in questions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    exam_by_number = {}
    for q in questions:
        matches_exam = (
            str(q.get("exam_id") or "") == exam_id
            or str(q.get("question_id") or "").startswith(question_prefix)
            or str(q.get("source_exam") or "").strip() == exam_name
        )
        if matches_exam and q.get("question_number") is not None:
            exam_by_number[int(q["question_number"])] = q
    if not exam_by_number:
        raise SystemExit(
            f"No questions matched exam_id={exam_id!r}, prefix={question_prefix!r}, or exam_name={exam_name!r}"
        )

    print(f"Loaded {len(questions)} questions ({len(exam_by_number)} target exam)")

    # 2. Parse Excel
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    if args.sheet and args.sheet not in wb.sheetnames:
        raise SystemExit(f"Worksheet not found: {args.sheet}; available: {', '.join(wb.sheetnames)}")
    ws = wb[args.sheet] if args.sheet else wb.active
    question_columns: dict[int, int] = {}
    for column in range(args.question_start_column, ws.max_column + 1):
        number = _question_number(ws.cell(args.header_row, column).value)
        if number is not None and number in exam_by_number:
            question_columns[number] = column
    if not question_columns:
        raise SystemExit("No question-number columns in the workbook matched the selected exam")
    students = []
    for row in range(args.student_start_row, ws.max_row + 1):
        name = ws.cell(row, args.name_column).value
        sid = ws.cell(row, args.student_id_column).value
        if not name or not sid:
            continue
        cls = ws.cell(row, args.class_column).value or ""
        total = ws.cell(row, args.total_score_column).value
        raw_scores = {number: ws.cell(row, column).value for number, column in question_columns.items()}
        students.append({"name": name, "student_id": str(sid), "class": cls, "total_score": total, "raw": raw_scores})
    print(f"Parsed {len(students)} students from Excel")

    # 3. Convert raw answers/scores to per-question records
    # For choice/multi-choice (q1-11): raw is option letters; compare with answer to determine score
    # For fill_blank/solution (q12-19): raw is numeric score directly
    student_records = []
    per_question_scores = defaultdict(list)  # question_number -> list of (score, full_score)

    for stu in students:
        answers = []
        for qnum in sorted(question_columns):
            q = exam_by_number[qnum]
            raw = stu["raw"].get(qnum)
            full = q.get("score") or 0
            qtype = q.get("question_type")
            correct_answer = (q.get("answer") or "").strip()
            score, is_correct, status, error_reason = _score_answer(raw, full, qtype, correct_answer)
            answers.append({
                "question_number": qnum,
                "question_id": q["question_id"],
                "question_type": qtype,
                "full_score": full,
                "raw_answer": _format_raw(raw),
                "score": score,
                "is_correct": is_correct,
                "status": status,
                "error_reason": error_reason,
            })
            if full > 0:
                per_question_scores[qnum].append((score, full))
        student_records.append({
            "student_id": stu["student_id"],
            "name": stu["name"],
            "ticket_number": "",
            "class": stu["class"],
            "exam_id": exam_id,
            "exam_name": exam_name,
            "subject": args.subject,
            "total_score": stu["total_score"],
            "answers": answers,
            "calc_total": sum(a["score"] for a in answers),
            "diff": "",
        })

    # 4. Write student_exam_records.jsonl
    _write_jsonl(records_path, student_records)
    print(f"Wrote {len(student_records)} records -> {records_path}")

    # 5. Compute score_rate per midterm question and update questions + tags
    score_data = {}  # question_id -> {score_rate, avg_score, full_score}
    for qnum, scores in per_question_scores.items():
        q = exam_by_number[qnum]
        full = q.get("score") or 0
        total_earned = sum(s for s, _ in scores)
        avg = round(total_earned / len(scores), 2) if scores else 0
        rate = round(100 * total_earned / (full * len(scores)), 1) if scores and full else 0
        score_data[q["question_id"]] = {"score_rate": rate, "avg_score": avg, "full_score": full}

    # Update questions.jsonl
    for q in questions:
        if q["question_id"] in score_data:
            q.update(score_data[q["question_id"]])
    _write_jsonl(questions_path, questions)
    print(f"Updated {len(score_data)} midterm questions with score_rate in questions.jsonl")

    # 6. Re-tag all questions (tagging.py reads score_rate for difficulty)
    # Re-tag midterm questions with score_rate attached; others stay as before
    tagged = [tag_question(q) for q in questions]

    # Preserve any teacher-reviewed fields if they existed (none yet, but safe)
    _write_json(tags_path, tagged)
    _write_jsonl(workspace / "output" / "full" / "tags" / "question_tags.jsonl", _flat_question_tags(tagged))
    _write_review_csv(workspace / "output" / "full" / "review" / "all_tag_review.csv", tagged)
    print(f"Re-tagged {len(tagged)} questions -> {tags_path}")

    # 7. Recompute student mastery
    tags_by_question = {item["question_id"]: item["tags"] for item in tagged}
    mastery = calculate_student_mastery(student_records, tags_by_question)
    _write_jsonl(mastery_path, mastery)
    print(f"Wrote {len(mastery)} mastery rows -> {mastery_path}")

    # 8. Summary
    print()
    print("=== Score-rate summary (midterm) ===")
    for qnum in sorted(exam_by_number.keys()):
        q = exam_by_number.get(qnum)
        if not q:
            continue
        sd = score_data.get(q["question_id"], {"score_rate": None})
        rate = sd["score_rate"]
        # find the tagged version
        t = next((x for x in tagged if x["question_id"] == q["question_id"]), None)
        diff = t["tags"]["difficulty"] if t else "?"
        print(f"  Q{qnum:2d} | {q['question_type']:16s} | 满分{q.get('score'):2d} | 得分率 {rate if rate is not None else 'N/A':>5}% | 难度{diff}")
    return 0


def _score_answer(raw, full, qtype, correct_answer):
    """Return (score, is_correct, status, error_reason)."""
    if raw is None or raw == "":
        return 0, False, "blank", "未作答"
    # Choice questions: raw is option letters like "B", "BD"
    if qtype in ("single_choice", "multiple_choice"):
        raw_str = str(raw).strip().upper().replace(" ", "")
        correct = correct_answer.upper().replace(" ", "").replace("$", "")
        # Strip LaTeX/markdown from correct answer for comparison
        correct = _strip_latex(correct)
        if raw_str == correct:
            return full, True, "correct", ""
        # Partial credit for multiple_choice (simplified: any correct selection without wrong gets half)
        if qtype == "multiple_choice" and full > 0:
            correct_set = set(correct)
            raw_set = set(raw_str)
            if raw_set and raw_set.issubset(correct_set) and len(raw_set) < len(correct_set):
                # partial: typically 2 or 3 points out of 6
                partial = 2 if len(correct_set) >= 3 else 3
                return partial, False, "partial", f"选对部分({raw_str} vs {correct})"
        return 0, False, "wrong", f"答案 {raw_str} vs 正确 {correct}"
    # Numeric score for fill_blank / solution
    try:
        score = float(raw)
    except (ValueError, TypeError):
        return 0, False, "unparseable", f"无法解析得分: {raw}"
    is_correct = score >= full
    if is_correct:
        return int(score), True, "correct", ""
    if score <= 0:
        return 0, False, "zero", "得 0 分"
    return int(score), False, "partial", f"得 {score}/{full}"


def _strip_latex(text):
    """Remove common LaTeX/markdown wrappers for answer comparison."""
    for token in (r"\displaystyle", r"\dfrac", r"\frac", r"\sqrt", r"\ln", r"\log"):
        text = text.replace(token, "")
    text = text.replace("{", "").replace("}", "").replace("\\", "").replace("$", "").replace(" ", "")
    return text


def _format_raw(raw):
    if raw is None:
        return ""
    return str(raw)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
