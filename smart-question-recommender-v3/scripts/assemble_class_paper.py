#!/usr/bin/env python3
"""Select a deterministic 16-slot source-question error set for class review."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recommend_structured_bank import build_safe_pool


DEFAULT_BLUEPRINT = [
    {"section": "一、单项选择题", "question_type": "single_choice", "count": 8, "points": [5] * 8, "bands": ["easy"] * 3 + ["standard"] * 3 + ["challenge"] * 2},
    {"section": "二、多项选择题", "question_type": "multiple_choice", "count": 3, "points": [6] * 3, "bands": ["easy", "standard", "standard"]},
    {"section": "三、填空题", "question_type": "fill_blank", "count": 3, "points": [5] * 3, "bands": ["easy", "standard", "standard"]},
    {"section": "四、解答题", "question_type": "solution", "count": 2, "points": [12, 15], "bands": ["standard", "standard"]},
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _records_path(bank: Path) -> Path:
    for name in ("student_exam_records.jsonl", "simulated_student_records.jsonl"):
        path = bank / "student" / name
        if path.exists():
            return path
    raise SystemExit(f"Student records not found under {bank / 'student'}")


def _difficulty_band(correct_rate: float) -> str:
    if correct_rate >= 0.72:
        return "easy"
    if correct_rate >= 0.50:
        return "standard"
    return "challenge"


def _band_label(band: str) -> str:
    return {"easy": "基础", "standard": "标准", "challenge": "挑战"}.get(band, "标准")


def _band_fit(actual: str, target: str) -> float:
    order = {"easy": 0, "standard": 1, "challenge": 2}
    distance = abs(order.get(actual, 1) - order.get(target, 1))
    return 1.0 if distance == 0 else 0.62 if distance == 1 else 0.25


def _class_knowledge_stats(performance_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"correct": 0, "wrong": 0, "attempts": 0, "questions": 0})
    for row in performance_rows:
        knowledge = row.get("primary_knowledge") or "未分类"
        grouped[knowledge]["correct"] += int(row.get("correct") or 0)
        grouped[knowledge]["wrong"] += int(row.get("wrong") or 0)
        grouped[knowledge]["attempts"] += int(row.get("attempts") or 0)
        grouped[knowledge]["questions"] += 1
    result = []
    for knowledge, counts in grouped.items():
        attempts = counts["attempts"]
        correct_rate = counts["correct"] / attempts if attempts else 0.0
        result.append({
            "primary_knowledge": knowledge,
            "correct": int(counts["correct"]),
            "wrong": int(counts["wrong"]),
            "attempts": int(attempts),
            "question_count": int(counts["questions"]),
            "correct_rate": round(correct_rate, 3),
            "weakness": round(1 - correct_rate, 3),
        })
    return sorted(result, key=lambda row: (-row["weakness"], row["primary_knowledge"]))


def _candidate(question: dict[str, Any], performance: dict[str, Any], weakness_by_knowledge: dict[str, float]) -> dict[str, Any]:
    tags = question.get("tags") or {}
    detail = tags.get("tags_confidence_detail") or {}
    correct_rate = float(performance.get("correct_rate") if performance.get("correct_rate") is not None else 0.65)
    knowledge = tags.get("primary_knowledge") or "未分类"
    return {
        "question_id": question["question_id"],
        "source_question_id": question["question_id"],
        "artifact_role": "source_wrong_question",
        "is_generated": False,
        "student_visible": True,
        "generation_required": False,
        "display_id": question.get("display_id") or question["question_id"],
        "source_exam": question.get("source_exam") or "",
        "question_number": question.get("question_number"),
        "question_type": question.get("question_type") or "",
        "primary_knowledge": knowledge,
        "difficulty": tags.get("difficulty") or 3,
        "empirical_band": _difficulty_band(correct_rate),
        "empirical_band_label": _band_label(_difficulty_band(correct_rate)),
        "class_correct_rate": round(correct_rate, 3),
        "class_correct": int(performance.get("correct") or 0),
        "class_wrong": int(performance.get("wrong") or 0),
        "class_omitted": int(performance.get("omitted") or 0),
        "class_weakness": round(float(weakness_by_knowledge.get(knowledge, 0.35)), 3),
        "tag_reliability": round(float(detail.get("overall") or tags.get("tags_confidence") or 0), 3),
        "stem_markdown": question.get("stem_markdown") or "",
        "stem_html": question.get("stem_html") or "",
        "answer": str(question.get("answer") or ""),
        "solution_markdown": question.get("solution_markdown") or "",
        "solution_html": question.get("solution_html") or "",
        "options": question.get("options") or {},
        "assets": question.get("assets") or [],
        "preview_url": f"../questions/{question['question_id']}/preview.html",
    }


def _score_for_slot(candidate: dict[str, Any], target_band: str, selected: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    knowledge_counts = Counter(row["primary_knowledge"] for row in selected)
    source_counts = Counter(row["source_exam"] for row in selected)
    knowledge_count = knowledge_counts[candidate["primary_knowledge"]]
    source_count = source_counts[candidate["source_exam"]]
    components = {
        "class_weakness": candidate["class_weakness"],
        "difficulty_fit": round(_band_fit(candidate["empirical_band"], target_band), 3),
        "knowledge_coverage": 1.0 if knowledge_count == 0 else 0.42 if knowledge_count == 1 else 0.12,
        "cohort_fit": round(max(0.0, 1 - abs(candidate["class_correct_rate"] - 0.64) / 0.64), 3),
        "source_diversity": 1.0 if source_count == 0 else 0.67 if source_count == 1 else 0.28 if source_count == 2 else 0.0,
        "tag_reliability": candidate["tag_reliability"],
    }
    total = (
        0.35 * components["class_weakness"]
        + 0.20 * components["difficulty_fit"]
        + 0.15 * components["knowledge_coverage"]
        + 0.10 * components["cohort_fit"]
        + 0.10 * components["source_diversity"]
        + 0.10 * components["tag_reliability"]
    )
    return round(total, 4), components


def _slots(blueprint: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for section_index, section in enumerate(blueprint, 1):
        for index in range(section["count"]):
            rows.append({
                "section_index": section_index,
                "section": section["section"],
                "section_order": index + 1,
                "question_type": section["question_type"],
                "points": section["points"][index],
                "target_band": section["bands"][index],
            })
    return rows


def _availability(slot: dict[str, Any], candidates: list[dict[str, Any]]) -> int:
    return sum(row["question_type"] == slot["question_type"] and row["empirical_band"] == slot["target_band"] for row in candidates)


def _attach_reason(row: dict[str, Any], coverage_index: int) -> None:
    rate = round(row["class_correct_rate"] * 100)
    row["reason"] = (
        f"覆盖班级薄弱点“{row['primary_knowledge']}”；该知识点薄弱度 {row['class_weakness']:.0%}，"
        f"本题班级正确率 {rate}%，作为{row['empirical_band_label']}层题目，"
        f"是本卷对该知识点的第 {coverage_index} 次覆盖。"
    )


def assemble_class_paper(bank: Path, seed: int = 20260713, write: bool = True) -> dict[str, Any]:
    bank = bank.resolve()
    tags_path = bank / "tags" / "all_question_tags.json"
    performance_path = bank / "student" / "question_performance.json"
    if not tags_path.exists() or not performance_path.exists():
        raise SystemExit("Tagged questions and question performance are required before class paper assembly.")
    questions = _read_json(tags_path)
    performance_payload = _read_json(performance_path)
    performance_rows = performance_payload.get("questions") or []
    performance_by_id = {row["question_id"]: row for row in performance_rows}
    safe_pool, excluded = build_safe_pool(questions, bank)
    knowledge_stats = _class_knowledge_stats(performance_rows)
    weakness_by_knowledge = {row["primary_knowledge"]: row["weakness"] for row in knowledge_stats}
    candidates = [_candidate(question, performance_by_id.get(question["question_id"], {}), weakness_by_knowledge) for question in safe_pool]
    slots = _slots(DEFAULT_BLUEPRINT)
    selection_order = sorted(slots, key=lambda slot: (_availability(slot, candidates), slot["section_index"], slot["section_order"]))
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    gaps: list[str] = []

    for slot in selection_order:
        available = [row for row in candidates if row["question_id"] not in selected_ids and row["question_type"] == slot["question_type"]]
        if not available:
            gaps.append(f"{slot['section']}缺少可用题目")
            continue
        exact_band = [row for row in available if row["empirical_band"] == slot["target_band"]]
        if exact_band:
            available = exact_band
        scored = []
        for row in available:
            score, components = _score_for_slot(row, slot["target_band"], selected)
            scored.append((score, row["display_id"], row, components))
        best_score, _, choice, components = max(scored, key=lambda item: (item[0], item[1]))
        selected_ids.add(choice["question_id"])
        selected.append({**choice, **slot, "selection_score": best_score, "score_components": components})

    selected.sort(key=lambda row: (row["section_index"], {"easy": 0, "standard": 1, "challenge": 2}.get(row["empirical_band"], 1), -row["class_correct_rate"], row["display_id"]))
    coverage_counts: Counter[str] = Counter()
    section_numbers: Counter[int] = Counter()
    for global_number, row in enumerate(selected, 1):
        section_numbers[row["section_index"]] += 1
        row["paper_number"] = global_number
        row["section_question_number"] = section_numbers[row["section_index"]]
        coverage_counts[row["primary_knowledge"]] += 1
        _attach_reason(row, coverage_counts[row["primary_knowledge"]])

    alternates: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["question_id"] in selected_ids:
            continue
        score, components = _score_for_slot(candidate, candidate["empirical_band"], selected)
        alternates.append({**candidate, "selection_score": score, "score_components": components, "reason": f"安全备选题；覆盖“{candidate['primary_knowledge']}”，班级正确率 {candidate['class_correct_rate']:.0%}。"})
    alternates.sort(key=lambda row: (row["question_type"], -row["selection_score"], row["display_id"]))

    records = _read_jsonl(_records_path(bank))
    class_counts = Counter(row.get("class") or "未填写班级" for row in records)
    class_name = class_counts.most_common(1)[0][0] if class_counts else "未填写班级"
    total_points = sum(int(row["points"]) for row in selected)
    empirical_counts = Counter(row["empirical_band"] for row in selected)
    type_counts = Counter(row["question_type"] for row in selected)
    source_counts = Counter(row["source_exam"] for row in selected)
    if total_points != 100:
        gaps.append(f"当前总分为 {total_points}，未达到默认 100 分")
    payload = {
        "schema_version": "class-paper-v1",
        "artifact_role": "source_wrong_question_set",
        "student_visible": True,
        "requires_new_question_generation": False,
        "delivery_warning": "This artifact intentionally delivers source-bank questions as a class error set.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "paper_title": "高二数学原题错题集",
        "paper_type": "class_source_wrong_question_set",
        "class_name": class_name,
        "student_count": len(records),
        "simulated": bool(performance_payload.get("simulated")),
        "duration_minutes": 60,
        "total_points": total_points,
        "question_count": len(selected),
        "safe_pool_count": len(safe_pool),
        "excluded_count": len(excluded),
        "bank_signature": hashlib.sha256(tags_path.read_bytes() + performance_path.read_bytes()).hexdigest(),
        "blueprint": DEFAULT_BLUEPRINT,
        "type_counts": dict(type_counts),
        "empirical_difficulty_counts": dict(empirical_counts),
        "knowledge_coverage_count": len(coverage_counts),
        "knowledge_coverage": dict(coverage_counts),
        "source_counts": dict(source_counts),
        "class_weaknesses": knowledge_stats,
        "coverage_gaps": gaps,
        "questions": selected,
        "alternates": alternates,
        "quality_gate": {
            "stem_answer_solution_required": True,
            "safe_pool_only": True,
            "source_questions_student_visible": True,
            "source_question_delivery_intentional": True,
            "new_question_generation_required": False,
            "teacher_review_required_for_print": False,
        },
    }
    if write:
        paper_dir = bank / "paper"
        paper_dir.mkdir(parents=True, exist_ok=True)
        (paper_dir / "class_paper.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (paper_dir / "class_paper_history.jsonl").touch(exist_ok=True)
    return payload


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Select a class-wide 16-slot source-question error set."
    )
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    payload = assemble_class_paper(args.structured_dir, args.seed, write=not args.no_write)
    print(json.dumps({
        "schema_version": payload["schema_version"],
        "paper_title": payload["paper_title"],
        "class_name": payload["class_name"],
        "question_count": payload["question_count"],
        "total_points": payload["total_points"],
        "type_counts": payload["type_counts"],
        "empirical_difficulty_counts": payload["empirical_difficulty_counts"],
        "knowledge_coverage_count": payload["knowledge_coverage_count"],
        "coverage_gaps": payload["coverage_gaps"],
        "output": str(args.structured_dir.resolve() / "paper" / "class_paper.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
