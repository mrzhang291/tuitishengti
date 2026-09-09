#!/usr/bin/env python3
"""Generate deterministic simulated student-answer data for workbench development."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE_CORRECT_RATE = {1: 0.91, 2: 0.82, 3: 0.69, 4: 0.51, 5: 0.34}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def generate_performance(bank: Path, student_count: int = 48, seed: int = 20260713) -> dict[str, Any]:
    bank = bank.resolve()
    tags_path = bank / "tags" / "all_question_tags.json"
    if not tags_path.exists():
        raise SystemExit(f"Tag artifact not found: {tags_path}")
    questions = json.loads(tags_path.read_text(encoding="utf-8"))
    rng = random.Random(seed)
    student_profiles = [
        {
            "student_id": f"SIM-{index:03d}",
            "name": f"模拟学生{index:03d}",
            "class": "高二模拟班",
            "ability": _clamp(rng.gauss(0, 0.12), -0.28, 0.28),
        }
        for index in range(1, student_count + 1)
    ]
    item_bias = {question["question_id"]: _clamp(rng.gauss(0, 0.07), -0.16, 0.16) for question in questions}
    aggregate = defaultdict(lambda: {"attempts": 0, "correct": 0, "wrong": 0, "omitted": 0})
    records = []

    for student in student_profiles:
        answers = []
        for question in questions:
            question_id = question["question_id"]
            tags = question.get("tags") or {}
            difficulty = int(tags.get("difficulty") or 3)
            full_score = float(question.get("full_score") or question.get("score") or (5 if question.get("question_type") != "solution" else 12))
            attempted = rng.random() >= 0.035
            if not attempted:
                aggregate[question_id]["omitted"] += 1
                answers.append(
                    {
                        "question_id": question_id,
                        "attempted": False,
                        "is_correct": False,
                        "score": 0,
                        "full_score": full_score,
                    }
                )
                continue
            probability = _clamp(
                BASE_CORRECT_RATE.get(difficulty, 0.69) + student["ability"] + item_bias[question_id],
                0.08,
                0.97,
            )
            is_correct = rng.random() < probability
            if is_correct:
                score = full_score
                aggregate[question_id]["correct"] += 1
            else:
                if question.get("question_type") == "solution":
                    fraction = rng.choice([0, 0.25, 0.4, 0.5, 0.6])
                    score = round(full_score * fraction, 1)
                else:
                    score = 0
                aggregate[question_id]["wrong"] += 1
            aggregate[question_id]["attempts"] += 1
            answers.append(
                {
                    "question_id": question_id,
                    "attempted": True,
                    "is_correct": is_correct,
                    "score": score,
                    "full_score": full_score,
                }
            )
        records.append(
            {
                "schema_version": "simulated-student-record-v1",
                "simulated": True,
                "student_id": student["student_id"],
                "name": student["name"],
                "class": student["class"],
                "answers": answers,
            }
        )

    question_stats = []
    for question in questions:
        question_id = question["question_id"]
        counts = aggregate[question_id]
        attempts = counts["attempts"]
        correct_rate = round(counts["correct"] / attempts, 3) if attempts else 0
        tags = question.get("tags") or {}
        question_stats.append(
            {
                "question_id": question_id,
                "display_id": question.get("display_id") or question_id,
                "source_exam": question.get("source_exam") or "",
                "question_number": question.get("question_number"),
                "question_type": question.get("question_type") or "",
                "attempts": attempts,
                "correct": counts["correct"],
                "wrong": counts["wrong"],
                "omitted": counts["omitted"],
                "correct_rate": correct_rate,
                "wrong_rate": round(1 - correct_rate, 3),
                "difficulty": tags.get("difficulty"),
                "primary_knowledge": tags.get("primary_knowledge") or "",
            }
        )

    total_attempts = sum(item["attempts"] for item in question_stats)
    total_correct = sum(item["correct"] for item in question_stats)
    summary = {
        "schema_version": "simulated-student-performance-v1",
        "simulated": True,
        "simulation_note": "仅用于工作台与后续推题流程测试，不代表真实学生成绩。",
        "seed": seed,
        "student_count": student_count,
        "question_count": len(question_stats),
        "total_attempts": total_attempts,
        "total_correct": total_correct,
        "overall_correct_rate": round(total_correct / total_attempts, 3) if total_attempts else 0,
        "high_error_question_count": sum(item["correct_rate"] < 0.6 for item in question_stats),
        "questions": question_stats,
    }
    student_dir = bank / "student"
    student_dir.mkdir(parents=True, exist_ok=True)
    (student_dir / "question_performance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_jsonl(student_dir / "simulated_student_records.jsonl", records)
    return {
        "student_count": student_count,
        "question_count": len(question_stats),
        "overall_correct_rate": summary["overall_correct_rate"],
        "high_error_question_count": summary["high_error_question_count"],
        "question_performance": str(student_dir / "question_performance.json"),
        "student_records": str(student_dir / "simulated_student_records.jsonl"),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Generate simulated student performance for a structured bank.")
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--students", type=int, default=48)
    parser.add_argument("--seed", type=int, default=20260713)
    args = parser.parse_args()
    if args.students < 1:
        raise SystemExit("--students must be positive")
    print(json.dumps(generate_performance(args.structured_dir, args.students, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
