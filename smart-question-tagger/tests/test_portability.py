import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_html_homework_question_bank import (
    choose_question_markdown,
    exam_code_from_folder,
    infer_type_and_score,
)
from build_review_workbench import build_workbench, resolve_live_workbench_url
from suggest_calibrations import _suggest


class PortabilityTests(unittest.TestCase):
    def test_question_discovery_prefers_exam_named_markdown_over_notes(self):
        paths = [Path("课堂备注.md"), Path("高二数学试卷.md"), Path("高二数学答案.md")]
        self.assertEqual(choose_question_markdown(paths).name, "高二数学试卷.md")

    def test_exam_ids_and_question_shapes_are_content_driven(self):
        first = exam_code_from_folder("甲校高二期中考试")
        self.assertEqual(first, exam_code_from_folder("甲校高二期中考试"))
        self.assertNotEqual(first, exam_code_from_folder("乙校高二期中考试"))
        self.assertRegex(first, r"^exam_[0-9a-f]{12}$")
        self.assertEqual(infer_type_and_score("题干\nA. 1\nB. 2\nC. 3\nD. 4", "B"), ("single_choice", None))
        self.assertEqual(infer_type_and_score("多选题\nA. 1\nB. 2\nC. 3\nD. 4", "BD"), ("multiple_choice", None))
        self.assertEqual(infer_type_and_score("结果为____。", "1"), ("fill_blank", None))
        self.assertEqual(infer_type_and_score("证明该不等式。", "见解析"), ("solution", None))

    def test_workbench_url_uses_environment_and_rejects_remote_hosts(self):
        with patch.dict(os.environ, {"TEACHER_WORKBENCH_PORT": "9123"}, clear=False):
            self.assertEqual(resolve_live_workbench_url(), "http://127.0.0.1:9123/review/index.html")
        with self.assertRaises(ValueError):
            resolve_live_workbench_url("https://example.com/review/index.html")

    def test_built_workbench_does_not_embed_absolute_bank_path(self):
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "tags").mkdir()
            (bank / "tags" / "all_question_tags.json").write_text("[]", encoding="utf-8")
            (bank / "index.html").write_text("<html><header></header></html>", encoding="utf-8")
            build_workbench(bank, "http://127.0.0.1:9123/review/index.html")
            page = (bank / "review" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn(str(bank), page)
            self.assertNotIn('"bank_path"', page)
            self.assertIn("http://127.0.0.1:9123/review/index.html", page)

    def test_calibration_without_external_override_never_invents_a_change(self):
        packet = {
            "question_id": "any-bank-question",
            "current_tags": {"primary_knowledge": "导数与微分·求导运算·基本函数求导", "tags_confidence": 0.6},
            "audit_flags": ["low_confidence"],
        }
        suggestion = _suggest(packet, None)
        self.assertEqual(suggestion["action"], "review")
        self.assertEqual(suggestion["suggested_tags"], packet["current_tags"])
        self.assertEqual(suggestion["suggestion_source"], "audit_triage_only")


if __name__ == "__main__":
    unittest.main()
