from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "structure_outputs.py"
SPEC = importlib.util.spec_from_file_location("exam_ocr_structure_outputs", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class StructureOutputsTests(unittest.TestCase):
    def test_declared_section_count_detects_missing_trailing_question(self) -> None:
        markdown = "## 一、选择题：本题共2小题\n\n### 1. 题目"
        _blocks, structure = MODULE.split_question_blocks(markdown)
        self.assertEqual(structure["declared_total"], 2)
        self.assertEqual(structure["missing"], [2])

    def test_index_loads_mathjax_and_renders_answer_as_markdown(self) -> None:
        question = {
            "question_id": "demo_q015",
            "display_id": "SZ24-G2-W01-Q015",
            "source_exam": "示例试卷",
            "question_number": 15,
            "question_type": "solution",
            "answer": r"(1) $y'=\mathrm{e}^{-x}$ (2) $y'=\frac{1}{x}$",
            "quality_flags": [],
        }
        rendered = MODULE.render_index([question])
        self.assertIn("tex-svg.js", rendered)
        self.assertIn("MathJax.typesetPromise", rendered)
        self.assertIn(r"<p>(1) $y'=\mathrm{e}^{-x}$", rendered)
        self.assertNotIn(r"<p>(1) $y&#x27;=\mathrm{e}^{-x}$", rendered)
        self.assertIn("解答题", rendered)
        self.assertIn("SZ24-G2-W01-Q015", rendered)

    def test_markdown_preserves_tex_escapes_inside_math(self) -> None:
        source = (
            r"故 $\left\{\begin{aligned}h(0)>g(0)\\h(-1)\leqslant g(-1)"
            r"\end{aligned}\right.$"
        )
        rendered = MODULE.markdown_to_html(source)
        self.assertIn(r"\left\{\begin{aligned}", rendered)
        self.assertIn(r"\\h(-1)", rendered)
        self.assertNotIn(r"\left{\begin", rendered)
        self.assertTrue(MODULE.markdown_math_is_preserved(source, rendered))
        self.assertFalse(MODULE.markdown_math_is_preserved(source, "<p>公式丢失</p>"))

    def test_numbered_fill_summary_stops_before_solution_heading(self) -> None:
        solution = (
            "填空题：12. $y=x$ ， $y=-x$ 13.-3 14. $[a,1)$\n\n"
            "### 5. A\n\n**详解**后续解析"
        )
        answers = MODULE.extract_summary_answers(solution, {"fill_blank": [12, 13, 14]})
        self.assertEqual(answers[12], "$y=x$ ， $y=-x$")
        self.assertEqual(answers[13], "-3")
        self.assertEqual(answers[14], "$[a,1)$")
        self.assertNotIn("###", " ".join(answers.values()))

    def test_positional_fill_summary_drops_inline_solution(self) -> None:
        solution = "填空题： $(0,1)$ ， $[0,1)$ ， $(0,6)$ 1. A 由题意可得"
        answers = MODULE.extract_summary_answers(solution, {"fill_blank": [12, 13, 14]})
        self.assertEqual(answers, {12: "$(0,1)$", 13: "$[0,1)$", 14: "$(0,6)$"})

    def test_compact_fill_heading_is_not_a_solution_block(self) -> None:
        solution = (
            "### 12. 0.61 $13.-\\frac{3}{2}$ 14. $[0,1)$\n\n"
            "### 6.\n\n**详解**第六题解析\n\n"
            "### 7.\n\n**详解**第七题解析"
        )
        blocks = MODULE.split_solution_blocks(
            solution,
            {6, 7, 12, 13, 14},
            {6: "single_choice", 7: "single_choice", 12: "fill_blank", 13: "fill_blank", 14: "fill_blank"},
        )
        self.assertNotIn(12, blocks)
        self.assertIn(6, blocks)
        self.assertIn(7, blocks)

    def test_see_solution_answer_is_not_treated_as_contamination(self) -> None:
        self.assertEqual(MODULE.truncate_answer_contamination("见解析"), "见解析")

    def test_display_question_id_is_short_and_stable(self) -> None:
        identity_a = MODULE.exam_identity("甲校高二数学周测（3）试卷")
        identity_b = MODULE.exam_identity("甲校高二数学周测（3）参考答案")
        self.assertEqual(identity_a, identity_b)
        self.assertRegex(identity_a[1], r"^exam_[0-9a-f]{12}$")
        expected_prefix = identity_a[1].replace("_", "-").upper()
        self.assertEqual(MODULE.display_question_id(identity_a[1], 14), f"{expected_prefix}-Q014")

    def test_display_repairs_only_one_missing_dollar_delimiter(self) -> None:
        self.assertEqual(
            MODULE.protect_answer_math_for_display(r"-\frac{3\sqrt{3}}{2}$"),
            r"$-\frac{3\sqrt{3}}{2}$",
        )
        self.assertEqual(MODULE.protect_answer_math_for_display(r"$x+1$ and $y+1$"), r"$x+1$ and $y+1$")

    def test_report_distinguishes_resolved_and_unresolved_diagnostics(self) -> None:
        cases = [
            {
                "exam_code": "demo",
                "source_exam": "示例试卷",
                "question_file": "paper.md",
                "solution_file": "answer.md",
                "question_count": 2,
                "seen": [1, 2],
                "missing": [],
                "duplicates": [],
                "repairs": ["split_embedded_question_2"],
                "repair_details": [
                    {
                        "code": "split_embedded_question_2",
                        "label": "拆分行内第 2 题",
                        "description": "已恢复第 2 题。",
                    }
                ],
                "structure_before_repair": {
                    "seen": [1],
                    "missing": [2],
                    "duplicates": [],
                    "declared_total": 2,
                    "expected_total": 2,
                },
                "structure_after_repair": {
                    "seen": [1, 2],
                    "missing": [],
                    "duplicates": [],
                    "declared_total": 2,
                    "expected_total": 2,
                },
                "resolved_missing": [2],
                "resolved_duplicates": [],
            }
        ]
        questions = [
            {"question_id": "demo_q001", "quality_flags": []},
            {"question_id": "demo_q002", "quality_flags": ["contains_images"]},
        ]
        report = MODULE.build_structure_report(questions, cases, Path("report-output"))
        self.assertEqual(report["status"], "pass")
        rendered = MODULE.render_structure_report(report)
        self.assertIn("已修复：2", rendered)
        self.assertIn("拆分行内第 2 题", rendered)
        self.assertNotIn("[]", rendered)
        self.assertNotIn("split_embedded_question_2</span>", rendered)

    def test_math_loss_and_answer_contamination_block_tag_handoff(self) -> None:
        questions = [
            {
                "question_id": "demo_q001",
                "display_id": "DEMO-Q001",
                "quality_flags": ["markdown_math_loss", "answer_contamination"],
            }
        ]
        report = MODULE.build_structure_report(questions, [], Path("report-output"))
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("公式" in failure for failure in report["failures"]))
        self.assertTrue(any("串题" in failure for failure in report["failures"]))


if __name__ == "__main__":
    unittest.main()
