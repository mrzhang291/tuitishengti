#!/usr/bin/env python3
"""Run a real Cherry Studio generation acceptance matrix on an isolated bank."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from live_personalized_generation import (
    GatewayConfig,
    GatewayConfigurationError,
    GenerationCancelledError,
    LiveGenerationError,
    cancel_personalized_batch,
    commit_personalized_batch,
    generate_personalized_draft,
    verify_personalized_draft,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_default_cases(bank: Path, limit: int = 5) -> list[dict[str, Any]]:
    pool = _read_json(bank / "generation" / "student_generated_question_candidates.json")
    counts: dict[str, int] = {}
    for slot in pool.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        knowledge = str(slot.get("primary_knowledge") or "").strip()
        if knowledge:
            counts[knowledge] = counts.get(knowledge, 0) + 1
    return [
        {
            "knowledge": knowledge,
            "question_type": "auto",
            "difficulty": "matched",
            "focus": "auto",
            "count": 1,
            "reference_count": 3,
        }
        for knowledge, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def parse_case(value: str) -> dict[str, Any]:
    try:
        case = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--case must be a JSON object: {exc}") from exc
    if not isinstance(case, dict) or not str(case.get("knowledge") or "").strip():
        raise SystemExit("--case requires a non-empty knowledge field")
    case = {
        "knowledge": str(case["knowledge"]).strip(),
        "question_type": str(case.get("question_type") or "auto"),
        "difficulty": str(case.get("difficulty") or "matched"),
        "focus": str(case.get("focus") or "auto"),
        "count": int(case.get("count") or 1),
        "reference_count": int(case.get("reference_count") or 3),
    }
    if case["count"] not in {1, 3, 5}:
        raise SystemExit("Each acceptance case count must be 1, 3, or 5")
    return case


def _run_slot(bank: Path, case: dict[str, Any], batch_id: str, slot_index: int, max_attempts: int) -> dict[str, Any]:
    feedback: list[str] = []
    started = time.monotonic()
    payload = {
        "mode": "knowledge",
        "student_id": "",
        "student_name": "",
        "knowledge": case["knowledge"],
        "question_type": case["question_type"],
        "difficulty": case["difficulty"],
        "focus": case["focus"],
        "reference_count": case["reference_count"],
        "slot_index": slot_index,
        "diversity_round": 1,
        "batch_size": case["count"],
        "batch_id": batch_id,
        "defer_history_commit": True,
    }
    last_error = "unknown"
    for attempt in range(1, max_attempts + 1):
        draft_result = None
        try:
            draft_result = generate_personalized_draft(bank, {**payload, "attempt": attempt, "feedback": feedback})
            result = verify_personalized_draft(bank, {**payload, "attempt": attempt, "draft": draft_result["draft"]})
            question = result["question"]
            commit_personalized_batch(
                bank,
                {"batch_ids": [batch_id], "question_ids": [question["question_id"]], "expected_count": 1},
            )
            generation = question.get("generation") or {}
            verification = question.get("verification") or {}
            return {
                "slot_index": slot_index,
                "status": "passed",
                "attempt": attempt,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "question_id": question.get("question_id"),
                "question_type": question.get("question_type"),
                "target_difficulty": (question.get("personalization") or {}).get("difficulty_label"),
                "generator": generation.get("generator"),
                "draft_source": generation.get("draft_source"),
                "blueprint_family": generation.get("safety_blueprint_family"),
                "consensus_checks": verification.get("consensus_checks"),
            }
        except (GatewayConfigurationError, GenerationCancelledError):
            raise
        except LiveGenerationError as exc:
            last_error = str(exc)
            feedback = [last_error]
            rejected_stem = str(((draft_result or {}).get("draft") or {}).get("stem_markdown") or "").strip()
            if rejected_stem:
                feedback.append(f"上一题不得重复或只换数字：{rejected_stem[:500]}")
    return {
        "slot_index": slot_index,
        "status": "failed",
        "attempt": max_attempts,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "error": last_error,
    }


def run_case(bank: Path, case: dict[str, Any], workers: int, max_attempts: int) -> dict[str, Any]:
    batch_id = f"acceptance-{uuid.uuid4().hex}"
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=min(workers, case["count"])) as executor:
            futures = {
                executor.submit(_run_slot, bank, case, batch_id, slot, max_attempts): slot
                for slot in range(case["count"])
            }
            for future in as_completed(futures):
                slot = futures[future]
                try:
                    results.append(future.result())
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:  # Keep the matrix auditable when one worker fails unexpectedly.
                    results.append(
                        {
                            "slot_index": slot,
                            "status": "failed",
                            "attempt": 0,
                            "elapsed_seconds": round(time.monotonic() - started, 2),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
    finally:
        failed = any(row.get("status") != "passed" for row in results)
        if failed:
            preserve = [str(row.get("question_id") or "") for row in results if row.get("status") == "passed"]
            cancel_personalized_batch({"batch_id": batch_id, "preserve_question_ids": preserve}, bank)
    results.sort(key=lambda row: int(row.get("slot_index") or 0))
    return {
        **case,
        "batch_id": batch_id,
        "status": "passed" if len(results) == case["count"] and all(row["status"] == "passed" for row in results) else "failed",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real Cherry Studio generation acceptance cases.")
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--case", action="append", default=[], help="JSON case; repeat for a matrix")
    parser.add_argument(
        "--case-file",
        action="append",
        default=[],
        type=Path,
        help="UTF-8 JSON object or array of cases; avoids shell quoting differences",
    )
    parser.add_argument("--discover", type=int, default=0, help="Auto-discover this many one-question knowledge cases")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2))
    parser.add_argument("--max-attempts", type=int, default=5, choices=range(1, 6))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bank = args.structured_dir.resolve()
    GatewayConfig.from_env()
    cases = [parse_case(value) for value in args.case]
    for case_file in args.case_file:
        if not case_file.is_file():
            raise SystemExit(f"Acceptance case file not found: {case_file}")
        raw_cases = _read_json(case_file)
        if isinstance(raw_cases, dict):
            raw_cases = [raw_cases]
        if not isinstance(raw_cases, list) or not all(isinstance(row, dict) for row in raw_cases):
            raise SystemExit(f"Acceptance case file must contain an object or object array: {case_file}")
        cases.extend(parse_case(json.dumps(row, ensure_ascii=False)) for row in raw_cases)
    if args.discover:
        cases.extend(discover_default_cases(bank, max(1, min(20, args.discover))))
    if not cases:
        cases = discover_default_cases(bank, 5)
    report = {
        "schema_version": "cherry-generation-acceptance-v1",
        "structured_dir": str(bank),
        "case_count": len(cases),
        "cases": [run_case(bank, case, args.workers, args.max_attempts) for case in cases],
    }
    report["passed_cases"] = sum(case["status"] == "passed" for case in report["cases"])
    report["status"] = "passed" if report["passed_cases"] == len(cases) else "failed"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
