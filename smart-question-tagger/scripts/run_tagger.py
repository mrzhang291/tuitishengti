#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Run school math question tagging workflows.")
    parser.add_argument("--workspace", default=".", help="Project workspace root containing test-data for legacy closed-loop runs.")
    parser.add_argument("--scope", choices=["midterm", "full"], default="full", help="Closed-loop tagging scope.")
    parser.add_argument("--output", required=True, help="Output directory for tag artifacts.")
    parser.add_argument("--student-id", default=None, help="Optional student id used for the legacy recommendation smoke output.")
    parser.add_argument("--question-json", default=None, help="Optional single question JSON file, or JSON array of questions, to tag outside the closed loop.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Summary output format.")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    output = Path(args.output).resolve()
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from tagging import run_full_closed_loop, run_midterm_closed_loop, tag_question

    if args.question_json:
        summary = _tag_question_json(Path(args.question_json).resolve(), output, tag_question)
    else:
        _ensure_workspace(workspace, args.scope)
        runner = run_full_closed_loop if args.scope == "full" else run_midterm_closed_loop
        summary = runner(workspace, output, sample_student_id=args.student_id)
        summary["scope"] = args.scope

    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")


def _ensure_workspace(workspace, scope):
    required = [workspace / "test-data" / "student_exam_records.jsonl"]
    if scope in {"midterm", "full"}:
        required.append(workspace / "test-data" / "midterm_tagging_input.json")
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing required workspace files: " + "; ".join(missing))


def _tag_question_json(path, output, tag_question):
    if not path.exists():
        raise SystemExit(f"Question JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data if isinstance(data, list) else [data]
    tagged = [tag_question(question) for question in questions]

    output.mkdir(parents=True, exist_ok=True)
    out_path = output / "tagged_questions.json"
    out_path.write_text(json.dumps(tagged, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "scope": "question",
        "tagged_questions": len(tagged),
        "output_dir": str(output),
        "tags_file": str(out_path),
    }


if __name__ == "__main__":
    main()
