#!/usr/bin/env python3
"""Validate a class source-question error set against its bank and blueprint."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from assemble_class_paper import assemble_class_paper
from recommend_structured_bank import build_safe_pool


EXPECTED_TYPES = {"single_choice": 8, "multiple_choice": 3, "fill_blank": 3, "solution": 2}


def validate(bank: Path, seed: int = 20260713) -> dict:
    bank = bank.resolve()
    payload = assemble_class_paper(bank, seed=seed, write=False)
    repeat = assemble_class_paper(bank, seed=seed, write=False)
    tagged = json.loads((bank / "tags" / "all_question_tags.json").read_text(encoding="utf-8"))
    safe, _ = build_safe_pool(tagged, bank)
    safe_ids = {row["question_id"] for row in safe}
    rows = payload["questions"]
    ids = [row["question_id"] for row in rows]
    errors: list[str] = []
    if payload.get("artifact_role") != "source_wrong_question_set":
        errors.append("payload is not marked as a source wrong-question set")
    if payload.get("student_visible") is not True:
        errors.append("source wrong-question set must be student_visible=true")
    if payload.get("requires_new_question_generation") is not False:
        errors.append("source wrong-question set must not require new-question generation")
    if ids != [row["question_id"] for row in repeat["questions"]]:
        errors.append("same seed produced different paper question IDs")
    if len(rows) != 16:
        errors.append(f"question count is {len(rows)}, expected 16")
    if len(ids) != len(set(ids)):
        errors.append("paper contains duplicate questions")
    if sum(int(row["points"]) for row in rows) != 100:
        errors.append("paper total is not 100 points")
    if dict(Counter(row["question_type"] for row in rows)) != EXPECTED_TYPES:
        errors.append(f"question type counts do not match {EXPECTED_TYPES}")
    if len({row["primary_knowledge"] for row in rows}) < 8:
        errors.append("paper covers fewer than 8 primary knowledge points")
    if max(Counter(row["source_exam"] for row in rows).values(), default=0) > 3:
        errors.append("more than 3 questions come from one source paper")
    for row in rows:
        if row.get("artifact_role") != "source_wrong_question" or row.get("student_visible") is not True:
            errors.append(f"source question is not marked for intentional student delivery: {row['question_id']}")
        if row.get("is_generated") is not False or row.get("generation_required") is not False:
            errors.append(f"wrong-question set item must remain an original source question: {row['question_id']}")
        if row["question_id"] not in safe_ids:
            errors.append(f"unsafe question selected: {row['question_id']}")
        if not str(row.get("stem_markdown") or row.get("stem_html") or "").strip():
            errors.append(f"missing stem: {row['question_id']}")
        if not str(row.get("answer") or "").strip():
            errors.append(f"missing answer: {row['question_id']}")
        if not str(row.get("solution_markdown") or row.get("solution_html") or "").strip():
            errors.append(f"missing solution: {row['question_id']}")
    if payload.get("coverage_gaps"):
        errors.extend(payload["coverage_gaps"])
    if errors:
        raise SystemExit("Class paper validation failed:\n- " + "\n- ".join(errors[:30]))
    return {
        "status": "pass",
        "artifact_role": payload["artifact_role"],
        "student_delivery_allowed": True,
        "source_question_delivery_intentional": True,
        "new_question_generation_required": False,
        "paper_title": payload["paper_title"],
        "class_name": payload["class_name"],
        "question_count": len(rows),
        "total_points": payload["total_points"],
        "type_counts": payload["type_counts"],
        "empirical_difficulty_counts": payload["empirical_difficulty_counts"],
        "knowledge_coverage_count": payload["knowledge_coverage_count"],
        "maximum_questions_from_one_source": max(Counter(row["source_exam"] for row in rows).values()),
        "complete_stem_answer_solution": True,
        "deterministic_with_seed": True,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate a class source-question error set.")
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--seed", type=int, default=20260713)
    args = parser.parse_args()
    print(json.dumps(validate(args.structured_dir, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
