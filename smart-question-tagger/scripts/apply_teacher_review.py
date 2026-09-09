#!/usr/bin/env python3
"""Apply exported teacher review decisions to a structured question bank."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tagging
from build_review_workbench import ABILITY_TAGS, build_workbench, refresh_class_paper, refresh_recommendations, tag_signature
from gaokao_taxonomy import KNOWLEDGE_TAXONOMY, PRIMARY_KNOWLEDGE_META
from tag_structured_bank import write_summary


VALID_DECISIONS = {"approve", "change", "defer"}


def read_decisions(path: Path) -> dict[str, dict[str, Any]]:
    decisions = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            question_id = item.get("question_id")
            if not question_id:
                raise SystemExit(f"Missing question_id at {path}:{line_number}")
            if item.get("decision") not in VALID_DECISIONS:
                raise SystemExit(f"Invalid decision for {question_id}: {item.get('decision')}")
            decisions[question_id] = item
    return decisions


def _validated_teacher_tags(decision: dict[str, Any]) -> dict[str, Any]:
    teacher = decision.get("teacher_tags") or {}
    primary = teacher.get("primary_knowledge")
    if primary not in KNOWLEDGE_TAXONOMY:
        raise SystemExit(f"Unknown primary knowledge for {decision['question_id']}: {primary}")
    secondary = teacher.get("secondary_knowledge") or []
    abilities = teacher.get("ability_tags") or []
    invalid_abilities = sorted(set(abilities) - set(ABILITY_TAGS))
    if invalid_abilities:
        raise SystemExit(f"Unknown ability tags for {decision['question_id']}: {invalid_abilities}")
    try:
        difficulty = int(teacher.get("difficulty"))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Invalid difficulty for {decision['question_id']}") from exc
    if difficulty not in range(1, 6):
        raise SystemExit(f"Difficulty must be 1-5 for {decision['question_id']}")
    return {
        "primary_knowledge": primary,
        "secondary_knowledge": [str(value).strip() for value in secondary if str(value).strip()],
        "ability_tags": list(dict.fromkeys(abilities)),
        "difficulty": difficulty,
    }


def _append_generator(current: str) -> str:
    marker = "teacher_review_v1"
    if marker in current:
        return current
    return f"{current}+{marker}" if current else marker


def validate_decisions(
    tagged: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    allow_stale: bool,
) -> dict[str, dict[str, Any]]:
    by_id = {item.get("question_id"): item for item in tagged}
    unknown = sorted(set(decisions) - set(by_id))
    if unknown:
        raise SystemExit(f"Decision file contains unknown question IDs: {', '.join(unknown[:8])}")
    validated = {}
    for question_id, decision in decisions.items():
        if decision["decision"] == "defer":
            validated[question_id] = decision
            continue
        current_signature = tag_signature(by_id[question_id].get("tags") or {})
        if decision.get("source_signature") != current_signature and not allow_stale:
            raise SystemExit(
                f"Stale decision for {question_id}; tags changed after export. "
                "Reopen the workbench or pass --allow-stale after manual verification."
            )
        if decision["decision"] == "change":
            decision = dict(decision)
            decision["teacher_tags"] = _validated_teacher_tags(decision)
        validated[question_id] = decision
    return validated


def apply_decisions(
    tagged: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changes = []
    for question in tagged:
        question_id = question.get("question_id")
        decision = decisions.get(question_id)
        if not decision or decision["decision"] == "defer":
            continue
        tags = question.get("tags") or {}
        before = {
            "primary_knowledge": tags.get("primary_knowledge"),
            "secondary_knowledge": tags.get("secondary_knowledge") or [],
            "ability_tags": tags.get("ability_tags") or [],
            "difficulty": tags.get("difficulty"),
        }
        if decision["decision"] == "change":
            teacher = decision["teacher_tags"]
            meta = PRIMARY_KNOWLEDGE_META[teacher["primary_knowledge"]]
            tags.update(
                {
                    "primary_knowledge": teacher["primary_knowledge"],
                    "curriculum_theme": meta["curriculum_theme"],
                    "knowledge_unit": meta["knowledge_unit"],
                    "skill_tags": list(meta["skill_tags"]),
                    "secondary_knowledge": teacher["secondary_knowledge"],
                    "ability_tags": teacher["ability_tags"],
                    "difficulty": teacher["difficulty"],
                    "difficulty_basis": "teacher_review",
                }
            )
            note = decision.get("review_notes") or "教师修改标签"
            tags["tag_reasoning"] = f"教师复核：{note}"
        tags["tags_reviewed"] = True
        tags["needs_teacher_review"] = False
        tags["review_reasons"] = []
        tags["tags_generated_by"] = _append_generator(tags.get("tags_generated_by", ""))
        tags["teacher_review"] = {
            "decision": decision["decision"],
            "reviewer": decision.get("reviewer") or "",
            "reviewed_at": decision.get("reviewed_at") or datetime.now(timezone.utc).isoformat(),
            "review_notes": decision.get("review_notes") or "",
            "source_signature": decision.get("source_signature") or "",
        }
        after = {
            "primary_knowledge": tags.get("primary_knowledge"),
            "secondary_knowledge": tags.get("secondary_knowledge") or [],
            "ability_tags": tags.get("ability_tags") or [],
            "difficulty": tags.get("difficulty"),
        }
        changes.append(
            {
                "question_id": question_id,
                "decision": decision["decision"],
                "changed_fields": [key for key in before if before[key] != after[key]],
                "before": before,
                "after": after,
            }
        )
    return changes


def _write_history(bank: Path, tags_path: Path, decisions_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    history = bank / "review" / "history" / stamp
    history.mkdir(parents=True, exist_ok=False)
    shutil.copy2(tags_path, history / "all_question_tags.before_teacher_review.json")
    shutil.copy2(decisions_path, history / "teacher_decisions.jsonl")
    return history


def rebuild_outputs(bank: Path, tagged: list[dict[str, Any]], threshold: float, no_audit: bool) -> dict[str, Any]:
    tags_path = bank / "tags" / "all_question_tags.json"
    tagging._write_json(tags_path, tagged)
    tagging._write_jsonl(bank / "tags" / "question_tags.jsonl", tagging._flat_question_tags(tagged))
    tagging._write_review_csv(bank / "review" / "all_tag_review.csv", tagged)
    for name, value in tagging.build_indices(tagged).items():
        tagging._write_json(bank / "index" / f"{name}.json", value)
    summary = write_summary(bank / "tagging_summary.json", tagged, threshold)
    if not no_audit:
        audit_script = Path(__file__).resolve().with_name("audit_tags.py")
        subprocess.run(
            [
                sys.executable,
                str(audit_script),
                "--workspace",
                str(bank),
                "--tags",
                str(tags_path),
                "--output",
                str(bank / "audit"),
                "--threshold",
                str(threshold),
            ],
            check=True,
        )
    recommendation_refresh = refresh_recommendations(bank)
    class_paper_refresh = refresh_class_paper(bank)
    workbench = build_workbench(bank)
    summary["teacher_review_candidates"] = workbench["review_queue"]
    summary["review_queue_sources"] = workbench["review_queue_sources"]
    (bank / "tagging_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {**summary, **workbench, "recommendation_refresh": recommendation_refresh, "class_paper_refresh": class_paper_refresh}


def apply_review_file(
    bank: Path,
    decisions_path: Path,
    threshold: float = 0.8,
    dry_run: bool = False,
    no_audit: bool = False,
    allow_stale: bool = False,
) -> dict[str, Any]:
    bank = bank.resolve()
    decisions_path = decisions_path.resolve()
    tags_path = bank / "tags" / "all_question_tags.json"
    if not tags_path.exists():
        raise SystemExit(f"Tag artifact not found: {tags_path}")
    if not decisions_path.exists():
        raise SystemExit(f"Decision file not found: {decisions_path}")
    tagged = json.loads(tags_path.read_text(encoding="utf-8"))
    decisions = validate_decisions(tagged, read_decisions(decisions_path), allow_stale)
    action_counts = {action: sum(item["decision"] == action for item in decisions.values()) for action in VALID_DECISIONS}
    if dry_run:
        return {
            "status": "valid",
            "questions": len(tagged),
            "decisions": len(decisions),
            "actions": action_counts,
        }

    history = _write_history(bank, tags_path, decisions_path)
    changes = apply_decisions(tagged, decisions)
    outputs = rebuild_outputs(bank, tagged, threshold, no_audit)
    result = {
        "status": "applied",
        "decisions": len(decisions),
        "applied": len(changes),
        "deferred": action_counts["defer"],
        "history_dir": str(history),
        "changes": changes,
        "outputs": outputs,
    }
    tagging._write_json(bank / "review" / "teacher_review_apply_summary.json", result)
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Apply teacher decisions and rebuild tag artifacts.")
    parser.add_argument("structured_dir", type=Path, help="Structured bank directory")
    parser.add_argument("decisions", type=Path, help="Exported teacher_decisions.jsonl")
    parser.add_argument("--threshold", type=float, default=0.8, help="Audit confidence threshold")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing")
    parser.add_argument("--no-audit", action="store_true", help="Skip audit rerun")
    parser.add_argument("--allow-stale", action="store_true", help="Apply decisions whose source signature changed")
    args = parser.parse_args()

    result = apply_review_file(
        args.structured_dir,
        args.decisions,
        threshold=args.threshold,
        dry_run=args.dry_run,
        no_audit=args.no_audit,
        allow_stale=args.allow_stale,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
