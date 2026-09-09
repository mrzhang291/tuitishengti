#!/usr/bin/env python3
"""Build the teacher-facing tag review and student-performance workbench."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gaokao_taxonomy import KNOWLEDGE_TAXONOMY


ABILITY_TAGS = ["运算求解", "逻辑推理", "数形结合", "分类讨论", "建模应用"]
PRIORITY_ORDER = {"high": 0, "medium": 1, "visual": 2}
FORMULA_SPAN_RE = re.compile(r'<span class="formula" data-formula-id="([^"]*)">(.*?)</span>', re.DOTALL)


def resolve_live_workbench_url(explicit: str | None = None) -> str:
    """Resolve a portable loopback workbench URL without embedding a user path."""
    raw = str(explicit or os.environ.get("TEACHER_WORKBENCH_URL") or "").strip()
    if not raw:
        host = str(os.environ.get("TEACHER_WORKBENCH_HOST") or "127.0.0.1").strip()
        try:
            port = int(os.environ.get("TEACHER_WORKBENCH_PORT") or "8765")
        except ValueError as exc:
            raise ValueError("TEACHER_WORKBENCH_PORT must be an integer") from exc
        if host not in {"127.0.0.1", "localhost", "::1"} or not 1 <= port <= 65535:
            raise ValueError("Teacher workbench must use a valid local loopback host and port")
        host_for_url = f"[{host}]" if host == "::1" else host
        return f"http://{host_for_url}:{port}/review/index.html"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("TEACHER_WORKBENCH_URL must point to this computer")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("TEACHER_WORKBENCH_URL contains an invalid port")
    return raw.rstrip("/") if parsed.path and parsed.path != "/" else raw.rstrip("/") + "/review/index.html"


def refresh_recommendations(bank: Path, top_n: int = 5) -> dict[str, Any] | None:
    """Run the sibling recommender only when student records are available."""
    student_dir = bank / "student"
    records_exist = any(
        (student_dir / name).exists()
        for name in ("student_exam_records.jsonl", "simulated_student_records.jsonl")
    )
    performance_exists = (student_dir / "question_performance.json").exists()
    if not records_exist or not performance_exists:
        return None
    script = (
        Path(__file__).resolve().parents[2]
        / "smart-question-recommender-v3"
        / "scripts"
        / "recommend_structured_bank.py"
    )
    if not script.exists():
        raise SystemExit(f"Recommendation bridge not found: {script}")
    completed = subprocess.run(
        [sys.executable, str(script), str(bank), "--top-n", str(top_n)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def refresh_class_paper(bank: Path) -> dict[str, Any] | None:
    """Assemble the class paper when tagged questions and performance exist."""
    if not (bank / "tags" / "all_question_tags.json").exists():
        return None
    if not (bank / "student" / "question_performance.json").exists():
        return None
    if not any((bank / "student" / name).exists() for name in ("student_exam_records.jsonl", "simulated_student_records.jsonl")):
        return None
    script = (
        Path(__file__).resolve().parents[2]
        / "smart-question-recommender-v3"
        / "scripts"
        / "assemble_class_paper.py"
    )
    if not script.exists():
        raise SystemExit(f"Class paper bridge not found: {script}")
    completed = subprocess.run(
        [sys.executable, str(script), str(bank)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def tag_signature(tags: dict[str, Any]) -> str:
    payload = {
        "curriculum_theme": tags.get("curriculum_theme"),
        "knowledge_unit": tags.get("knowledge_unit"),
        "skill_tags": tags.get("skill_tags") or [],
        "primary_knowledge": tags.get("primary_knowledge"),
        "secondary_knowledge": tags.get("secondary_knowledge") or [],
        "ability_tags": tags.get("ability_tags") or [],
        "difficulty": tags.get("difficulty"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_audit(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["question_id"]: row for row in csv.DictReader(handle)}


def _read_performance(bank: Path) -> dict[str, Any]:
    path = bank / "student" / "question_performance.json"
    if not path.exists():
        return {"simulated": False, "student_count": 0, "question_count": 0, "total_attempts": 0, "overall_correct_rate": 0, "high_error_question_count": 0, "questions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_recommendations(bank: Path) -> dict[str, Any]:
    path = bank / "student" / "recommendations.json"
    if not path.exists():
        return {
            "schema_version": "structured-recommendations-v1",
            "student_count": 0,
            "safe_pool_count": 0,
            "students": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    for student in payload.get("students") or []:
        for question in (student.get("recommendations") or []) + (student.get("alternates") or []):
            question_id = question.get("question_id") or ""
            question["stem_html"] = _math_html(_asset_paths(question.get("stem_html") or "", question_id))
            question["solution_html"] = _math_html(_asset_paths(question.get("solution_html") or "", question_id))
            question["stem_markdown"] = _normalise_formula_text(question.get("stem_markdown") or "")
            question["solution_markdown"] = _normalise_formula_text(question.get("solution_markdown") or "")
    return payload


def _read_class_paper(bank: Path) -> dict[str, Any]:
    path = bank / "paper" / "class_paper.json"
    if not path.exists():
        return {"schema_version": "class-paper-v1", "question_count": 0, "questions": [], "alternates": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    for question in (payload.get("questions") or []) + (payload.get("alternates") or []):
        question_id = question.get("question_id") or ""
        question["stem_html"] = _math_html(_asset_paths(question.get("stem_html") or "", question_id))
        question["solution_html"] = _math_html(_asset_paths(question.get("solution_html") or "", question_id))
        question["stem_markdown"] = _normalise_formula_text(question.get("stem_markdown") or "")
        question["solution_markdown"] = _normalise_formula_text(question.get("solution_markdown") or "")
    return payload


def _normalise_generated_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: _normalise_formula_text(value)
            if key in {"stem_markdown", "solution_markdown"}
            else _normalise_generated_payload(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_normalise_generated_payload(item) for item in payload]
    return payload


def _read_generated_questions(bank: Path) -> dict[str, Any]:
    path = bank / "generation" / "generated_questions.json"
    if not path.exists():
        return {
            "schema_version": "generated-question-set-v1",
            "status": "pending_generation",
            "questions": [],
        }
    return _normalise_generated_payload(json.loads(path.read_text(encoding="utf-8")))


def _read_generated_candidates(bank: Path) -> dict[str, Any]:
    path = bank / "generation" / "generated_question_candidates.json"
    if not path.exists():
        return {
            "schema_version": "generated-question-candidate-pool-v1",
            "slots": [],
        }
    return _normalise_generated_payload(json.loads(path.read_text(encoding="utf-8")))


def _read_student_generated_candidates(bank: Path) -> dict[str, Any]:
    path = bank / "generation" / "student_generated_question_candidates.json"
    if not path.exists():
        return {
            "schema_version": "generated-question-candidate-pool-v1",
            "scope": "student_practice",
            "slots": [],
        }
    return _normalise_generated_payload(json.loads(path.read_text(encoding="utf-8")))


def _asset_paths(markup: str, question_id: str) -> str:
    prefix = f"../questions/{question_id}/assets/"
    return ((markup or "").replace('src="assets/', f'src="{prefix}').replace("src='assets/", f"src='{prefix}").replace('href="assets/', f'href="{prefix}').replace("href='assets/", f"href='{prefix}"))


def _normalise_formula_text(value: str) -> str:
    value = (value or "").replace("^&#x27;", "'").replace("^′", "'").replace("^″", "''")
    value = value.replace("^'", "'")
    value = re.sub(r"(?<=[A-Za-z0-9])_([A-Za-z])_(\d+)", r"_{\1_\2}", value)
    return value


def _math_html(markup: str) -> str:
    if not markup:
        return markup
    markup = _normalise_formula_text(markup)
    if 'class="formula"' not in markup:
        return markup

    def replace_formula(match: re.Match[str]) -> str:
        body = _normalise_formula_text(match.group(2))
        stripped = body.strip()
        if (
            (stripped.startswith("$") and stripped.endswith("$"))
            or (stripped.startswith("\\(") and stripped.endswith("\\)"))
            or (stripped.startswith("\\[") and stripped.endswith("\\]"))
        ):
            return match.group(0)
        return f'<span class="formula" data-formula-id="{match.group(1)}">${body}$</span>'

    return FORMULA_SPAN_RE.sub(replace_formula, markup)


def _queue_items(tagged: list[dict[str, Any]], audit: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    items = []
    for question in tagged:
        question_id = question.get("question_id", "")
        tags = question.get("tags") or {}
        audit_row = audit.get(question_id)
        if not audit_row and not tags.get("needs_teacher_review"):
            continue
        items.append({
            "question_id": question_id,
            "display_id": question.get("display_id") or question_id,
            "source_exam": question.get("source_exam") or "",
            "question_number": question.get("question_number"),
            "question_type": question.get("question_type") or "",
            "priority": (audit_row or {}).get("priority") or "visual",
            "audit_flags": [value for value in (audit_row or {}).get("flags", "").split("；") if value],
            "quality_flags": list(question.get("quality_flags") or []),
            "review_reasons": tags.get("review_reasons") or [],
            "stem_html": _math_html(_asset_paths(question.get("stem_html") or "", question_id)),
            "solution_html": _math_html(_asset_paths(question.get("solution_html") or "", question_id)),
            "answer": str(question.get("answer") or ""),
            "preview_url": f"../questions/{question_id}/preview.html",
            "tags": tags,
            "source_signature": tag_signature(tags),
        })
    return sorted(items, key=lambda item: (PRIORITY_ORDER.get(item["priority"], 9), item["source_exam"], item["question_number"] if isinstance(item["question_number"], int) else 9999, item["question_id"]))


def _inject_root_link(bank: Path, live_url: str) -> None:
    path = bank / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    escaped_url = html.escape(live_url, quote=True)
    entry = f'<section id="tag-review-entry" style="margin:16px 0;padding:16px 18px;border:1px solid #b9c9e8;border-radius:14px;background:#f5f8ff;display:flex;align-items:center;justify-content:space-between;gap:16px"><div><strong>教师学情与推题工作台</strong><div style="margin-top:4px;color:#5d6b82;font-size:13px">整理原题错题集，并通过 Cherry Studio 企业版实时生成个性新题。请从本机服务入口打开，不要直接打开静态 HTML。</div></div><a href="{escaped_url}" style="color:#174ea6;font-weight:700;white-space:nowrap">打开实时教师工作台 →</a></section>'
    if 'id="tag-review-entry"' in text:
        path.write_text(
            re.sub(r'<section id="tag-review-entry".*?</section>', entry, text, count=1, flags=re.DOTALL),
            encoding="utf-8",
        )
        return
    if "</header>" in text:
        path.write_text(text.replace("</header>", "</header>" + entry, 1), encoding="utf-8")


def _template() -> str:
    path = Path(__file__).resolve().parents[1] / "assets" / "review_workbench.html"
    if not path.exists():
        raise SystemExit(f"Workbench template not found: {path}")
    return path.read_text(encoding="utf-8")


def build_workbench(bank: Path, live_url: str | None = None) -> dict[str, Any]:
    bank = bank.resolve()
    live_url = resolve_live_workbench_url(live_url)
    tags_path = bank / "tags" / "all_question_tags.json"
    if not tags_path.exists():
        raise SystemExit(f"Tag artifact not found: {tags_path}")
    tagged = json.loads(tags_path.read_text(encoding="utf-8"))
    audit = _read_audit(bank / "audit" / "tag_quality_audit.csv")
    items = _queue_items(tagged, audit)
    counts = Counter(item["priority"] for item in items)
    payload = {
        "schema_version": "teacher-workbench-v2",
        "bank_name": bank.name,
        "items": items,
        "taxonomy": KNOWLEDGE_TAXONOMY,
        "ability_tags": ABILITY_TAGS,
        "priority_counts": {key: counts.get(key, 0) for key in ("high", "medium", "visual")},
        "student_performance": _read_performance(bank),
        "recommendations": _read_recommendations(bank),
        "class_paper": _read_class_paper(bank),
        "generated_questions": _read_generated_questions(bank),
        "generated_question_candidates": _read_generated_candidates(bank),
        "student_generated_question_candidates": _read_student_generated_candidates(bank),
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    output = bank / "review" / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = _template().replace("__WORKBENCH_DATA__", payload_json)
    rendered = rendered.replace("__LIVE_WORKBENCH_URL__", json.dumps(live_url, ensure_ascii=False))
    output.write_text(rendered, encoding="utf-8")
    _inject_root_link(bank, live_url)
    return {
        "review_workbench": str(output), "review_queue": len(items), "priority_counts": dict(payload["priority_counts"]),
        "review_queue_sources": {"tagging_risk_candidates": sum(bool((item.get("tags") or {}).get("needs_teacher_review")) for item in tagged), "rule_audit_candidates": len(audit), "combined_unique_candidates": len(items)},
        "student_performance": {"simulated": payload["student_performance"].get("simulated", False), "student_count": payload["student_performance"].get("student_count", 0), "question_count": payload["student_performance"].get("question_count", 0)},
        "recommendations": {"student_count": payload["recommendations"].get("student_count", 0), "safe_pool_count": payload["recommendations"].get("safe_pool_count", 0)},
        "class_paper": {"question_count": payload["class_paper"].get("question_count", 0), "total_points": payload["class_paper"].get("total_points", 0), "knowledge_coverage_count": payload["class_paper"].get("knowledge_coverage_count", 0)},
        "generated_questions": {"question_count": len(payload["generated_questions"].get("questions") or []), "status": payload["generated_questions"].get("status")},
        "generated_question_candidates": {"slot_count": len(payload["generated_question_candidates"].get("slots") or []), "candidate_count": sum(len(row.get("candidates") or []) for row in payload["generated_question_candidates"].get("slots") or [])},
        "student_generated_question_candidates": {"slot_count": len(payload["student_generated_question_candidates"].get("slots") or []), "candidate_count": sum(len(row.get("candidates") or []) for row in payload["student_generated_question_candidates"].get("slots") or [])},
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build the teacher workbench.")
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--live-url", help="Loopback URL used by static preview links")
    args = parser.parse_args()
    print(json.dumps(build_workbench(args.structured_dir, args.live_url), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
