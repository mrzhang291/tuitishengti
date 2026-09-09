import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from live_personalized_generation import _draft_missing_fields, _normalise_draft_fields, _personal_skill_rules
from recommend import _resolve_data_paths
from validate_generated_questions import validate_generated_questions


class IntegrityContractTests(unittest.TestCase):
    def bank(self):
        return [
            {"question_id": "q1", "stem_markdown": "原题一"},
            {"question_id": "q2", "stem_markdown": "原题二"},
            {"question_id": "q3", "stem_markdown": "原题三"},
        ]

    def question(self):
        anchor = {"kind": "mother_question", "id": "q1"}
        task_spec = {
            "schema_version": "generation-task-spec-v2",
            "target": {"knowledge": "导数应用", "question_type": "solution"},
            "structure_anchor": anchor,
            "generation_contract": {
                "fulltext_source_question_count": 1,
                "forbidden": [
                    "merge_multiple_source_stems",
                    "copy_or_paraphrase_anchor",
                    "numbers_only_change",
                ],
            },
        }
        return {
            "question_id": "gen-1",
            "is_generated": True,
            "student_visible": True,
            "scope": "knowledge_practice",
            "student_id": "KNOWLEDGE",
            "question_type": "solution",
            "primary_knowledge": "导数应用",
            "stem_markdown": "设函数 $h(x)=e^x-x$，证明其在实数域上有唯一最小值。",
            "options": {},
            "answer": "最小值为1",
            "solution_markdown": "由导数变号可知唯一最小值为1。",
            "generation": {
                "mother_question_id": "q1",
                "strategy": "single_anchor_task_spec",
                "reference_question_ids": ["q1", "q2", "q3"],
                "diagnostic_evidence_question_ids": ["q1", "q2", "q3"],
                "diagnostic_evidence_count": 3,
                "structure_anchor": anchor,
                "fulltext_anchor_count": 1,
                "task_spec": task_spec,
                "pedagogical_fingerprint": {
                    "knowledge": "导数应用",
                    "question_type": "solution",
                    "target_level": 4,
                    "method_family": "derivative_extremum",
                    "function_family": "exponential",
                    "task_intent": "prove_extremum",
                    "reasoning_pattern": "monotonicity_to_extremum",
                    "option_pattern": "none",
                    "complexity_signals": ["proof", "non_mechanical"],
                },
                "changed_dimensions": ["question_angle", "reasoning_path"],
                "novelty_review": "passed",
            },
            "verification": {
                "checked_from_stem_only": True,
                "independent_answer": "最小值为1",
                "answer_matches": True,
                "difficulty_matches": True,
                "estimated_level": 4,
                "answer_solution_consistency": "passed",
                "status": "passed",
            },
            "teacher_review": {"status": "pending"},
        }

    def test_skill_rule_loader_includes_all_current_safety_sections(self):
        rules = _personal_skill_rules()
        self.assertIn("智能新题流程", rules)
        self.assertIn("诊断证据、任务单与结构锚点安全门", rules)
        self.assertIn("智能新题生成门", rules)

    def test_missing_changed_dimensions_is_not_invented(self):
        draft = {"stem_markdown": "题干", "answer": "1", "solution_markdown": "解析", "question_type": "solution"}
        _normalise_draft_fields(draft, "solution")
        self.assertIn("changed_dimensions", _draft_missing_fields(draft))
        self.assertNotIn("changed_dimensions", draft)

    def test_legacy_recommender_never_falls_back_to_bundled_demo_data(self):
        with self.assertRaises(SystemExit):
            _resolve_data_paths(None, None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tags").mkdir()
            (root / "student").mkdir()
            (root / "questions").mkdir()
            (root / "tags" / "all_question_tags.json").write_text("[]", encoding="utf-8")
            (root / "student" / "student_mastery.jsonl").write_text("", encoding="utf-8")
            (root / "student" / "student_exam_records.jsonl").write_text("", encoding="utf-8")
            paths = _resolve_data_paths(None, str(root))
            self.assertTrue(str(paths["source"]).startswith("data-dir:"))

    def test_single_anchor_contract_accepts_complete_question(self):
        self.assertEqual(validate_generated_questions(self.bank(), {"schema_version": "generated-question-set-v1", "questions": [self.question()]}), [])

    def test_single_anchor_contract_rejects_shape_and_difficulty_drift(self):
        question = self.question()
        question["options"] = {"A": "1", "B": "2", "C": "3", "D": "4"}
        question["verification"]["difficulty_matches"] = False
        errors = validate_generated_questions(self.bank(), {"schema_version": "generated-question-set-v1", "questions": [question]})
        self.assertTrue(any("must not contain A/B/C/D" in error for error in errors), errors)
        self.assertTrue(any("difficulty_matches" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
