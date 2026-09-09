#!/usr/bin/env python3
"""Select deterministic mother questions for later new-question generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


PRIOR_MASTERY = 0.65
PRIOR_WEIGHT = 2.0
MIN_OVERALL_CONFIDENCE = 0.85
MIN_PRIMARY_CONFIDENCE = 0.80
MIN_CLASSIFICATION_MARGIN = 0.12
MIN_STRUCTURE_CONFIDENCE = 0.85
BENIGN_QUALITY_FLAGS = {"contains_images", "answer_math_display_repaired"}
SOURCE_BLOCKING_FLAGS = {
    "answer_contamination",
    "source_solution_invalid",
    "answer_solution_mismatch",
    "solution_alignment_warning",
    "ocr_solution_contamination",
}
NON_CONCRETE_ANSWERS = {"见解析", "详见解析", "见详解", "略", "答案见解析", "答案略"}
TYPE_QUOTA = {"single_choice": 2, "multiple_choice": 1, "fill_blank": 1, "solution": 1}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalise_stem(value: str) -> str:
    value = re.sub(r"^\s*#{1,6}\s*\d+[.、]?\s*", "", value or "")
    value = re.sub(r"!\[[^]]*]\([^)]*\)", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def _answer_is_concrete(value: Any) -> bool:
    text = re.sub(r"[\s：:。．.!！]+", "", str(value or ""))
    return bool(text) and text not in NON_CONCRETE_ANSWERS


def _leaf(tag: str) -> str:
    return (tag or "").split("·")[-1].strip()


def _tag_is_reliable(tags: dict[str, Any]) -> bool:
    detail = tags.get("tags_confidence_detail") or {}
    if float(detail.get("structure") or 0) < MIN_STRUCTURE_CONFIDENCE:
        return False
    if tags.get("tags_reviewed"):
        return True
    overall = float(detail.get("overall") or tags.get("tags_confidence") or 0)
    primary = float(detail.get("primary_knowledge") or 0)
    margin = float(tags.get("classification_margin") or detail.get("classification_margin") or 0)
    return (
        overall >= MIN_OVERALL_CONFIDENCE
        and primary >= MIN_PRIMARY_CONFIDENCE
        and margin >= MIN_CLASSIFICATION_MARGIN
    )


def _assets_are_available(question: dict[str, Any], bank: Path) -> bool:
    for asset in question.get("assets") or []:
        if not asset.get("saved", True):
            return False
        relative = asset.get("relative_path")
        if relative and not (bank / "questions" / question["question_id"] / relative).exists():
            return False
    return True


def question_eligibility(question: dict[str, Any], bank: Path) -> tuple[bool, list[str]]:
    """Return whether a question is safe to recommend and machine-readable reasons."""
    reasons: list[str] = []
    if not str(question.get("stem_markdown") or "").strip():
        reasons.append("missing_stem")
    if not str(question.get("answer") or "").strip():
        reasons.append("missing_answer")
    elif not _answer_is_concrete(question.get("answer")):
        reasons.append("non_concrete_answer")
    if not str(question.get("solution_markdown") or "").strip():
        reasons.append("missing_solution")
    blocking_flags = sorted(set(question.get("quality_flags") or []) - BENIGN_QUALITY_FLAGS)
    reasons.extend(f"quality:{flag}" for flag in blocking_flags)
    source_flags = sorted(set(question.get("quality_flags") or []).intersection(SOURCE_BLOCKING_FLAGS))
    reasons.extend(f"source_quality:{flag}" for flag in source_flags)
    if not _assets_are_available(question, bank):
        reasons.append("missing_image_asset")
    tags = question.get("tags") or {}
    if not _tag_is_reliable(tags):
        reasons.append("unreliable_tag")
    return not reasons, reasons


def build_safe_pool(questions: list[dict[str, Any]], bank: Path) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    safe: list[dict[str, Any]] = []
    excluded: dict[str, list[str]] = {}
    seen: list[str] = []
    for question in questions:
        eligible, reasons = question_eligibility(question, bank)
        if not eligible:
            excluded[question.get("question_id") or "unknown"] = reasons
            continue
        normalised = _normalise_stem(question.get("stem_markdown") or question.get("stem_html") or "")
        if any(
            normalised == previous
            or (len(normalised) >= 40 and SequenceMatcher(None, normalised, previous).ratio() >= 0.96)
            for previous in seen
        ):
            excluded[question["question_id"]] = ["near_duplicate"]
            continue
        seen.append(normalised)
        safe.append(question)
    return safe, excluded


def _mastery_status(score: float) -> str:
    if score < 0.45:
        return "严重薄弱"
    if score < 0.65:
        return "中度薄弱"
    if score < 0.80:
        return "轻度薄弱"
    return "已掌握"


def build_mastery_rows(
    students: list[dict[str, Any]], questions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Derive mastery with a prior; omitted answers provide no negative evidence."""
    question_by_id = {question["question_id"]: question for question in questions}
    rows: list[dict[str, Any]] = []
    for student in students:
        grouped: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "performance_sum": 0.0,
                "evidence_weight": 0.0,
                "attempts": 0,
                "correct": 0,
                "wrongs": 0,
                "partials": 0,
                "omitted": 0,
                "confidences": [],
            }
        )
        for answer in student.get("answers") or []:
            question = question_by_id.get(answer.get("question_id"))
            if not question:
                continue
            tags = question.get("tags") or {}
            if not _tag_is_reliable(tags):
                continue
            primary = tags.get("primary_knowledge")
            if not primary:
                continue
            item = grouped[primary]
            if not answer.get("attempted", True):
                item["omitted"] += 1
                continue
            full_score = float(answer.get("full_score") or question.get("full_score") or 1)
            performance = _clamp(float(answer.get("score") or 0) / full_score) if full_score else 0.0
            item["performance_sum"] += performance
            item["evidence_weight"] += 1.0
            item["attempts"] += 1
            if performance >= 0.999:
                item["correct"] += 1
            elif performance <= 0.001:
                item["wrongs"] += 1
            else:
                item["partials"] += 1
            detail = tags.get("tags_confidence_detail") or {}
            item["confidences"].append(float(detail.get("primary_knowledge") or tags.get("tags_confidence") or 0))

        for tag_name, item in grouped.items():
            mastery = (
                PRIOR_MASTERY * PRIOR_WEIGHT + item["performance_sum"]
            ) / (PRIOR_WEIGHT + item["evidence_weight"])
            confidence = (
                sum(item["confidences"]) / len(item["confidences"])
                if item["confidences"]
                else 0.0
            )
            rows.append(
                {
                    "schema_version": "student-mastery-v2",
                    "simulated": bool(student.get("simulated")),
                    "student_id": student["student_id"],
                    "name": student.get("name") or student["student_id"],
                    "tag_name": tag_name,
                    "mastery_score": round(mastery, 3),
                    "status": _mastery_status(mastery),
                    "attempts": item["attempts"],
                    "correct": item["correct"],
                    "wrongs": item["wrongs"],
                    "partials": item["partials"],
                    "omitted": item["omitted"],
                    "recent_wrongs": min(item["wrongs"] + item["partials"], 3),
                    "evidence_confidence": round(confidence, 3),
                    "prior_mastery": PRIOR_MASTERY,
                    "prior_weight": PRIOR_WEIGHT,
                }
            )
    return sorted(rows, key=lambda row: (row["student_id"], row["mastery_score"], row["tag_name"]))


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _recent_question_ids(history: list[dict[str, Any]], student_id: str) -> set[str]:
    now = datetime.now(timezone.utc)
    student_rows = [row for row in history if row.get("student_id") == student_id]
    student_rows.sort(key=lambda row: row.get("generated_at") or row.get("timestamp") or "", reverse=True)
    last_two = student_rows[:2]
    blocked: set[str] = set()
    for row in student_rows:
        stamp = _parse_time(row.get("generated_at") or row.get("timestamp") or "")
        if stamp and (now - stamp).days < 30:
            blocked.update(row.get("question_ids") or [row.get("question_id")])
    for row in last_two:
        blocked.update(row.get("question_ids") or [row.get("question_id")])
    blocked.discard(None)
    return blocked


