from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from tag_structured_bank import preserve_teacher_reviews


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tag_structured_bank.py"


class TagStructuredBankTests(unittest.TestCase):
    def test_teacher_review_survives_retag_when_question_content_is_unchanged(self) -> None:
        old = {
            "question_id": "q1",
            "stem_markdown": "题目",
            "solution_markdown": "解析",
            "answer": "A",
            "options": {"A": "1"},
            "tags": {"tags_reviewed": True, "primary_knowledge": "teacher-approved"},
        }
        fresh = {
            "question_id": "q1",
            "stem_markdown": "题目",
            "solution_markdown": "解析",
            "answer": "A",
            "options": {"A": "1"},
            "tags": {"tags_reviewed": False, "primary_knowledge": "model"},
        }
        preserved, invalidated = preserve_teacher_reviews([fresh], [old])
        self.assertEqual((preserved, invalidated), (1, 0))
        self.assertEqual(fresh["tags"]["primary_knowledge"], "teacher-approved")

    def test_one_command_structured_bank_handoff(self) -> None:
        question = {
            "schema_version": "1.0",
            "question_id": "demo_q001",
            "display_id": "DEMO-Q001",
            "question_number": 1,
            "question_type": "solution",
            "stem_markdown": "### 1. 已知函数 $f(x)=x^2$，求导数。",
            "solution_markdown": "$f'(x)=2x$。",
            "answer": "$2x$",
            "options": {},
            "assets": [],
            "quality_flags": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "questions.jsonl").write_text(
                json.dumps(question, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (bank / "structure_report.json").write_text(
                json.dumps({"status": "pass"}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(bank), "--no-audit"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            tagged = json.loads((bank / "tags" / "all_question_tags.json").read_text(encoding="utf-8"))
            summary = json.loads((bank / "tagging_summary.json").read_text(encoding="utf-8"))

            self.assertIn('"questions": 1', completed.stdout)
            self.assertEqual(tagged[0]["question_id"], "demo_q001")
            self.assertEqual(tagged[0]["tags"]["tags_generated_by"], "local_rules_v2_evidence_scored")
            self.assertTrue(tagged[0]["tags"]["tag_evidence"])
            self.assertEqual(summary["questions"], 1)
            self.assertTrue((bank / "review" / "all_tag_review.csv").exists())
            self.assertTrue((bank / "index" / "knowledge_index.json").exists())


if __name__ == "__main__":
    unittest.main()
