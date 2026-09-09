#!/usr/bin/env python3
"""Deterministic structural gate for high-school math variant sets."""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


QUESTION_TYPES = {"single_choice", "multiple_choice", "fill_blank", "solution"}
STRUCTURAL_DIMENSIONS = {
    "question_angle",
    "condition_organization",
    "representation",
    "context",
    "reasoning_path",
}
IMAGE_REFERENCE_RE = re.compile(r"!\[[^]]*]\(([^)]+)\)")


def _normalise(value: Any, *, remove_numbers: bool = False) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"^\s*#{1,6}\s*", "", text)
    text = re.sub(r"\\(?:left|right|displaystyle|quad|qquad)\b", "", text)
    if remove_numbers:
        text = re.sub(r"\d+(?:\.\d+)?", "#", text)
    return re.sub(r"[^\w\u4e00-\u9fff#]+", "", text)


def _choice_labels(value: Any) -> tuple[str, ...]:
    found = set(re.findall(r"[A-D]", str(value or "").upper()))
    return tuple(label for label in "ABCD" if label in found)


def _shape_errors(candidate: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    question_type = str(candidate.get("question_type") or "")
    stem = str(candidate.get("stem_markdown") or "")
    options = candidate.get("options")
    if question_type not in QUESTION_TYPES:
        return [f"{label}: unsupported question_type"]
    if question_type in {"single_choice", "multiple_choice"}:
        if not isinstance(options, dict) or {str(key).upper() for key in options} != set("ABCD"):
            errors.append(f"{label}: choice question must contain A/B/C/D options")
        elif any(not str(value).strip() for value in options.values()):
            errors.append(f"{label}: choice question contains an empty option")
        elif len({_normalise(value) for value in options.values()}) != 4:
            errors.append(f"{label}: choice question contains duplicate options")
        labels = _choice_labels(candidate.get("answer"))
        if question_type == "single_choice" and len(labels) != 1:
            errors.append(f"{label}: single-choice answer must be exactly one letter")
        if question_type == "multiple_choice" and len(labels) < 2:
            errors.append(f"{label}: multiple-choice answer must contain at least two letters")
    elif isinstance(options, dict) and any(str(value).strip() for value in options.values()):
        errors.append(f"{label}: non-choice question must not contain choice options")
    if question_type == "fill_blank" and not re.search(r"_{2,}|____|填空|空格", stem):
        errors.append(f"{label}: fill-blank stem must contain an explicit blank")
    if question_type == "solution" and re.search(r"(?m)^\s*[A-D]\s*[.．、:：]", stem):
        errors.append(f"{label}: solution question must not contain A/B/C/D options")
    return errors


def validate_variant_set(payload: dict[str, Any], similarity_threshold: float = 0.93) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    original = payload.get("original")
    candidates = payload.get("candidates")
    target = payload.get("target") or {}
    if not isinstance(original, dict) or not str(original.get("stem_markdown") or "").strip():
        errors.append("original: stem_markdown is required")
        original = {}
    if not isinstance(candidates, list) or len(candidates) != 3:
        return errors + ["candidates must contain exactly three variants"]
    target_type = str(target.get("question_type") or original.get("question_type") or "")
    target_level = target.get("difficulty_level")
    original_text = _normalise(original.get("stem_markdown"))
    original_structure = _normalise(original.get("stem_markdown"), remove_numbers=True)
    candidate_ids: set[str] = set()
    accepted_texts: list[tuple[str, str]] = []
    for index, candidate in enumerate(candidates, 1):
        label = f"candidate[{index}]"
        if not isinstance(candidate, dict):
            errors.append(f"{label}: must be an object")
            continue
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            errors.append(f"{label}: candidate_id is required")
        elif candidate_id in candidate_ids:
            errors.append(f"{label}: duplicate candidate_id")
        candidate_ids.add(candidate_id)
        for field in ("stem_markdown", "answer", "solution_markdown", "variation_note", "relative_difficulty"):
            if not str(candidate.get(field) or "").strip():
                errors.append(f"{label}: {field} is required")
        if target_type and candidate.get("question_type") != target_type:
            errors.append(f"{label}: question_type does not match the requested target")
        if target_level is not None:
            try:
                level = int(candidate.get("difficulty_level"))
                expected_level = int(target_level)
            except (TypeError, ValueError):
                errors.append(f"{label}: difficulty_level must be an exact integer")
            else:
                if level != expected_level:
                    errors.append(f"{label}: difficulty_level must equal the requested exact level {expected_level}")
        errors.extend(_shape_errors(candidate, label))
        dimensions = candidate.get("changed_dimensions")
        dimension_values = [str(value) for value in dimensions] if isinstance(dimensions, list) else []
        if len(set(dimension_values)) < 2 or not STRUCTURAL_DIMENSIONS.intersection(dimension_values):
            errors.append(f"{label}: changed_dimensions needs two changes including one structural change")
        verification = candidate.get("independent_verification") or {}
        if verification.get("status") != "passed" or verification.get("answer_matches") is not True:
            errors.append(f"{label}: independent verification must pass and match the stated answer")
        stem = str(candidate.get("stem_markdown") or "")
        if IMAGE_REFERENCE_RE.search(stem) or IMAGE_REFERENCE_RE.search(str(candidate.get("solution_markdown") or "")):
            errors.append(f"{label}: unresolved Markdown image references are not allowed in a self-contained variant")
        candidate_text = _normalise(stem)
        candidate_structure = _normalise(stem, remove_numbers=True)
        if original_text and candidate_text:
            ratio = SequenceMatcher(None, original_text, candidate_text).ratio()
            if ratio >= similarity_threshold:
                errors.append(f"{label}: stem is too close to the original ({ratio:.3f})")
            if original_structure and candidate_structure == original_structure:
                errors.append(f"{label}: variant changes only numbers or presentation")
        for previous_label, previous_text in accepted_texts:
            ratio = SequenceMatcher(None, previous_text, candidate_text).ratio() if candidate_text else 0.0
            if ratio >= similarity_threshold:
                errors.append(f"{label}: stem is too close to {previous_label} ({ratio:.3f})")
        accepted_texts.append((label, candidate_text))
    recommendation = payload.get("recommendation") or {}
    if str(recommendation.get("candidate_id") or "") not in candidate_ids:
        errors.append("recommendation.candidate_id must identify one of the three variants")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a three-candidate math variant JSON file.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--similarity-threshold", type=float, default=0.93)
    args = parser.parse_args()
    if not 0.5 <= args.similarity_threshold <= 1.0:
        raise SystemExit("--similarity-threshold must be between 0.5 and 1.0")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    errors = validate_variant_set(payload, args.similarity_threshold)
    if errors:
        raise SystemExit("Variant validation failed:\n- " + "\n- ".join(errors))
    print(json.dumps({"status": "passed", "candidate_count": 3}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