def _target_difficulty(mastery: float) -> float:
    if mastery < 0.45:
        return 1.5
    if mastery < 0.65:
        return 2.5
    return 3.5


def _knowledge_match(question: dict[str, Any], weak: dict[str, Any], tag_themes: dict[str, str]) -> float:
    tags = question.get("tags") or {}
    target = weak["tag_name"]
    if tags.get("primary_knowledge") == target:
        return 1.0
    if any(item == target or _leaf(item) == _leaf(target) for item in tags.get("secondary_knowledge") or []):
        return 0.6
    if tag_themes.get(target) and tags.get("curriculum_theme") == tag_themes[target]:
        return 0.3
    return 0.0


def _score_candidate(
    question: dict[str, Any],
    weak: dict[str, Any],
    knowledge_match: float,
    performance: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    tags = question.get("tags") or {}
    difficulty = float(tags.get("difficulty") or 3)
    target = _target_difficulty(float(weak["mastery_score"]))
    difficulty_fit = _clamp(1 - abs(difficulty - target) / 3.5)
    correct_rate = float(performance.get("correct_rate", 0.65))
    cohort_fit = _clamp(1 - abs(correct_rate - 0.68) / 0.55)
    detail = tags.get("tags_confidence_detail") or {}
    reliability = float(detail.get("overall") or tags.get("tags_confidence") or 0)
    components = {
        "weakness": round(1 - float(weak["mastery_score"]), 3),
        "difficulty_fit": round(difficulty_fit, 3),
        "knowledge_match": round(knowledge_match, 3),
        "cohort_fit": round(cohort_fit, 3),
        "freshness": 1.0,
        "tag_reliability": round(reliability, 3),
    }
    total = (
        0.35 * components["weakness"]
        + 0.20 * components["difficulty_fit"]
        + 0.15 * components["knowledge_match"]
        + 0.10 * components["cohort_fit"]
        + 0.10 * components["freshness"]
        + 0.10 * components["tag_reliability"]
    )
    return round(total, 4), components


def _candidate_rows(
    student: dict[str, Any],
    weak_points: list[dict[str, Any]],
    safe_pool: list[dict[str, Any]],
    performance_by_id: dict[str, dict[str, Any]],
    recent_ids: set[str],
) -> list[dict[str, Any]]:
    answers = {row.get("question_id"): row for row in student.get("answers") or []}
    tag_themes: dict[str, str] = {}
    for question in safe_pool:
        tags = question.get("tags") or {}
        tag_themes.setdefault(tags.get("primary_knowledge") or "", tags.get("curriculum_theme") or "")
    candidates: list[dict[str, Any]] = []
    for question in safe_pool:
        question_id = question["question_id"]
        if question_id in recent_ids:
            continue
        answer = answers.get(question_id)
        if answer and answer.get("attempted", True):
            full_score = float(answer.get("full_score") or 1)
            ratio = _clamp(float(answer.get("score") or 0) / full_score) if full_score else 0.0
            if ratio >= 0.999:
                answer_source = "consolidation"
            else:
                answer_source = "wrong_retry"
        else:
            answer_source = "unattempted"
        matches = [(_knowledge_match(question, weak, tag_themes), weak) for weak in weak_points]
        match, weak = max(matches, key=lambda pair: (pair[0], 1 - pair[1]["mastery_score"]), default=(0, None))
        if not weak or match <= 0:
            continue
        source = answer_source
        if answer_source == "unattempted":
            source = "weak_new" if match >= 0.6 else "transfer"
        elif match < 0.6:
            source = "transfer_retry"
        performance = performance_by_id.get(question_id) or {}
        score, components = _score_candidate(question, weak, match, performance)
        if answer_source == "consolidation":
            score = round(max(0.0, score - 0.12), 4)
        tags = question.get("tags") or {}
        candidates.append(
            {
                "question_id": question_id,
                "mother_question_id": question_id,
                "artifact_role": "mother_question",
                "is_generated": False,
                "student_visible": False,
                "generation_required": True,
                "display_id": question.get("display_id") or question_id,
                "source_exam": question.get("source_exam") or "",
                "question_number": question.get("question_number"),
                "question_type": question.get("question_type") or "",
                "source": source,
                "target_knowledge": weak["tag_name"],
                "target_mastery": weak["mastery_score"],
                "target_status": weak["status"],
                "primary_knowledge": tags.get("primary_knowledge") or "",
                "difficulty": tags.get("difficulty") or 3,
                "cohort": {
                    "correct": performance.get("correct", 0),
                    "wrong": performance.get("wrong", 0),
                    "omitted": performance.get("omitted", 0),
                    "correct_rate": performance.get("correct_rate"),
                },
                "tag_reliability": components["tag_reliability"],
                "score": score,
                "score_components": components,
                "stem_markdown": question.get("stem_markdown") or "",
                "stem_html": question.get("stem_html") or "",
                "answer": str(question.get("answer") or ""),
                "solution_markdown": question.get("solution_markdown") or "",
                "solution_html": question.get("solution_html") or "",
                "options": question.get("options") or {},
                "assets": question.get("assets") or [],
                "preview_url": f"../questions/{question_id}/preview.html",
                "stem_fingerprint": hashlib.sha256(_normalise_stem(question.get("stem_markdown") or "").encode("utf-8")).hexdigest()[:16],
            }
        )
    return sorted(candidates, key=lambda row: (-row["score"], row["display_id"]))


def _adjusted_score(candidate: dict[str, Any], selected: list[dict[str, Any]]) -> float:
    counts = Counter(row["question_type"] for row in selected)
    covered = {row["target_knowledge"] for row in selected}
    quota_bonus = 0.035 if counts[candidate["question_type"]] < TYPE_QUOTA.get(candidate["question_type"], 0) else -0.025
    coverage_bonus = 0.045 if candidate["target_knowledge"] not in covered else 0.0
    return candidate["score"] + quota_bonus + coverage_bonus


def _pick_balanced(
    candidates: list[dict[str, Any]],
    count: int,
    selected: list[dict[str, Any]],
    weak_by_tag: dict[str, dict[str, Any]],
) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate["target_knowledge"]].append(candidate)
    tags = sorted(groups, key=lambda tag: (weak_by_tag.get(tag, {}).get("mastery_score", 1), tag))
    schedule = [tag for tag in tags for _ in range(2 if weak_by_tag.get(tag, {}).get("mastery_score", 1) < 0.45 else 1)]
    selected_ids = {row["question_id"] for row in selected}
    while count > 0 and schedule:
        progress = False
        for tag in schedule:
            available = [row for row in groups[tag] if row["question_id"] not in selected_ids]
            if not available:
                continue
            choice = max(available, key=lambda row: (_adjusted_score(row, selected), row["display_id"]))
            selected.append(choice)
            selected_ids.add(choice["question_id"])
            count -= 1
            progress = True
            if count == 0:
                break
        if not progress:
            break


