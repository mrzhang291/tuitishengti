#!/usr/bin/env python3
"""Initialize source-error-set delivery and personalized new-question generation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Required artifact not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _contract(question: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "must_keep": [
            question.get("primary_knowledge") or "目标主知识点",
            "母题主解法",
            "母题关键认知瓶颈或易错点",
        ],
        "may_change": ["question_angle", "condition_organization", "representation", "context"],
        "must_not": ["source_copy", "paraphrase_only", "numbers_only", "option_reorder_only"],
    }


def _mother_slot(question: dict[str, Any], slot_id: str) -> dict[str, Any]:
    mother_id = question.get("mother_question_id") or question.get("question_id")
    return {
        "slot_id": slot_id,
        "mother_question_id": mother_id,
        "student_visible": False,
        "generation_required": True,
        "question_type": question.get("question_type"),
        "target_knowledge": question.get("primary_knowledge") or question.get("target_knowledge"),
        "empirical_difficulty_reference": question.get("empirical_band") or question.get("difficulty"),
        "weakness_evidence": {
            "class_correct_rate": question.get("class_correct_rate"),
            "class_correct": question.get("class_correct"),
            "class_wrong": question.get("class_wrong"),
            "target_mastery": question.get("target_mastery"),
        },
        "selection_reason": question.get("reason") or "Selected as a teacher-only source question.",
        "difficulty_preservation_contract": _contract(question),
        "mother_question": question,
    }


def initialize_workspace(bank: Path, *, reset_generated: bool = False) -> dict[str, Any]:
    bank = bank.resolve()
    paper = _read_json(bank / "paper" / "class_paper.json")
    recommendations = _read_json(bank / "student" / "recommendations.json")
    generated_at = datetime.now(timezone.utc).isoformat()

    source_questions = paper.get("questions") or []
    class_plan = {
        "schema_version": "class-generation-disabled-v1",
        "generated_at": generated_at,
        "scope": "class_source_wrong_question_set",
        "artifact_role": "source_wrong_question_set",
        "status": "not_applicable_source_questions_are_delivered_directly",
        "student_visible": False,
        "requires_new_question_generation": False,
        "source_artifact": "paper/class_paper.json",
        "paper_title": paper.get("paper_title"),
        "class_name": paper.get("class_name"),
        "duration_minutes": paper.get("duration_minutes"),
        "total_points": paper.get("total_points"),
        "blueprint": paper.get("blueprint") or [],
        "class_weaknesses": paper.get("class_weaknesses") or [],
        "slots": [],
        "delivery_note": "Class source questions are intentionally delivered as the original wrong-question set; no new-question generation runs in this mode.",
    }
    _write_json(bank / "generation" / "mother_question_plan.json", class_plan)

    student_plans = []
    for student in recommendations.get("students") or []:
        slots = [
            _mother_slot(question, f"{student.get('student_id')}-q{index:03d}")
            for index, question in enumerate(student.get("recommendations") or [], 1)
        ]
        student_plans.append(
            {
                "student_id": student.get("student_id"),
                "name": student.get("name"),
                "class": student.get("class"),
                "weak_points": student.get("weak_points") or [],
                "student_visible": False,
                "slots": slots,
                "coverage_gaps": student.get("coverage_gaps") or [],
            }
        )
    _write_json(
        bank / "generation" / "student_mother_question_plans.json",
        {
            "schema_version": "student-mother-question-plans-v1",
            "generated_at": generated_at,
            "scope": "student_practice",
            "student_visible": False,
            "requires_new_question_generation": True,
            "students": student_plans,
        },
    )

    generated_path = bank / "generation" / "generated_questions.json"
    _write_json(
        generated_path,
        {
            "schema_version": "generated-question-set-v1",
            "generated_at": generated_at,
            "scope": "class_source_wrong_question_set",
            "status": "not_applicable_source_questions_are_delivered_directly",
            "questions": [],
        },
    )
    _write_json(
        bank / "generation" / "generated_question_candidates.json",
        {
            "schema_version": "generated-question-candidate-pool-v1",
            "generated_at": generated_at,
            "scope": "class_source_wrong_question_set",
            "status": "not_applicable_source_questions_are_delivered_directly",
            "candidate_count_per_slot": 0,
            "slots": [],
        },
    )

    student_paper_path = bank / "paper" / "student_paper.json"
    _write_json(
        student_paper_path,
        {
            "schema_version": "source-wrong-question-set-delivery-v1",
            "generated_at": generated_at,
            "artifact_role": "source_wrong_question_set",
            "student_visible": True,
            "requires_new_question_generation": False,
            "status": "ready",
            "paper_title": paper.get("paper_title"),
            "class_name": paper.get("class_name"),
            "duration_minutes": paper.get("duration_minutes"),
            "total_points": paper.get("total_points"),
            "questions": source_questions,
            "coverage_gaps": paper.get("coverage_gaps") or [],
        },
    )

    generated_recommendations_path = bank / "student" / "generated_recommendations.json"
    if reset_generated or not generated_recommendations_path.exists():
        _write_json(
            generated_recommendations_path,
            {
                "schema_version": "generated-student-recommendations-v1",
                "generated_at": generated_at,
                "status": "blocked_pending_generation_and_approval",
                "students": [
                    {
                        "student_id": student.get("student_id"),
                        "name": student.get("name"),
                        "recommendations": [],
                        "coverage_gaps": [f"{len(student.get('slots') or [])} recommendation slots are awaiting generated questions."],
                    }
                    for student in student_plans
                ],
            },
        )

    provenance_path = bank / "paper" / "teacher_provenance.json"
    if reset_generated or not provenance_path.exists():
        _write_json(
            provenance_path,
            {
                "schema_version": "teacher-provenance-v1",
                "generated_at": generated_at,
                "student_visible": False,
                "mappings": [],
            },
        )

    return {
        "status": "initialized",
        "class_source_question_count": len(source_questions),
        "class_mother_slots": 0,
        "class_generation_status": "not_applicable_source_questions_are_delivered_directly",
        "student_count": len(student_plans),
        "student_mother_slots": sum(len(row["slots"]) for row in student_plans),
        "generated_question_count": len(_read_json(generated_path).get("questions") or []),
        "student_paper_question_count": len(_read_json(student_paper_path).get("questions") or []),
        "generation_dir": str(bank / "generation"),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Initialize a class source-error-set delivery and personalized new-question plans."
    )
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--reset-generated", action="store_true")
    args = parser.parse_args()
    print(json.dumps(initialize_workspace(args.structured_dir, reset_generated=args.reset_generated), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
