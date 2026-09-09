#!/usr/bin/env python3
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from gaokao_taxonomy import CURRICULUM_THEMES, KNOWLEDGE_TAXONOMY, KNOWLEDGE_UNITS

TAXONOMY = set(KNOWLEDGE_TAXONOMY)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Audit generated question tags and create LLM calibration packets.")
    parser.add_argument("--workspace", default=".", help="Project workspace root.")
    parser.add_argument("--tags", default=None, help="Path to all_question_tags.json. Defaults to workspace output/full tags.")
    parser.add_argument("--output", required=True, help="Output directory for audit artifacts.")
    parser.add_argument("--threshold", type=float, default=0.8, help="Confidence threshold for priority review.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Summary output format.")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    tags_path = _resolve_tags_path(workspace, args.tags)
    questions_root = _resolve_questions_root(workspace)
    tagged_questions = json.loads(tags_path.read_text(encoding="utf-8"))

    rows = []
    llm_packets = []
    for question in tagged_questions:
        detail = _load_question_detail(questions_root, question.get("question_id"))
        merged = _merge_question(question, detail)
        audit = _audit_question(merged, threshold=args.threshold)
        if audit["flags"]:
            rows.append(audit)
            llm_packets.append(_llm_packet(merged, audit))

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    _write_audit_csv(output / "tag_quality_audit.csv", rows)
    _write_jsonl(output / "llm_calibration_batch.jsonl", llm_packets)
    high_priority_packets = [packet for packet, row in zip(llm_packets, rows) if row["priority"] == "high"]
    _write_jsonl(output / "llm_calibration_high_priority.jsonl", high_priority_packets)
    summary = _summary(tagged_questions, rows, tags_path, output, args.threshold)
    (output / "tag_quality_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")

    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")


def _resolve_tags_path(workspace, explicit):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            workspace / "tags" / "all_question_tags.json",
            workspace / "output" / "full" / "tags" / "all_question_tags.json",
            workspace / "skills" / "smart-question-recommender" / "references" / "data" / "tags" / "all_question_tags.json",
        ]
    )
    for path in candidates:
        path = path if path.is_absolute() else workspace / path
        if path.exists():
            return path.resolve()
    raise SystemExit("Could not find all_question_tags.json. Pass --tags explicitly.")


