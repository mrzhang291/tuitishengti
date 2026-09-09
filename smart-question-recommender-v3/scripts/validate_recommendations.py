#!/usr/bin/env python3
"""Validate teacher-only mother-question recommendation-plan invariants."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from recommend_structured_bank import build_safe_pool, generate_recommendations


def validate(bank: Path, top_n: int = 5, seed: int = 20260713) -> dict:
    bank = bank.resolve()
    payload = generate_recommendations(bank, top_n=top_n, seed=seed, write=False)
    repeat = generate_recommendations(bank, top_n=top_n, seed=seed, write=False)
    questions = json.loads((bank / "tags" / "all_question_tags.json").read_text(encoding="utf-8"))
    safe_pool, _ = build_safe_pool(questions, bank)
    safe_ids = {row["question_id"] for row in safe_pool}
    errors: list[str] = []
    incomplete_sets = 0
    fallback_sets = 0

    if payload.get("artifact_role") != "mother_question_recommendation_plan":
        errors.append("payload is not marked as a mother-question recommendation plan")
    if payload.get("student_visible") is not False:
        errors.append("mother-question recommendation plan must be student_visible=false")
    if payload.get("requires_new_question_generation") is not True:
        errors.append("mother-question recommendation plan must require new-question generation")

    first_ids = {
        row["student_id"]: [item["question_id"] for item in row["recommendations"]]
        for row in payload["students"]
    }
    repeat_ids = {
        row["student_id"]: [item["question_id"] for item in row["recommendations"]]
        for row in repeat["students"]
    }
    if first_ids != repeat_ids:
        errors.append("same seed produced different question IDs")

    for student in payload["students"]:
        rows = student.get("recommendations") or []
        ids = [row["question_id"] for row in rows]
        if len(rows) != top_n:
            incomplete_sets += 1
            if not student.get("coverage_gaps"):
                errors.append(f"{student['student_id']}: incomplete set without coverage gap")
        if len(ids) != len(set(ids)):
            errors.append(f"{student['student_id']}: duplicate question IDs")
        if student.get("coverage_gaps"):
            fallback_sets += 1
        for row in rows:
            if row.get("artifact_role") != "mother_question" or row.get("student_visible") is not False:
                errors.append(f"{row['question_id']}: source question is not marked teacher-only")
            if row.get("is_generated") is not False or row.get("generation_required") is not True:
                errors.append(f"{row['question_id']}: source question must require generation before delivery")
            if row["question_id"] not in safe_ids:
                errors.append(f"{student['student_id']}: unsafe question {row['question_id']}")
            if not str(row.get("stem_markdown") or row.get("stem_html") or "").strip():
                errors.append(f"{row['question_id']}: missing stem")
            if not str(row.get("answer") or "").strip():
                errors.append(f"{row['question_id']}: missing answer")
            if not str(row.get("solution_markdown") or row.get("solution_html") or "").strip():
                errors.append(f"{row['question_id']}: missing solution")
        available_targets = {row["target_knowledge"] for row in rows + (student.get("alternates") or [])}
        selected_targets = {row["target_knowledge"] for row in rows}
        if len(available_targets) >= 2 and len(selected_targets) < 2 and not student.get("coverage_gaps"):
            errors.append(f"{student['student_id']}: failed to cover two available targets")

    source = Path(payload["data_source"]).resolve()
    if bank not in source.parents:
        errors.append("data source is outside the requested structured bank")
    if errors:
        raise SystemExit("Recommendation validation failed:\n- " + "\n- ".join(errors[:30]))
    return {
        "status": "pass",
        "students": payload["student_count"],
        "questions_per_student": top_n,
        "full_sets": payload["student_count"] - incomplete_sets,
        "fallback_sets": fallback_sets,
        "safe_pool": payload["safe_pool_count"],
        "artifact_role": payload["artifact_role"],
        "student_delivery_allowed": False,
        "new_question_generation_required": True,
        "complete_stem_answer_solution": True,
        "deterministic_with_seed": True,
        "data_source": str(source),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate a teacher-only mother-question recommendation plan.")
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260713)
    args = parser.parse_args()
    print(json.dumps(validate(args.structured_dir, args.top_n, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