def _reason(candidate: dict[str, Any]) -> str:
    source_text = {
        "wrong_retry": "原错题重练",
        "weak_new": "薄弱点新题",
        "transfer": "关联迁移题",
        "transfer_retry": "关联错题重练",
        "consolidation": "同知识点巩固",
    }.get(candidate["source"], "巩固题")
    cohort = candidate.get("cohort") or {}
    if cohort.get("correct_rate") is None:
        cohort_text = "暂无班级正确率"
    else:
        cohort_text = f"班级答对 {cohort.get('correct', 0)} 人、答错 {cohort.get('wrong', 0)} 人"
    return (
        f"{source_text}；针对“{candidate['target_knowledge']}”（当前掌握度 {candidate['target_mastery']:.0%}），"
        f"难度 {candidate['difficulty']}，{cohort_text}。"
    )


def recommend_student(
    student: dict[str, Any],
    mastery_rows: list[dict[str, Any]],
    safe_pool: list[dict[str, Any]],
    performance_by_id: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    top_n: int = 5,
    seed: int = 20260713,
) -> dict[str, Any]:
    student_mastery = [row for row in mastery_rows if row["student_id"] == student["student_id"]]
    weak_points = sorted(
        [row for row in mastery_rows if row["student_id"] == student["student_id"] and row["mastery_score"] < 0.8],
        key=lambda row: (row["mastery_score"], row["tag_name"]),
    )
    target_points = list(weak_points)
    if len(target_points) < 2:
        existing = {row["tag_name"] for row in target_points}
        target_points.extend(
            row
            for row in sorted(student_mastery, key=lambda row: (row["mastery_score"], row["tag_name"]))
            if row["tag_name"] not in existing
        )
        target_points = target_points[:2]
    recent_ids = _recent_question_ids(history, student["student_id"])
    candidates = _candidate_rows(student, target_points, safe_pool, performance_by_id, recent_ids)
    random.Random(f"{seed}:{student['student_id']}").shuffle(candidates)
    candidates.sort(key=lambda row: (-row["score"], row["display_id"]))
    weak_by_tag = {row["tag_name"]: row for row in target_points}
    selected: list[dict[str, Any]] = []

    _pick_balanced([row for row in candidates if row["source"] == "wrong_retry"], min(1, top_n), selected, weak_by_tag)
    _pick_balanced([row for row in candidates if row["source"] == "weak_new"], min(3, top_n - len(selected)), selected, weak_by_tag)
    _pick_balanced([row for row in candidates if row["source"] == "transfer"], min(1, top_n - len(selected)), selected, weak_by_tag)
    for source_order in (("weak_new",), ("wrong_retry",), ("transfer", "transfer_retry"), ("consolidation",)):
        if len(selected) >= top_n:
            break
        _pick_balanced(
            [row for row in candidates if row["source"] in source_order],
            top_n - len(selected),
            selected,
            weak_by_tag,
        )

    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
        row["reason"] = _reason(row)
    selected_ids = {row["question_id"] for row in selected}
    alternates = [row for row in candidates if row["question_id"] not in selected_ids][:20]
    for row in alternates:
        row["reason"] = _reason(row)
    coverage = {row["target_knowledge"] for row in selected}
    available_weak_tags = {row["target_knowledge"] for row in candidates}
    gaps: list[str] = []
    if len(selected) < top_n:
        gaps.append(f"安全题池只能提供 {len(selected)} / {top_n} 题")
    if len(available_weak_tags) >= 2 and len(coverage) < 2:
        gaps.append("未能覆盖至少两个薄弱知识点")
    source_counts = Counter(row["source"] for row in selected)
    if source_counts["weak_new"] < min(3, top_n):
        gaps.append("该学生几乎做完现有题库，薄弱点新题不足，已按顺序用错题重练或迁移题补位")
    if source_counts["consolidation"]:
        gaps.append("安全错题和未作答题不足，末位使用已做对的同知识点题进行间隔巩固；可再用变式题补充新题")

    return {
        "student_id": student["student_id"],
        "name": student.get("name") or student["student_id"],
        "class": student.get("class") or "",
        "simulated": bool(student.get("simulated")),
        "weak_points": weak_points,
        "recommendations": selected,
        "alternates": alternates,
        "coverage_gaps": gaps,
        "source_counts": dict(source_counts),
    }


