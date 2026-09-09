#!/usr/bin/env python3
"""Create portable calibration review suggestions from audit packets.

The script intentionally contains no question-id corrections.  Optional
teacher/model overrides are supplied as an external JSON or JSONL artifact so
the Skill can be reused with another school or question bank.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from gaokao_taxonomy import KNOWLEDGE_TAXONOMY


ALLOWED_OVERRIDE_FIELDS = {
    "primary_knowledge",
    "secondary_knowledge",
    "ability_tags",
    "difficulty",
    "difficulty_basis",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create data-driven calibration suggestions from high-priority audit packets."
    )
    parser.add_argument("--input", required=True, type=Path, help="Audit packet JSONL.")
    parser.add_argument(
        "--overrides",
        type=Path,
        help="Optional teacher/model JSON or JSONL keyed by question_id; never embedded in this Skill.",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output directory.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    packets = _read_jsonl(args.input.resolve())
    overrides = _read_overrides(args.overrides.resolve()) if args.overrides else {}
    suggestions = [_suggest(packet, overrides.get(str(packet.get("question_id") or ""))) for packet in packets]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "model_calibration_suggestions.jsonl", suggestions)
    _write_csv(output / "model_calibration_suggestions.csv", suggestions)
    summary = _summary(suggestions, overrides, output)
    (output / "model_calibration_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")
    return 0


def _read_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Overrides file not found: {path}")
    if path.suffix.lower() == ".jsonl":
        raw_rows = _read_jsonl(path)
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            raw_rows = [
                {"question_id": question_id, **(row if isinstance(row, dict) else {})}
                for question_id, row in value.items()
            ]
        elif isinstance(value, list):
            raw_rows = value
        else:
            raise SystemExit("Overrides must be a JSON object, JSON array, or JSONL rows")
    overrides: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw_rows, 1):
        if not isinstance(row, dict):
            raise SystemExit(f"Override row {index} must be an object")
        question_id = str(row.get("question_id") or "").strip()
        if not question_id:
            raise SystemExit(f"Override row {index} is missing question_id")
        candidate = row.get("suggested_tags") if isinstance(row.get("suggested_tags"), dict) else row
        fields = {key: candidate[key] for key in ALLOWED_OVERRIDE_FIELDS if key in candidate}
        _validate_override(question_id, fields)
        overrides[question_id] = {**fields, "rationale": str(row.get("rationale") or row.get("calibration_rationale") or "外部审核给出修正").strip()}
    return overrides


def _validate_override(question_id: str, override: dict[str, Any]) -> None:
    primary = override.get("primary_knowledge")
    if primary is not None and primary not in KNOWLEDGE_TAXONOMY:
        raise SystemExit(f"{question_id}: primary_knowledge is outside the canonical taxonomy")
    for field in ("secondary_knowledge", "ability_tags"):
        if field in override and not isinstance(override[field], list):
            raise SystemExit(f"{question_id}: {field} must be a list")
    if "difficulty" in override:
        try:
            difficulty = int(override["difficulty"])
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"{question_id}: difficulty must be an integer from 1 to 5") from exc
        if difficulty not in range(1, 6):
            raise SystemExit(f"{question_id}: difficulty must be an integer from 1 to 5")
        override["difficulty"] = difficulty


def _suggest(packet: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    question_id = str(packet.get("question_id") or "").strip()
    if not question_id:
        raise SystemExit("Audit packet is missing question_id")
    current = packet.get("current_tags")
    if not isinstance(current, dict):
        raise SystemExit(f"{question_id}: audit packet is missing current_tags")
    suggested = dict(current)
    changed_fields: list[str] = []
    if override:
        for field in ALLOWED_OVERRIDE_FIELDS:
            if field in override and override[field] != current.get(field):
                suggested[field] = override[field]
                changed_fields.append(field)
    action = "change" if changed_fields else "review"
    return {
        "question_id": question_id,
        "action": action,
        "changed_fields": changed_fields,
        "audit_flags": packet.get("audit_flags", []),
        "current_tags": current,
        "suggested_tags": suggested,
        "calibration_rationale": (
            str((override or {}).get("rationale") or "").strip()
            if changed_fields
            else "没有外部教师或模型修正依据；保留原标签并进入人工复核，不自动提高置信度。"
        ),
        "needs_teacher_review": True,
        "suggestion_source": "external_override" if changed_fields else "audit_triage_only",
    }


def _summary(suggestions: list[dict[str, Any]], overrides: dict[str, dict[str, Any]], output: Path) -> dict[str, Any]:
    changes = [item for item in suggestions if item["action"] == "change"]
    reviews = [item for item in suggestions if item["action"] == "review"]
    used_ids = {item["question_id"] for item in suggestions if item["action"] == "change"}
    changed_fields: dict[str, int] = {}
    for item in changes:
        for field in item["changed_fields"]:
            changed_fields[field] = changed_fields.get(field, 0) + 1
    return {
        "suggestions": len(suggestions),
        "changes": len(changes),
        "review_only": len(reviews),
        "changed_fields": changed_fields,
        "unused_override_ids": sorted(set(overrides) - used_ids),
        "suggestions_jsonl": str(output / "model_calibration_suggestions.jsonl"),
        "suggestions_csv": str(output / "model_calibration_suggestions.csv"),
        "summary_md": str(output / "model_calibration_summary.md"),
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Calibration Suggestions",
        "",
        f"- Audit packets: {summary['suggestions']}",
        f"- External changes: {summary['changes']}",
        f"- Waiting for review: {summary['review_only']}",
        "",
        "No question-specific correction is embedded in the Skill. Changes come only from --overrides.",
    ]
    if summary["unused_override_ids"]:
        lines.extend(["", "## Unused override IDs", "", *[f"- {value}" for value in summary["unused_override_ids"]]])
    return "\n".join(lines) + "\n"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"JSONL file not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(value, dict):
                raise SystemExit(f"{path}:{line_number}: row must be an object")
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["question_id", "action", "changed_fields", "current_primary", "suggested_primary", "calibration_rationale", "suggestion_source"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "question_id": row["question_id"],
                    "action": row["action"],
                    "changed_fields": "；".join(row["changed_fields"]),
                    "current_primary": row["current_tags"].get("primary_knowledge", ""),
                    "suggested_primary": row["suggested_tags"].get("primary_knowledge", ""),
                    "calibration_rationale": row["calibration_rationale"],
                    "suggestion_source": row["suggestion_source"],
                }
            )


if __name__ == "__main__":
    raise SystemExit(main())
