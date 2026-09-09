#!/usr/bin/env python3
"""Tag an exam-ocr structured question bank and build review artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from build_review_workbench import build_workbench, refresh_class_paper, refresh_recommendations
from generate_student_performance import generate_performance


BENIGN_QUALITY_FLAGS = {"contains_images", "answer_math_display_repaired"}
BLOCKING_TAG_FLAGS = {"missing_image_asset", "answer_contamination", "markdown_math_loss"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def load_tagger(tagger_dir: Path):
    tagging_path = tagger_dir / "tagging.py"
    if not tagging_path.exists():
        raise SystemExit(f"tagging.py not found: {tagging_path}")
    sys.path.insert(0, str(tagger_dir))
    spec = importlib.util.spec_from_file_location("linked_smart_question_tagger", tagging_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def blocking_tag_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question_id": question.get("question_id"),
            "flags": sorted(set(question.get("quality_flags") or []) & BLOCKING_TAG_FLAGS),
        }
        for question in questions
        if set(question.get("quality_flags") or []) & BLOCKING_TAG_FLAGS
    ]


def question_content_signature(question: dict[str, Any]) -> str:
    payload = {
        "stem_markdown": question.get("stem_markdown") or "",
        "solution_markdown": question.get("solution_markdown") or "",
        "answer": question.get("answer") or "",
        "options": question.get("options") or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def preserve_teacher_reviews(
    fresh: list[dict[str, Any]],
    previous: list[dict[str, Any]],
) -> tuple[int, int]:
    previous_by_id = {item.get("question_id"): item for item in previous}
    preserved = 0
    invalidated = 0
    for question in fresh:
        old = previous_by_id.get(question.get("question_id"))
        old_tags = (old or {}).get("tags") or {}
        if not old_tags.get("tags_reviewed"):
            continue
        if question_content_signature(question) == question_content_signature(old):
            question["tags"] = old_tags
            preserved += 1
        else:
            tags = question.get("tags") or {}
            tags["needs_teacher_review"] = True
            reasons = list(tags.get("review_reasons") or [])
            if "source_changed_after_teacher_review" not in reasons:
                reasons.append("source_changed_after_teacher_review")
            tags["review_reasons"] = reasons
            invalidated += 1
    return preserved, invalidated


def enrich_tagged(
    question: dict[str, Any],
    tagged: dict[str, Any],
    threshold: float,
) -> dict[str, Any]:
    for field in (
        "schema_version",
        "display_id",
        "source_exam",
        "source_file",
        "solution_file",
        "stem_html",
        "solution_markdown",
        "solution_html",
        "options",
        "assets",
        "quality_flags",
        "provenance",
    ):
        tagged[field] = question.get(field)

    tags = tagged["tags"]
    confidence = float(tags.get("tags_confidence") or 0)
    quality_flags = set(question.get("quality_flags") or [])
    risky_flags = quality_flags - BENIGN_QUALITY_FLAGS
    review_reasons = []
    if confidence < threshold:
        review_reasons.append("confidence_below_threshold")
    if risky_flags:
        review_reasons.append("structure_quality_warning")
    if "contains_images" in quality_flags:
        review_reasons.append("contains_images")
    tags["needs_teacher_review"] = bool(review_reasons)
    tags["review_reasons"] = review_reasons
    if not tags.get("tag_reasoning"):
        tags["tag_reasoning"] = (
            f"规则分类为 {tags.get('curriculum_theme')} / {tags.get('knowledge_unit')}；"
            f"主知识点为 {tags.get('primary_knowledge')}。"
        )
    return tagged


def write_summary(path: Path, tagged: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    themes = Counter(item["tags"].get("curriculum_theme") or "未分类" for item in tagged)
    review_count = sum(bool(item["tags"].get("needs_teacher_review")) for item in tagged)
    confidences = [float(item["tags"].get("tags_confidence") or 0) for item in tagged]
    confidence_bands = Counter(
        "low_<0.65" if value < 0.65 else "medium_0.65-0.79" if value < 0.8 else "high_>=0.80"
        for value in confidences
    )
    review_reasons = Counter()
    for item in tagged:
        confidence = float(item["tags"].get("tags_confidence") or 0)
        flags = set(item.get("quality_flags") or [])
        if confidence < threshold:
            review_reasons["confidence_below_threshold"] += 1
        if flags - BENIGN_QUALITY_FLAGS:
            review_reasons["structure_quality_warning"] += 1
        if "contains_images" in flags:
            review_reasons["contains_images"] += 1
    payload = {
        "questions": len(tagged),
        "teacher_review_candidates": review_count,
        "confidence_threshold": threshold,
        "confidence_min": min(confidences) if confidences else None,
        "confidence_max": max(confidences) if confidences else None,
        "confidence_average": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "confidence_bands": dict(sorted(confidence_bands.items())),
        "review_reasons": dict(sorted(review_reasons.items())),
        "curriculum_themes": dict(sorted(themes.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tag a structured question bank and create indices, review CSV, and audit artifacts."
    )
    parser.add_argument("structured_dir", type=Path, help="Bank directory containing questions.jsonl")
    parser.add_argument(
        "--tagger-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory containing tagging.py and audit_tags.py",
    )
    parser.add_argument("--threshold", type=float, default=0.8, help="Teacher-review confidence threshold")
    parser.add_argument("--no-audit", action="store_true", help="Skip audit_tags.py")
    parser.add_argument("--reset-reviewed", action="store_true", help="Discard previously teacher-reviewed tags")
    parser.add_argument("--simulate-students", type=int, default=0, help="Generate deterministic simulated student records")
    parser.add_argument("--no-recommendations", action="store_true", help="Skip personalized recommendation refresh")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    structured_dir = args.structured_dir.resolve()
    tagger_dir = args.tagger_dir.resolve()
    questions_path = structured_dir / "questions.jsonl"
    structure_report_path = structured_dir / "structure_report.json"
    if not questions_path.exists():
        raise SystemExit(f"questions.jsonl not found: {questions_path}")
    if structure_report_path.exists():
        structure_report = json.loads(structure_report_path.read_text(encoding="utf-8"))
        if structure_report.get("status") == "fail":
            raise SystemExit("Structure report is fail; fix structure before tagging.")

    questions = read_jsonl(questions_path)
    blocked = blocking_tag_questions(questions)
    if blocked:
        preview = ", ".join(f"{item['question_id']}({'+'.join(item['flags'])})" for item in blocked[:8])
        raise SystemExit(f"Pre-tag quality gate failed for {len(blocked)} question(s): {preview}")

    existing_tags_path = structured_dir / "tags" / "all_question_tags.json"
    previous = []
    if existing_tags_path.exists() and not args.reset_reviewed:
        previous = json.loads(existing_tags_path.read_text(encoding="utf-8"))
    tagger = load_tagger(tagger_dir)
    tagged = [enrich_tagged(question, tagger.tag_question(question), args.threshold) for question in questions]
    preserved_reviews, invalidated_reviews = preserve_teacher_reviews(tagged, previous)
    tags_dir = structured_dir / "tags"
    review_dir = structured_dir / "review"
    index_dir = structured_dir / "index"
    tagger._write_json(tags_dir / "all_question_tags.json", tagged)
    tagger._write_jsonl(tags_dir / "question_tags.jsonl", tagger._flat_question_tags(tagged))
    tagger._write_review_csv(review_dir / "all_tag_review.csv", tagged)
    for name, value in tagger.build_indices(tagged).items():
        tagger._write_json(index_dir / f"{name}.json", value)
    summary = write_summary(structured_dir / "tagging_summary.json", tagged, args.threshold)

    if not args.no_audit:
        audit_script = tagger_dir / "audit_tags.py"
        if not audit_script.exists():
            raise SystemExit(f"audit_tags.py not found: {audit_script}")
        subprocess.run(
            [
                sys.executable,
                str(audit_script),
                "--workspace",
                str(structured_dir),
                "--tags",
                str(tags_dir / "all_question_tags.json"),
                "--output",
                str(structured_dir / "audit"),
                "--threshold",
                str(args.threshold),
            ],
            check=True,
        )

    simulated_performance = None
    if args.simulate_students:
        if args.simulate_students < 1:
            raise SystemExit("--simulate-students must be positive")
        simulated_performance = generate_performance(structured_dir, args.simulate_students)
    recommendations = None if args.no_recommendations else refresh_recommendations(structured_dir)
    class_paper = None if args.no_recommendations else refresh_class_paper(structured_dir)
    workbench = build_workbench(structured_dir)
    summary["teacher_review_candidates"] = workbench["review_queue"]
    summary["review_queue_sources"] = workbench["review_queue_sources"]
    (structured_dir / "tagging_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                **summary,
                **workbench,
                "structured_dir": str(structured_dir),
                "preserved_teacher_reviews": preserved_reviews,
                "invalidated_teacher_reviews": invalidated_reviews,
                "simulated_performance": simulated_performance,
                "recommendation_refresh": recommendations,
                "class_paper_refresh": class_paper,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