def _records_path(bank: Path) -> Path:
    real = bank / "student" / "student_exam_records.jsonl"
    simulated = bank / "student" / "simulated_student_records.jsonl"
    if real.exists():
        return real
    if simulated.exists():
        return simulated
    raise SystemExit(f"Student records not found: {real} or {simulated}")


def generate_recommendations(
    bank: Path,
    student_query: str | None = None,
    top_n: int = 5,
    seed: int = 20260713,
    write: bool = True,
) -> dict[str, Any]:
    bank = bank.resolve()
    tags_path = bank / "tags" / "all_question_tags.json"
    performance_path = bank / "student" / "question_performance.json"
    if not tags_path.exists():
        raise SystemExit(f"Tagged bank not found: {tags_path}")
    if not performance_path.exists():
        raise SystemExit(f"Question performance not found: {performance_path}")
    records_path = _records_path(bank)
    questions = _read_json(tags_path)
    students = _read_jsonl(records_path)
    if student_query:
        needle = student_query.casefold()
        students = [
            student
            for student in students
            if needle in {str(student.get("student_id", "")).casefold(), str(student.get("name", "")).casefold()}
        ]
        if not students:
            raise SystemExit(f"Student not found: {student_query}")
    performance = _read_json(performance_path)
    performance_by_id = {row["question_id"]: row for row in performance.get("questions") or []}
    safe_pool, excluded = build_safe_pool(questions, bank)
    mastery_rows = build_mastery_rows(students, questions)
    history_path = bank / "student" / "recommendation_history.jsonl"
    history = _read_jsonl(history_path) if history_path.exists() else []
    student_payloads = [
        recommend_student(student, mastery_rows, safe_pool, performance_by_id, history, top_n, seed)
        for student in students
    ]
    tag_bytes = tags_path.read_bytes()
    record_bytes = records_path.read_bytes()
    payload = {
        "schema_version": "structured-recommendations-v1",
        "artifact_role": "mother_question_recommendation_plan",
        "student_visible": False,
        "requires_new_question_generation": True,
        "delivery_warning": "Source-bank questions are teacher-only mother questions and must not be delivered to students.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "top_n": top_n,
        "scenario": "post_exam",
        "data_source": str(records_path),
        "simulated": all(bool(student.get("simulated")) for student in students),
        "bank_signature": hashlib.sha256(tag_bytes + record_bytes).hexdigest(),
        "quality_gate": {
            "complete_question_required": True,
            "answer_required": True,
            "solution_required": True,
            "minimum_overall_confidence": MIN_OVERALL_CONFIDENCE,
            "minimum_primary_confidence": MIN_PRIMARY_CONFIDENCE,
            "minimum_classification_margin": MIN_CLASSIFICATION_MARGIN,
            "minimum_structure_confidence": MIN_STRUCTURE_CONFIDENCE,
        },
        "bank_question_count": len(questions),
        "safe_pool_count": len(safe_pool),
        "excluded_count": len(excluded),
        "excluded_reasons": dict(Counter(reason for reasons in excluded.values() for reason in reasons)),
        "student_count": len(student_payloads),
        "students": student_payloads,
    }
    if write:
        student_dir = bank / "student"
        student_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(student_dir / "student_mastery.jsonl", mastery_rows)
        (student_dir / "recommendations.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        history_path.touch(exist_ok=True)
    return payload


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Select safe teacher-only mother questions for later new-question generation."
    )
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--student", help="Student name or id; omit to process every student")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.top_n < 1:
        raise SystemExit("--top-n must be positive")
    payload = generate_recommendations(
        args.structured_dir,
        student_query=args.student,
        top_n=args.top_n,
        seed=args.seed,
        write=not args.no_write,
    )
    summary = {
        "schema_version": payload["schema_version"],
        "bank_question_count": payload["bank_question_count"],
        "safe_pool_count": payload["safe_pool_count"],
        "excluded_count": payload["excluded_count"],
        "student_count": payload["student_count"],
        "students_with_full_set": sum(len(row["recommendations"]) == args.top_n for row in payload["students"]),
        "students_with_coverage_gaps": sum(bool(row["coverage_gaps"]) for row in payload["students"]),
        "recommendations": str(args.structured_dir.resolve() / "student" / "recommendations.json"),
        "mastery": str(args.structured_dir.resolve() / "student" / "student_mastery.jsonl"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
