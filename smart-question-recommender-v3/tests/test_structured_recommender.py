from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from recommend_structured_bank import build_mastery_rows, build_safe_pool, generate_recommendations
from assemble_class_paper import assemble_class_paper
from validate_class_paper import validate as validate_class_paper


TAG_A = "导数与微分·求导运算·基本函数求导"
TAG_B = "导数应用·单调性·判断单调区间"


def question(index: int, tag: str, question_type: str = "single_choice", complete: bool = True) -> dict:
    return {
        "question_id": f"demo_q{index:03d}", "display_id": f"DEMO-Q{index:03d}",
        "source_exam": "演示试卷", "question_number": index, "question_type": question_type,
        "stem_markdown": f"### {index}. 计算演示函数 {index} 的结果。", "stem_html": f"<p>计算演示函数 {index} 的结果。</p>",
        "answer": str(index), "solution_markdown": f"由定义计算得到 {index}。" if complete else "",
        "solution_html": f"<p>由定义计算得到 {index}。</p>" if complete else "",
        "quality_flags": [] if complete else ["missing_solution"], "assets": [],
        "tags": {"curriculum_theme": "T7导数", "knowledge_unit": "U7.2 导数运算（基本函数/复合/隐函数）",
            "primary_knowledge": tag, "secondary_knowledge": [], "difficulty": 2 if index % 2 else 3,
            "classification_margin": 0.4, "tags_reviewed": False, "tags_confidence": 0.91,
            "tags_confidence_detail": {"overall": 0.91, "primary_knowledge": 0.9, "structure": 0.96}},
    }


class StructuredRecommenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.bank = Path(self.temp.name)
        (self.bank / "tags").mkdir(); (self.bank / "student").mkdir()
        self.questions = [question(1, TAG_A), question(2, TAG_A, "fill_blank"), question(3, TAG_A, "solution"), question(4, TAG_A), question(5, TAG_B), question(6, TAG_B, "multiple_choice"), question(7, TAG_B, "solution"), question(8, TAG_B), question(9, TAG_B, complete=False)]
        (self.bank / "tags" / "all_question_tags.json").write_text(json.dumps(self.questions, ensure_ascii=False), encoding="utf-8")
        answers = [
            {"question_id": "demo_q001", "attempted": True, "score": 0, "full_score": 5},
            {"question_id": "demo_q002", "attempted": False, "score": 0, "full_score": 5},
            {"question_id": "demo_q003", "attempted": True, "score": 2, "full_score": 5},
            {"question_id": "demo_q004", "attempted": True, "score": 5, "full_score": 5},
            {"question_id": "demo_q005", "attempted": True, "score": 0, "full_score": 5},
            {"question_id": "demo_q006", "attempted": False, "score": 0, "full_score": 5},
            {"question_id": "demo_q007", "attempted": True, "score": 5, "full_score": 5},
            {"question_id": "demo_q008", "attempted": True, "score": 5, "full_score": 5},
        ]
        self.student = {"simulated": True, "student_id": "SIM-001", "name": "模拟学生", "class": "高二", "answers": answers}
        (self.bank / "student" / "simulated_student_records.jsonl").write_text(json.dumps(self.student, ensure_ascii=False) + "\n", encoding="utf-8")
        performance = {"questions": [{"question_id": row["question_id"], "correct": 30, "wrong": 18, "omitted": 0, "correct_rate": 0.625} for row in self.questions]}
        (self.bank / "student" / "question_performance.json").write_text(json.dumps(performance, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_omission_is_not_negative_mastery_evidence(self) -> None:
        rows = build_mastery_rows([self.student], self.questions); tag_a = next(row for row in rows if row["tag_name"] == TAG_A)
        self.assertEqual(tag_a["attempts"], 3); self.assertEqual(tag_a["omitted"], 1)
        self.assertEqual(tag_a["wrongs"], 1); self.assertEqual(tag_a["partials"], 1)

    def test_quality_gate_excludes_missing_solution(self) -> None:
        safe, excluded = build_safe_pool(self.questions, self.bank)
        self.assertEqual(len(safe), 8); self.assertIn("demo_q009", excluded); self.assertIn("missing_solution", excluded["demo_q009"])

    def test_five_unique_complete_recommendations_are_deterministic(self) -> None:
        first = generate_recommendations(self.bank, top_n=5, seed=19, write=False); second = generate_recommendations(self.bank, top_n=5, seed=19, write=False)
        first_rows = first["students"][0]["recommendations"]; second_rows = second["students"][0]["recommendations"]
        self.assertEqual([row["question_id"] for row in first_rows], [row["question_id"] for row in second_rows])
        self.assertEqual(len(first_rows), 5); self.assertEqual(len({row["question_id"] for row in first_rows}), 5)
        self.assertTrue(all(row["answer"] and row["solution_markdown"] for row in first_rows))
        self.assertGreaterEqual(len({row["target_knowledge"] for row in first_rows}), 2)
        self.assertEqual(first["artifact_role"], "mother_question_recommendation_plan")
        self.assertFalse(first["student_visible"])
        self.assertTrue(first["requires_new_question_generation"])
        self.assertTrue(all(not row["student_visible"] and row["generation_required"] for row in first_rows))


class ClassPaperTests(unittest.TestCase):
    def test_builds_complete_100_point_class_paper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory); (bank / "tags").mkdir(); (bank / "student").mkdir()
            types = ["single_choice"] * 10 + ["multiple_choice"] * 4 + ["fill_blank"] * 3 + ["solution"] * 3
            questions = []
            performance = []
            for index, question_type in enumerate(types, 1):
                row = question(index, f"演示知识点-{index % 8}", question_type)
                row["source_exam"] = f"演示试卷{index % 6}"
                questions.append(row)
                rate = 0.4 if index <= 2 else 0.78 if index % 4 == 0 else 0.62
                correct = round(40 * rate)
                performance.append({"question_id": row["question_id"], "primary_knowledge": row["tags"]["primary_knowledge"], "attempts": 40, "correct": correct, "wrong": 40 - correct, "omitted": 0, "correct_rate": correct / 40})
            (bank / "tags" / "all_question_tags.json").write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")
            (bank / "student" / "question_performance.json").write_text(json.dumps({"simulated": True, "questions": performance}, ensure_ascii=False), encoding="utf-8")
            (bank / "student" / "simulated_student_records.jsonl").write_text(json.dumps({"simulated": True, "student_id": "SIM-001", "name": "模拟学生", "class": "演示班", "answers": []}, ensure_ascii=False) + "\n", encoding="utf-8")
            first = assemble_class_paper(bank, seed=7, write=False); second = assemble_class_paper(bank, seed=7, write=False)
            rows = first["questions"]
            self.assertEqual(len(rows), 16)
            self.assertEqual(first["total_points"], 100)
            self.assertEqual(first["type_counts"], {"single_choice": 8, "multiple_choice": 3, "fill_blank": 3, "solution": 2})
            self.assertEqual(len({row["question_id"] for row in rows}), 16)
            self.assertTrue(all(row["answer"] and row["solution_markdown"] for row in rows))
            self.assertEqual([row["question_id"] for row in rows], [row["question_id"] for row in second["questions"]])
            self.assertEqual(first["artifact_role"], "source_wrong_question_set")
            self.assertTrue(first["student_visible"])
            self.assertFalse(first["requires_new_question_generation"])
            self.assertTrue(all(row["student_visible"] and not row["generation_required"] for row in rows))
            report = validate_class_paper(bank, seed=7)
            self.assertEqual(report["artifact_role"], "source_wrong_question_set")
            self.assertTrue(report["student_delivery_allowed"])
            self.assertFalse(report["new_question_generation_required"])


if __name__ == "__main__":
    unittest.main()