def _resolve_questions_root(workspace):
    candidates = [
        workspace / "questions",
        workspace / "skills" / "smart-question-recommender" / "references" / "data" / "questions",
        workspace / "skills" / "smart-question-tagger" / "references" / "data" / "questions",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _load_question_detail(root, question_id):
    if not root or not question_id:
        return {}
    path = root / question_id / "question.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_question(question, detail):
    merged = dict(detail or {})
    merged.update(question)
    if "tags" not in merged and "primary_knowledge" in merged:
        merged["tags"] = {
            "primary_knowledge": merged.get("primary_knowledge"),
            "secondary_knowledge": merged.get("secondary_knowledge", []),
            "ability_tags": merged.get("ability_tags", []),
            "difficulty": merged.get("difficulty"),
            "difficulty_basis": merged.get("difficulty_basis"),
            "tags_confidence": merged.get("tags_confidence"),
            "tags_reviewed": merged.get("tags_reviewed", False),
            "tags_generated_by": merged.get("tags_generated_by", ""),
        }
    return merged


def _audit_question(question, threshold):
    tags = question.get("tags", {})
    primary = tags.get("primary_knowledge", "")
    secondary = tags.get("secondary_knowledge") or []
    abilities = tags.get("ability_tags") or []
    confidence = float(tags.get("tags_confidence") or 0)
    confidence_detail = tags.get("tags_confidence_detail") or {}
    reviewed = bool(tags.get("tags_reviewed"))
    text = _question_text(question)

    flags = []
    if primary not in TAXONOMY:
        flags.append("primary_outside_taxonomy")
    if tags.get("curriculum_theme") not in CURRICULUM_THEMES:
        flags.append("curriculum_theme_outside_taxonomy")
    if tags.get("knowledge_unit") not in KNOWLEDGE_UNITS:
        flags.append("knowledge_unit_outside_taxonomy")
    # Teacher-approved labels supersede model uncertainty and heuristic checks.
    # Taxonomy integrity remains enforced even after review.
    if not reviewed:
        if confidence < threshold:
            flags.append(f"confidence_below_{threshold:g}")
        if confidence_detail and float(confidence_detail.get("primary_knowledge") or 0) < 0.72:
            flags.append("primary_confidence_low")
        if confidence_detail and float(confidence_detail.get("classification_margin") or 0) < 0.12:
            flags.append("classification_margin_low")
        if _looks_derivative_method(text) and primary == "函数综合·函数性质·奇偶性与周期性" and not _looks_function_property(text):
            flags.append("possible_function_property_surface_error")
        if _looks_geometry_model(text) and _looks_optimization(text):
            if primary != "导数应用·极值与最值·求最值":
                flags.append("geometry_optimization_primary_check")
            if not any("几何" in item or "体积" in item or "建模" in item for item in secondary):
                flags.append("geometry_context_missing_secondary")
        if _looks_expectation(text) and primary != "概率统计·数字特征·期望与方差":
            flags.append("expectation_primary_check")
        if "分类讨论" not in abilities and _looks_parameter_casework(text):
            flags.append("casework_ability_missing")
        if primary == "导数与微分·切线方程·求切线" and "切线" not in text:
            flags.append("tangent_primary_without_tangent_keyword")
    if flags and not reviewed:
        flags.insert(0, "unreviewed")

    priority = _priority(flags, confidence)
    return {
        "question_id": question.get("question_id", ""),
        "display_id": question.get("display_id", ""),
        "priority": priority,
        "confidence": confidence,
        "primary_confidence": confidence_detail.get("primary_knowledge", ""),
        "classification_margin": confidence_detail.get("classification_margin", ""),
        "runner_up": (tags.get("tag_candidates") or [{}, {}])[1].get("primary_knowledge", "")
        if len(tags.get("tag_candidates") or []) > 1
        else "",
        "primary_knowledge": primary,
        "secondary_knowledge": "；".join(secondary),
        "ability_tags": "；".join(abilities),
        "difficulty": tags.get("difficulty", ""),
        "difficulty_basis": tags.get("difficulty_basis", ""),
        "flags": flags,
        "stem_preview": _preview(question.get("stem_markdown") or question.get("stem_text") or "", 120),
    }


def _question_text(question):
    return " ".join(
        str(question.get(key, ""))
        for key in ("stem_markdown", "stem_text", "solution_markdown", "solution_text", "answer")
    )


def _looks_derivative_method(text):
    return any(token in text for token in ("导数", "f^'", "f'(x)", "求导", "单调", "极值", "最值", "V'(x)", "V^'"))


def _looks_function_property(text):
    return any(token in text for token in ("周期", "奇偶", "对称", "偶函数", "奇函数", "sin", "cos"))


def _looks_geometry_model(text):
    return any(token in text for token in ("几何", "体积", "容积", "容器", "铁片", "棱锥", "棱柱", "圆锥", "方盒", "空间"))


def _looks_optimization(text):
    return any(token in text for token in ("最大", "最小", "最值", "极大", "极小", "单调递增", "单调递减"))


def _looks_expectation(text):
    return any(token in text for token in ("E(X)", "期望", "数学期望", "方差"))


def _looks_parameter_casework(text):
    return any(token in text for token in ("参数", "取值范围", "分类讨论", "恒成立", "存在")) or any(
        token in text for token in ("a∈", "m∈", "a>", "a<", "m>", "m<")
    )


def _priority(flags, confidence):
    issue_flags = [flag for flag in flags if flag != "unreviewed"]
    if any(flag.endswith("_check") or flag.endswith("_error") or flag.endswith("_missing") for flag in issue_flags):
        return "high"
    if confidence < 0.7:
        return "high"
    if issue_flags:
        return "medium"
    return "low"


def _llm_packet(question, audit):
    tags = question.get("tags", {})
    return {
        "question_id": question.get("question_id", ""),
        "task": "Calibrate the math question tag. Prefer the main solution method for primary_knowledge and use secondary_knowledge for context.",
        "current_tags": tags,
        "audit_flags": audit["flags"],
        "stem_markdown": question.get("stem_markdown", ""),
        "solution_markdown": _preview(question.get("solution_markdown") or question.get("solution_text") or "", 1000),
        "answer": question.get("answer", ""),
        "required_response_schema": {
            "primary_knowledge": "string",
            "secondary_knowledge": ["string"],
            "ability_tags": ["string"],
            "difficulty": "integer 1-5",
            "difficulty_basis": "string",
            "calibration_rationale": "string",
            "needs_teacher_review": "boolean",
        },
    }


def _write_audit_csv(path, rows):
    fieldnames = [
        "question_id",
        "display_id",
        "priority",
        "confidence",
        "primary_confidence",
        "classification_margin",
        "runner_up",
        "primary_knowledge",
        "secondary_knowledge",
        "ability_tags",
        "difficulty",
        "difficulty_basis",
        "flags",
        "stem_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            csv_row["flags"] = "；".join(row["flags"])
            writer.writerow(csv_row)


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _summary(tagged_questions, rows, tags_path, output, threshold):
    priorities = Counter(row["priority"] for row in rows)
    flag_counts = Counter(flag for row in rows for flag in row["flags"])
    confidence_values = [float((q.get("tags") or {}).get("tags_confidence") or 0) for q in tagged_questions]
    return {
        "tags_file": str(tags_path),
        "output_dir": str(output),
        "question_count": len(tagged_questions),
        "review_candidates": len(rows),
        "unreviewed_drafts": sum(not bool((q.get("tags") or {}).get("tags_reviewed")) for q in tagged_questions),
        "threshold": threshold,
        "confidence_min": min(confidence_values) if confidence_values else None,
        "confidence_max": max(confidence_values) if confidence_values else None,
        "priority_counts": dict(priorities),
        "top_flags": dict(flag_counts.most_common(12)),
        "audit_csv": str(output / "tag_quality_audit.csv"),
        "llm_batch": str(output / "llm_calibration_batch.jsonl"),
        "llm_high_priority_batch": str(output / "llm_calibration_high_priority.jsonl"),
    }


def _summary_markdown(summary):
    lines = [
        "# Tag Quality Audit",
        "",
        f"- Questions: {summary['question_count']}",
        f"- Review candidates: {summary['review_candidates']}",
        f"- Unreviewed drafts: {summary['unreviewed_drafts']}",
        f"- Confidence threshold: {summary['threshold']}",
        f"- Confidence range: {summary['confidence_min']} - {summary['confidence_max']}",
        "",
        "## Priority Counts",
        "",
    ]
    for key, value in summary["priority_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Top Flags", ""])
    for key, value in summary["top_flags"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- Audit CSV: `{summary['audit_csv']}`",
            f"- LLM batch: `{summary['llm_batch']}`",
            f"- LLM high-priority batch: `{summary['llm_high_priority_batch']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _preview(text, length):
    return " ".join(str(text).split())[:length]


if __name__ == "__main__":
    main()
