import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_generated_questions import validate_generated_questions
from initialize_generation_workspace import initialize_workspace
from generate_personalized_candidate_pool import (
    TEMPLATE_NUMBER_BY_KNOWLEDGE,
    generate_personalized_pool,
    _estimate_target_level,
    _ordered_references,
    _pedagogical_fingerprint,
)


class GeneratedQuestionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bank = [
            {
                "question_id": "source_q001",
                "stem_markdown": "已知函数 $f(x)=x^2-2x$，求 $f(x)$ 的最小值。",
            }
        ]

    def _payload(self, stem: str) -> dict:
        return {
            "schema_version": "generated-question-set-v1",
            "questions": [
                {
                    "question_id": "gen_q001_abc123",
                    "is_generated": True,
                    "student_visible": True,
                    "question_type": "solution",
                    "stem_markdown": stem,
                    "answer": "$a=2$",
                    "solution_markdown": "由判别式条件整理得 $(a-2)^2=0$，所以 $a=2$。",
                    "generation": {
                        "mother_question_id": "source_q001",
                        "strategy": "structural",
                        "changed_dimensions": ["question_angle", "condition_organization"],
                        "relative_difficulty": "Similar",
                        "novelty_review": "passed",
                    },
                    "verification": {
                        "checked_from_stem_only": True,
                        "independent_answer": "$a=2$",
                        "answer_matches": True,
                        "status": "passed",
                    },
                    "teacher_review": {"status": "approved"},
                }
            ],
        }

    def test_accepts_novel_verified_approved_question(self) -> None:
        payload = self._payload(
            "设关于 $x$ 的方程 $x^2-ax+1=0$ 有两个相等实根，求实数 $a$ 的正值。"
        )
        self.assertEqual(
            validate_generated_questions(self.bank, payload, require_approved=True),
            [],
        )

    def test_accepts_not_applicable_empty_source_delivery_file(self) -> None:
        payload = {
            "schema_version": "generated-question-set-v1",
            "scope": "class_source_wrong_question_set",
            "status": "not_applicable_source_questions_are_delivered_directly",
            "questions": [],
        }
        self.assertEqual(validate_generated_questions(self.bank, payload), [])

    def test_rejects_direct_copy_of_source_stem(self) -> None:
        payload = self._payload(self.bank[0]["stem_markdown"])
        errors = validate_generated_questions(self.bank, payload, require_approved=True)
        self.assertTrue(any("copied or too close" in error for error in errors))

    def test_rejects_unapproved_question(self) -> None:
        payload = self._payload(
            "设关于 $x$ 的方程 $x^2-ax+1=0$ 有两个相等实根，求实数 $a$ 的正值。"
        )
        payload["questions"][0]["teacher_review"]["status"] = "pending"
        errors = validate_generated_questions(self.bank, payload, require_approved=True)
        self.assertTrue(any("teacher_review.status" in error for error in errors))

    def test_rejects_number_only_change_contract(self) -> None:
        payload = self._payload("已知函数 $g(x)=x^2-4x$，求 $g(x)$ 的最小值。")
        payload["questions"][0]["generation"]["changed_dimensions"] = ["numbers", "names"]
        errors = validate_generated_questions(self.bank, payload, require_approved=True)
        self.assertTrue(any("must be structural" in error for error in errors))

    def test_rejects_multiple_choice_with_only_one_correct_option(self) -> None:
        payload = self._payload("设函数具有两个驻点，判断下列结论。")
        question = payload["questions"][0]
        question["question_type"] = "multiple_choice"
        question["options"] = {"A": "结论一", "B": "结论二", "C": "结论三", "D": "结论四"}
        question["answer"] = "B"
        errors = validate_generated_questions(self.bank, payload)
        self.assertTrue(any("at least two options" in error for error in errors))

    def test_accepts_three_candidate_pool_and_checks_slot_contract(self) -> None:
        first = self._payload("设关于 $x$ 的方程 $x^2-ax+1=0$ 有两个相等实根，求实数 $a$ 的正值。")["questions"][0]
        candidates = []
        for index in range(3):
            candidate = json.loads(json.dumps(first, ensure_ascii=False))
            candidate["question_id"] = f"gen_q001_candidate_{index + 1}"
            candidate["candidate_index"] = index + 1
            candidate["primary_knowledge"] = "函数"
            candidates.append(candidate)
        payload = {
            "schema_version": "generated-question-candidate-pool-v1",
            "candidate_count_per_slot": 3,
            "slots": [
                {
                    "slot_id": "class-q001",
                    "mother_question_id": "source_q001",
                    "question_type": "solution",
                    "primary_knowledge": "函数",
                    "candidates": candidates,
                }
            ],
        }
        self.assertEqual(validate_generated_questions(self.bank, payload), [])
        payload["slots"][0]["candidates"][1]["generation"]["mother_question_id"] = "other_source"
        errors = validate_generated_questions(self.bank, payload)
        self.assertTrue(any("does not match the slot mother_question_id" in error for error in errors))

    def test_personalized_question_requires_three_source_references(self) -> None:
        payload = self._payload(
            "设关于 $x$ 的方程 $x^2-ax+1=0$ 有两个相等实根，求实数 $a$ 的正值。"
        )
        question = payload["questions"][0]
        question["scope"] = "student_practice"
        question["student_id"] = "S001"
        question["generation"]["strategy"] = "multi_source_synthesis"
        question["generation"]["reference_question_ids"] = ["source_q001"]
        errors = validate_generated_questions(self.bank, payload)
        self.assertTrue(any("at least three source questions" in error for error in errors))

    def test_single_anchor_gate_rejects_invalid_structure_anchors(self) -> None:
        bank = [
            *self.bank,
            {"question_id": "source_q002", "stem_markdown": "第二道诊断证据题。"},
            {"question_id": "source_q003", "stem_markdown": "第三道诊断证据题。"},
            {"question_id": "source_q004", "stem_markdown": "不属于本题诊断集合的题。"},
        ]
        reference_ids = ["source_q001", "source_q002", "source_q003"]

        def payload_with_anchor(anchor: dict, fulltext_anchor_count: int) -> dict:
            payload = self._payload(
                "设函数 $h(x)=x^3-3x+a$，若其在指定区间内恰有两个零点，求参数范围。"
            )
            question = payload["questions"][0]
            question["scope"] = "student_practice"
            question["student_id"] = "S001"
            question["generation"].update(
                {
                    "strategy": "single_anchor_task_spec",
                    "mother_question_id": "source_q001",
                    "reference_question_ids": reference_ids,
                    "reference_count": 3,
                    "diagnostic_evidence_question_ids": reference_ids,
                    "diagnostic_evidence_count": 3,
                    "fulltext_anchor_count": fulltext_anchor_count,
                    "structure_anchor": anchor,
                    "task_spec": {"schema_version": "generation-task-spec-v2"},
                }
            )
            return payload

        invalid_cases = (
            (
                {"kind": "mother_question", "id": "source_q004"},
                1,
                "mother-question anchor must be unique and belong to diagnostic evidence",
            ),
            (
                {"kind": "mother_question", "id": "source_q001"},
                2,
                "mother-question anchor must be unique and belong to diagnostic evidence",
            ),
            (
                {"kind": "skill_blueprint", "id": ""},
                0,
                "skill-blueprint anchor must have an id and no full-text source question",
            ),
            (
                {"kind": "multi_question_merge", "id": "source_q001"},
                1,
                "missing or invalid generation.structure_anchor",
            ),
        )
        for anchor, fulltext_count, expected_error in invalid_cases:
            with self.subTest(anchor=anchor, fulltext_count=fulltext_count):
                errors = validate_generated_questions(
                    bank,
                    payload_with_anchor(anchor, fulltext_count),
                )
                self.assertTrue(any(expected_error in error for error in errors), errors)

    def test_reference_control_is_backed_by_five_safe_questions(self) -> None:
        rows = []
        for index in range(6):
            exact = index < 4
            rows.append(
                {
                    "question_id": f"source_q{index + 1:03d}",
                    "display_id": f"SOURCE-{index + 1:03d}",
                    "source_exam": "演示题库",
                    "question_number": index + 1,
                    "question_type": "single_choice",
                    "stem_markdown": f"安全参考题 {index + 1}",
                    "answer": "A",
                    "solution_markdown": "完整解析。",
                    "quality_flags": [],
                    "tags": {
                        "primary_knowledge": "目标知识点" if exact else "同单元相关知识点",
                        "curriculum_theme": "T1",
                        "knowledge_unit": "U1",
                        "classification_margin": 0.3,
                        "tags_confidence_detail": {
                            "overall": 0.92,
                            "primary_knowledge": 0.91,
                            "structure": 0.95,
                        },
                    },
                }
            )
        references = _ordered_references(
            rows,
            student_id="S001",
            knowledge="目标知识点",
            preferred_ids=["source_q001"],
            limit=5,
        )
        self.assertEqual(len(references), 5)
        self.assertEqual(len({row["question_id"] for row in references}), 5)
        self.assertEqual(references[0]["question_id"], "source_q001")
        self.assertTrue(all(row["solution_markdown"] for row in references))

    def test_knowledge_chain_pool_does_not_require_student_plan(self) -> None:
        knowledge = next(iter(TEMPLATE_NUMBER_BY_KNOWLEDGE))
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "tags").mkdir(parents=True)
            rows = [
                {
                    "question_id": f"source_q{index + 1:03d}",
                    "display_id": f"SOURCE-{index + 1:03d}",
                    "source_exam": "示例题库",
                    "question_number": index + 1,
                    "question_type": "single_choice",
                    "stem_markdown": f"安全参考题 {index + 1}",
                    "answer": "A",
                    "solution_markdown": "完整解析。",
                    "quality_flags": [],
                    "tags": {
                        "primary_knowledge": knowledge,
                        "curriculum_theme": "T1",
                        "knowledge_unit": "U1",
                        "classification_margin": 0.3,
                        "tags_confidence_detail": {
                            "overall": 0.92,
                            "primary_knowledge": 0.91,
                            "structure": 0.95,
                        },
                    },
                }
                for index in range(3)
            ]
            (bank / "tags" / "all_question_tags.json").write_text(
                json.dumps(rows, ensure_ascii=False),
                encoding="utf-8",
            )
            summary = generate_personalized_pool(bank)
            self.assertEqual(summary["student_count"], 0)
            self.assertEqual(summary["knowledge_chain_count"], 1)
            payload = json.loads(
                (bank / "generation" / "student_generated_question_candidates.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["logic"], "knowledge_first_before_student_profile")
            self.assertEqual(payload["slots"][0]["scope"], "knowledge_practice")
            self.assertEqual(payload["slots"][0]["student_id"], "KNOWLEDGE")
            self.assertEqual(len(payload["slots"][0]["candidates"]), 3)
            self.assertEqual(
                payload["slots"][0]["recommendation_logic_version"],
                "knowledge-teaching-chain-scoring-v1",
            )
            self.assertEqual(len(payload["slots"][0]["candidate_ranking"]), 3)
            candidate = payload["slots"][0]["candidates"][0]
            self.assertIn("pedagogical_fingerprint", candidate["generation"])
            self.assertIn("recommendation_score", candidate["generation"])
            self.assertIn(candidate["verification"]["estimated_level"], {1, 2, 3, 4, 5})
            self.assertIn(candidate["verification"]["difficulty_matches"], {True, False})

    def test_offline_recommendation_rules_distinguish_formula_and_transfer_tasks(self) -> None:
        basic_stem = (
            "设 $x>0$，函数 $f(x)=\\sqrt{x}+\\dfrac1x$，则 $f'(x)=（ ）。\n\n"
            "A. $\\dfrac1{2\\sqrt{x}}-\\dfrac1{x^2}$\n\n"
            "B. $\\dfrac1{\\sqrt{x}}+\\dfrac1{x^2}$"
        )
        basic_solution = "$f'(x)=\\frac1{2\\sqrt{x}}-\\frac1{x^2}$，故选 A。"
        self.assertLessEqual(
            _estimate_target_level(
                "single_choice",
                basic_stem,
                basic_solution,
                {"A": "A", "B": "B", "C": "C", "D": "D"},
                "A",
            ),
            2,
        )
        fingerprint = _pedagogical_fingerprint(
            knowledge="导数与微分·求导运算·基本函数求导",
            question_type="single_choice",
            target_level=2,
            stem=basic_stem,
            solution=basic_solution,
            options={"A": "A", "B": "B", "C": "C", "D": "D"},
            answer="A",
        )
        self.assertEqual(fingerprint["method_family"], "basic_derivative_formula")
        self.assertEqual(fingerprint["reasoning_pattern"], "direct_formula")

        transfer_stem = (
            "已知函数 $f(x)=\\dfrac{e^x}{x}+\\ln x-a\\ (x>0,a\\in R)$，"
            "令 $g(x)=xf'(x)$，下列关于 $g(x)$ 的单调性判断正确的是（ ）。\n\n"
            "A. $g(x)$ 在 $(0,1)$ 上递减，在 $(1,+\\infty)$ 上递增\n\n"
            "B. 存在参数使 $g(x)$ 恒为负\n\n"
            "C. $g(x)$ 含参数但导函数判号与 $a$ 无关\n\n"
            "D. $g(x)$ 不可能单调"
        )
        transfer_solution = (
            "由题得 $g(x)=e^x(x-1)/x+1$。求导得 "
            "$g'(x)=e^x(x^2-x+1)/x^2$，结合区间端点和判号可得正确结论。"
        )
        self.assertGreaterEqual(
            _estimate_target_level(
                "multiple_choice",
                transfer_stem,
                transfer_solution,
                {"A": "A", "B": "B", "C": "C", "D": "D"},
                "AC",
            ),
            4,
        )

    def test_workspace_initialization_separates_source_set_from_personal_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "paper").mkdir()
            (bank / "student").mkdir()
            mother = {
                "question_id": "source_q001",
                "question_type": "solution",
                "primary_knowledge": "函数单调性",
                "stem_markdown": "母题题干",
                "answer": "母题答案",
                "solution_markdown": "母题解析",
                "points": 12,
            }
            (bank / "paper" / "class_paper.json").write_text(
                json.dumps(
                    {
                        "artifact_role": "source_wrong_question_set",
                        "student_visible": True,
                        "requires_new_question_generation": False,
                        "paper_title": "测试原题错题集",
                        "questions": [mother],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (bank / "student" / "recommendations.json").write_text(
                json.dumps(
                    {
                        "students": [
                            {
                                "student_id": "S001",
                                "name": "测试学生",
                                "recommendations": [mother],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = initialize_workspace(bank)
            class_plan = json.loads((bank / "generation" / "mother_question_plan.json").read_text(encoding="utf-8"))
            generated = json.loads((bank / "generation" / "generated_questions.json").read_text(encoding="utf-8"))
            class_candidates = json.loads((bank / "generation" / "generated_question_candidates.json").read_text(encoding="utf-8"))
            student_paper = json.loads((bank / "paper" / "student_paper.json").read_text(encoding="utf-8"))
            student_plans = json.loads((bank / "generation" / "student_mother_question_plans.json").read_text(encoding="utf-8"))
            self.assertEqual(result["class_source_question_count"], 1)
            self.assertEqual(result["class_mother_slots"], 0)
            self.assertEqual(class_plan["artifact_role"], "source_wrong_question_set")
            self.assertFalse(class_plan["student_visible"])
            self.assertFalse(class_plan["requires_new_question_generation"])
            self.assertEqual(class_plan["slots"], [])
            self.assertEqual(generated["questions"], [])
            self.assertIn("not_applicable", generated["status"])
            self.assertEqual(class_candidates["slots"], [])
            self.assertEqual(student_paper["artifact_role"], "source_wrong_question_set")
            self.assertTrue(student_paper["student_visible"])
            self.assertFalse(student_paper["requires_new_question_generation"])
            self.assertEqual(student_paper["questions"][0]["question_id"], "source_q001")
            self.assertEqual(student_plans["students"][0]["slots"][0]["mother_question_id"], "source_q001")


if __name__ == "__main__":
    unittest.main()
