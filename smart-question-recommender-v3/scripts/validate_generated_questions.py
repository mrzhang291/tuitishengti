#!/usr/bin/env python3
"""Reject copied, incomplete, unverified, or unapproved generated questions."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


STRUCTURAL_DIMENSIONS = {
    "question_angle",
    "condition_organization",
    "representation",
    "context",
    "reasoning_path",
}
CHOICE_TYPES = {"single_choice", "multiple_choice"}
QUESTION_TYPES = {"single_choice", "multiple_choice", "fill_blank", "solution"}
QUESTION_SET_SCHEMA = "generated-question-set-v1"
CANDIDATE_POOL_SCHEMA = "generated-question-candidate-pool-v1"


def _normalise_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"^\s*#{1,6}\s*\d+[.、]?\s*", "", value)
    value = re.sub(r"!\[[^]]*]\([^)]*\)", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _choice_labels(value: Any) -> tuple[str, ...]:
    found = set(re.findall(r"[A-D]", str(value or "").upper()))
    return tuple(label for label in "ABCD" if label in found)


def _load_bank_questions(bank: Path) -> list[dict[str, Any]]:
    path = bank / "tags" / "all_question_tags.json"
    if not path.exists():
        raise SystemExit(f"Tagged bank not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"Tagged bank must contain a JSON list: {path}")
    return payload


def validate_generated_questions(
    bank_questions: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    require_approved: bool = False,
    similarity_threshold: float = 0.90,
) -> list[str]:
    """Return validation errors; an empty list means the hard gate passed."""
    errors: list[str] = []
    bank_ids = {str(row.get("question_id") or "") for row in bank_questions}
    schema_version = payload.get("schema_version")
    rows: list[dict[str, Any]] = []
    if schema_version == QUESTION_SET_SCHEMA:
        questions = payload.get("questions")
        if not isinstance(questions, list):
            return ["payload: questions must be a non-empty list"]
        if not questions:
            if payload.get("status") == "not_applicable_source_questions_are_delivered_directly":
                return []
            return ["payload: questions must be a non-empty list"]
        rows = questions
    elif schema_version == CANDIDATE_POOL_SCHEMA:
        slots = payload.get("slots")
        if not isinstance(slots, list) or not slots:
            return ["payload: slots must be a non-empty list"]
        declared_count = payload.get("candidate_count_per_slot")
        if not isinstance(declared_count, int) or declared_count < 3:
            errors.append("payload: candidate_count_per_slot must be an integer of at least 3")
        maximum_references = payload.get("maximum_reference_questions")
        if maximum_references is not None and (
            not isinstance(maximum_references, int) or maximum_references < 3
        ):
            errors.append("payload: maximum_reference_questions must be an integer of at least 3")
        seen_slot_ids: set[str] = set()
        for slot_index, slot in enumerate(slots):
            slot_label = str(slot.get("slot_id") or f"slots[{slot_index}]")
            slot_id = str(slot.get("slot_id") or "").strip()
            if not slot_id:
                errors.append(f"{slot_label}: missing slot_id")
            elif slot_id in seen_slot_ids:
                errors.append(f"{slot_label}: duplicate slot_id")
            seen_slot_ids.add(slot_id)
            slot_mother_id = str(slot.get("mother_question_id") or "").strip()
            if slot.get("scope") == "student_practice":
                if not str(slot.get("student_id") or "").strip():
                    errors.append(f"{slot_label}: student_practice slot is missing student_id")
                raw_slot_reference_ids = slot.get("reference_question_ids") or []
                slot_reference_ids = (
                    [str(value).strip() for value in raw_slot_reference_ids if str(value).strip()]
                    if isinstance(raw_slot_reference_ids, list)
                    else []
                )
                if len(set(slot_reference_ids)) < 3:
                    errors.append(f"{slot_label}: student_practice slot must reference at least three source questions")
                elif any(str(question_id) not in bank_ids for question_id in slot_reference_ids):
                    errors.append(f"{slot_label}: reference_question_ids contains an unknown source question")
                if isinstance(maximum_references, int) and len(set(slot_reference_ids)) > maximum_references:
                    errors.append(
                        f"{slot_label}: reference_question_ids exceeds the declared "
                        f"maximum of {maximum_references} source questions"
                    )
            candidates = slot.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 3:
                errors.append(f"{slot_label}: candidates must contain at least three verified new questions")
                continue
            if isinstance(declared_count, int) and len(candidates) != declared_count:
                errors.append(
                    f"{slot_label}: candidate count {len(candidates)} does not match "
                    f"candidate_count_per_slot {declared_count}"
                )
            for candidate_index, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    errors.append(f"{slot_label}.candidates[{candidate_index}]: candidate must be an object")
                    continue
                candidate_mother_id = str((candidate.get("generation") or {}).get("mother_question_id") or "").strip()
                if slot_mother_id and candidate_mother_id != slot_mother_id:
                    errors.append(
                        f"{slot_label}.candidates[{candidate_index}]: generation.mother_question_id "
                        "does not match the slot mother_question_id"
                    )
                if slot.get("question_type") and candidate.get("question_type") != slot.get("question_type"):
                    errors.append(
                        f"{slot_label}.candidates[{candidate_index}]: question_type does not match the slot"
                    )
                if slot.get("primary_knowledge") and candidate.get("primary_knowledge") != slot.get("primary_knowledge"):
                    errors.append(
                        f"{slot_label}.candidates[{candidate_index}]: primary_knowledge does not match the slot"
                    )
                rows.append(candidate)
        if not rows:
            return errors + ["payload: candidate pool contains no questions"]
    else:
        return [
            "payload: schema_version must be generated-question-set-v1 or "
            "generated-question-candidate-pool-v1"
        ]

    bank_stems = [
        (str(row.get("question_id") or "unknown"), _normalise_text(row.get("stem_markdown") or row.get("stem_html") or ""))
        for row in bank_questions
    ]
    seen_generated_ids: set[str] = set()

    for index, row in enumerate(rows, 1):
        label = str(row.get("question_id") or f"questions[{index - 1}]")
        question_id = str(row.get("question_id") or "").strip()
        if not question_id:
            errors.append(f"{label}: missing question_id")
        elif question_id in bank_ids:
            errors.append(f"{label}: question_id reuses a source-bank id")
        elif question_id in seen_generated_ids:
            errors.append(f"{label}: duplicate generated question_id")
        seen_generated_ids.add(question_id)

        if row.get("is_generated") is not True:
            errors.append(f"{label}: is_generated must be true")
        if row.get("student_visible") is not True:
            errors.append(f"{label}: student_visible must be true for a deliverable candidate")

        stem = str(row.get("stem_markdown") or row.get("stem_html") or "").strip()
        answer = str(row.get("answer") or "").strip()
        solution = str(row.get("solution_markdown") or row.get("solution_html") or "").strip()
        if not stem:
            errors.append(f"{label}: missing generated stem")
        if not answer:
            errors.append(f"{label}: missing generated answer")
        if not solution:
            errors.append(f"{label}: missing generated solution")

        question_type = str(row.get("question_type") or "")
        if question_type not in QUESTION_TYPES:
            errors.append(f"{label}: unsupported or missing question_type")
        if question_type in CHOICE_TYPES:
            options = row.get("options")
            if not isinstance(options, dict) or {str(key).upper() for key in options} != set("ABCD"):
                errors.append(f"{label}: choice question must contain A/B/C/D options")
            elif any(not str(value).strip() for value in options.values()):
                errors.append(f"{label}: choice question contains an empty option")
            elif len({re.sub(r"\s+", "", str(value)) for value in options.values()}) != 4:
                errors.append(f"{label}: choice question contains duplicate options")
            labels = _choice_labels(answer)
            if question_type == "single_choice" and len(labels) != 1:
                errors.append(f"{label}: single_choice answer must contain exactly one option")
            if question_type == "multiple_choice" and len(labels) < 2:
                errors.append(f"{label}: multiple_choice answer must contain at least two options")
        elif question_type == "solution":
            options = row.get("options")
            option_labels = re.findall(r"(?m)^\s*[A-D]\s*[.．、:：]", stem)
            if (isinstance(options, dict) and any(str(value).strip() for value in options.values())) or len(option_labels) >= 2:
                errors.append(f"{label}: solution question must not contain A/B/C/D options")
        elif question_type == "fill_blank":
            options = row.get("options")
            if isinstance(options, dict) and any(str(value).strip() for value in options.values()):
                errors.append(f"{label}: fill_blank question must not contain choice options")
            if not re.search(r"_{2,}|____|填空|空格", stem):
                errors.append(f"{label}: fill_blank stem must contain an explicit answer blank")

        generation = row.get("generation") or {}
        mother_id = str(generation.get("mother_question_id") or "").strip()
        if not mother_id:
            errors.append(f"{label}: missing generation.mother_question_id")
        elif mother_id not in bank_ids:
            errors.append(f"{label}: mother_question_id is not present in the source bank")
        if not str(generation.get("strategy") or "").strip():
            errors.append(f"{label}: missing generation.strategy")
        raw_reference_ids = generation.get("reference_question_ids") or []
        reference_ids = (
            [str(value).strip() for value in raw_reference_ids if str(value).strip()]
            if isinstance(raw_reference_ids, list)
            else []
        )
        row_scope = str(row.get("scope") or payload.get("scope") or "")
        if row_scope in {"student_practice", "knowledge_practice"}:
            if len(set(reference_ids)) < 3 or len(reference_ids) != len(set(reference_ids)):
                errors.append(
                    f"{label}: personalized new question needs at least three source questions "
                    "as diagnostic evidence (not multi-question synthesis)"
                )
            elif any(str(question_id) not in bank_ids for question_id in reference_ids):
                errors.append(f"{label}: generation.reference_question_ids contains an unknown source question")
            strategy = str(generation.get("strategy") or "")
            if strategy not in {"multi_source_synthesis", "single_anchor_task_spec", "knowledge_teaching_chain_template"}:
                errors.append(
                    f"{label}: personalized generation.strategy must be single_anchor_task_spec "
                    "(or legacy multi_source_synthesis / knowledge_teaching_chain_template)"
                )
            if strategy == "single_anchor_task_spec":
                raw_evidence_ids = generation.get("diagnostic_evidence_question_ids") or []
                evidence_ids = (
                    [str(value).strip() for value in raw_evidence_ids if str(value).strip()]
                    if isinstance(raw_evidence_ids, list)
                    else []
                )
                evidence_count = generation.get("diagnostic_evidence_count")
                if evidence_ids != reference_ids or evidence_count != len(reference_ids):
                    errors.append(f"{label}: diagnostic evidence aliases must match reference provenance")
                structure_anchor = generation.get("structure_anchor") or {}
                anchor_kind = str(structure_anchor.get("kind") or "")
                anchor_id = str(structure_anchor.get("id") or "")
                fulltext_anchor_count = generation.get("fulltext_anchor_count")
                if anchor_kind == "mother_question":
                    if anchor_id not in reference_ids or fulltext_anchor_count != 1:
                        errors.append(f"{label}: mother-question anchor must be unique and belong to diagnostic evidence")
                elif anchor_kind == "skill_blueprint":
                    if not anchor_id or fulltext_anchor_count != 0:
                        errors.append(f"{label}: skill-blueprint anchor must have an id and no full-text source question")
                else:
                    errors.append(f"{label}: missing or invalid generation.structure_anchor")
                task_spec = generation.get("task_spec") or {}
                if task_spec.get("schema_version") != "generation-task-spec-v2":
                    errors.append(f"{label}: missing generation-task-spec-v2 snapshot")
                else:
                    task_target = task_spec.get("target") or {}
                    if task_target.get("question_type") != question_type:
                        errors.append(f"{label}: task_spec target question_type does not match the delivered question")
                    if task_target.get("knowledge") != row.get("primary_knowledge"):
                        errors.append(f"{label}: task_spec target knowledge does not match the delivered question")
                    task_anchor = task_spec.get("structure_anchor") or {}
                    if task_anchor != structure_anchor:
                        errors.append(f"{label}: task_spec structure_anchor does not match generation.structure_anchor")
                    contract = task_spec.get("generation_contract") or {}
                    expected_fulltext = 0 if anchor_kind == "skill_blueprint" else 1
                    if contract.get("fulltext_source_question_count") != expected_fulltext:
                        errors.append(f"{label}: task_spec full-text anchor boundary is inconsistent")
                    forbidden = set(contract.get("forbidden") or [])
                    required_forbidden = {
                        "merge_multiple_source_stems",
                        "copy_or_paraphrase_anchor",
                        "numbers_only_change",
                    }
                    if not required_forbidden.issubset(forbidden):
                        errors.append(f"{label}: task_spec is missing mandatory anti-copy constraints")
                fingerprint = generation.get("pedagogical_fingerprint")
                required_fingerprint_fields = {
                    "knowledge",
                    "question_type",
                    "target_level",
                    "method_family",
                    "function_family",
                    "task_intent",
                    "reasoning_pattern",
                    "option_pattern",
                    "complexity_signals",
                }
                if not isinstance(fingerprint, dict):
                    errors.append(f"{label}: missing generation.pedagogical_fingerprint")
                elif not required_fingerprint_fields.issubset(fingerprint):
                    errors.append(f"{label}: incomplete generation.pedagogical_fingerprint")
                if verification := row.get("verification") or {}:
                    if verification.get("difficulty_matches") is not True:
                        errors.append(f"{label}: verification.difficulty_matches must be true")
                    if verification.get("answer_solution_consistency") != "passed":
                        errors.append(f"{label}: verification.answer_solution_consistency must be passed")
                    if verification.get("estimated_level") not in {1, 2, 3, 4, 5}:
                        errors.append(f"{label}: verification.estimated_level must be an integer from 1 to 5")
        raw_dimensions = generation.get("changed_dimensions") or []
        dimensions = [str(value) for value in raw_dimensions if str(value)] if isinstance(raw_dimensions, list) else []
        if len(set(dimensions)) < 2:
            errors.append(f"{label}: changed_dimensions must contain at least two distinct changes")
        if not STRUCTURAL_DIMENSIONS.intersection(dimensions):
            errors.append(f"{label}: at least one changed dimension must be structural")
        if generation.get("novelty_review") != "passed":
            errors.append(f"{label}: generation.novelty_review must be passed")

        normalised = _normalise_text(stem)
        if normalised:
            closest_id = ""
            closest_ratio = 0.0
            for source_id, source_stem in bank_stems:
                if not source_stem:
                    continue
                ratio = 1.0 if normalised == source_stem else SequenceMatcher(None, normalised, source_stem).ratio()
                if ratio > closest_ratio:
                    closest_id, closest_ratio = source_id, ratio
            if closest_ratio >= similarity_threshold:
                errors.append(
                    f"{label}: stem is copied or too close to source {closest_id} "
                    f"(text similarity {closest_ratio:.3f} >= {similarity_threshold:.3f})"
                )

        verification = row.get("verification") or {}
        if verification.get("checked_from_stem_only") is not True:
            errors.append(f"{label}: independent verification must solve from the new stem only")
        if not str(verification.get("independent_answer") or "").strip():
            errors.append(f"{label}: missing verification.independent_answer")
        if verification.get("answer_matches") is not True:
            errors.append(f"{label}: independent answer does not match the stated answer")
        if verification.get("status") != "passed":
            errors.append(f"{label}: verification.status must be passed")

        if require_approved and (row.get("teacher_review") or {}).get("status") != "approved":
            errors.append(f"{label}: teacher_review.status must be approved")

    return errors


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Validate that delivered questions are new, independently verified, and teacher-approved."
    )
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument(
        "--input",
        type=Path,
        help="Generated-question JSON; defaults to <structured-dir>/generation/generated_questions.json",
    )
    parser.add_argument("--require-approved", action="store_true")
    parser.add_argument("--similarity-threshold", type=float, default=0.90)
    args = parser.parse_args()
    if not 0.50 <= args.similarity_threshold <= 1.0:
        raise SystemExit("--similarity-threshold must be between 0.50 and 1.0")

    bank = args.structured_dir.resolve()
    input_path = args.input or bank / "generation" / "generated_questions.json"
    if not input_path.is_absolute():
        input_path = (Path.cwd() / input_path).resolve()
    if not input_path.exists():
        raise SystemExit(f"Generated-question file not found: {input_path}")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    errors = validate_generated_questions(
        _load_bank_questions(bank),
        payload,
        require_approved=args.require_approved,
        similarity_threshold=args.similarity_threshold,
    )
    if errors:
        raise SystemExit("Generated-question validation failed:\n- " + "\n- ".join(errors[:50]))
    print(
        json.dumps(
            {
                "schema_version": payload.get("schema_version"),
                "question_count": (
                    len(payload.get("questions") or [])
                    if payload.get("schema_version") == QUESTION_SET_SCHEMA
                    else sum(len(slot.get("candidates") or []) for slot in payload.get("slots") or [])
                ),
                "copied_source_questions": 0,
                "independent_verification": "passed",
                "teacher_approval_required": args.require_approved,
                "status": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
