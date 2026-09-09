from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tag_structured_outputs.py"
SPEC = importlib.util.spec_from_file_location("exam_ocr_tag_structured_outputs", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TagStructuredOutputsTests(unittest.TestCase):
    def test_blocking_flags_stop_tag_handoff(self) -> None:
        blocked = MODULE.blocking_tag_questions(
            [
                {"question_id": "q1", "quality_flags": ["contains_images"]},
                {"question_id": "q2", "quality_flags": ["answer_contamination"]},
                {"question_id": "q3", "quality_flags": ["markdown_math_loss", "missing_solution"]},
            ]
        )
        self.assertEqual(
            blocked,
            [
                {"question_id": "q2", "flags": ["answer_contamination"]},
                {"question_id": "q3", "flags": ["markdown_math_loss"]},
            ],
        )

    def test_summary_exposes_confidence_bands_and_review_reasons(self) -> None:
        tagged = [
            {
                "quality_flags": ["contains_images"],
                "tags": {
                    "curriculum_theme": "T7导数",
                    "tags_confidence": 0.62,
                    "needs_teacher_review": True,
                },
            },
            {
                "quality_flags": [],
                "tags": {
                    "curriculum_theme": "T9概率统计",
                    "tags_confidence": 0.84,
                    "needs_teacher_review": False,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            summary = MODULE.write_summary(Path(directory) / "summary.json", tagged, 0.8)
        self.assertEqual(summary["confidence_bands"], {"high_>=0.80": 1, "low_<0.65": 1})
        self.assertEqual(summary["review_reasons"]["confidence_below_threshold"], 1)
        self.assertEqual(summary["review_reasons"]["contains_images"], 1)


if __name__ == "__main__":
    unittest.main()
