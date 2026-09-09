from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from live_personalized_generation import (
    SAFETY_FAMILY_COUNTS,
    SAFE_BLUEPRINT_VERIFICATION_POLICY_VERSION,
    GatewayConfig,
    GenerationCancelledError,
    LiveGenerationError,
    _answers_match,
    _build_generation_task_spec,
    _choice_answer_labels,
    _clean_answer_content,
    _dynamic_structure_recipe,
    _declared_equality_point_error,
    _difficulty_gate_evidence,
    _difficulty_gate_error,
    _extremum_blueprint,
    _feedback_recovery_contract,
    _gateway_chat_json,
    _generation_messages,
    _history_rows,
    _inequality_blueprint,
    _inequality_solution_blueprint,
    _knowledge_safety_constraints,
    _history_similarity_errors,
    _inequality_challenge_choice_blueprint,
    _math_structure,
    _monotonicity_blueprint,
    _number_agnostic_structure,
    _parse_json_content,
    _validate_request,
    _probability_branch_arithmetic_error,
    _repair_probability_branch_draft,
    _question_type_shape_error,
    _question_from_draft,
    _register_batch_draft,
    _release_batch_draft,
    _seal_safety_blueprint,
    _safe_blueprint_verification_error,
    _safety_blueprint,
    _select_safety_blueprint,
    _slot_and_references,
    _verification_messages,
    _verification_solution_process_error,
    _verification_consistency_error,
    cancel_personalized_batch,
    commit_personalized_batch,
    gateway_status,
    generate_personalized_draft,
    generate_personalized_question,
    save_personalized_review,
    verify_personalized_draft,
)


class LivePersonalizedGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.bank = Path(self.temp.name)
        (self.bank / "tags").mkdir()
        (self.bank / "generation").mkdir()
        references = []
        bank_rows = []
        for index in range(5):
            question_id = f"source_q{index + 1:03d}"
            source = {
                "question_id": question_id,
                "display_id": f"SOURCE-{index + 1}",
                "source_exam": "演示题库",
                "question_number": index + 1,
                "question_type": "single_choice",
                "primary_knowledge": "函数性质",
                "stem_markdown": f"参考原题 {index + 1}：判断函数性质。",
                "answer": "A",
                "solution_markdown": f"参考原题 {index + 1} 的完整解析。",
            }
            references.append(source)
            bank_rows.append({**source, "tags": {"primary_knowledge": "函数性质"}})
        self.references = references
        (self.bank / "tags" / "all_question_tags.json").write_text(
            json.dumps(bank_rows, ensure_ascii=False), encoding="utf-8"
        )
        (self.bank / "generation" / "student_generated_question_candidates.json").write_text(
            json.dumps(
                {
                    "schema_version": "generated-question-candidate-pool-v1",
                    "slots": [
                        {
                            "slot_id": "S001-functions",
                            "scope": "student_practice",
                            "student_id": "S001",
                            "primary_knowledge": "函数性质",
                            "question_type": "single_choice",
                            "reference_questions": references,
                            "reference_question_ids": [row["question_id"] for row in references],
                            "candidates": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.env = patch.dict(
            os.environ,
            {
                "CHERRY_STUDIO_BASE_URL": "http://127.0.0.1:23333",
                "CHERRY_STUDIO_API_KEY": "cs-sk-test",
                "CHERRY_STUDIO_MODEL": "demo:model",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_every_workbench_knowledge_and_type_has_five_exact_distinct_families(self) -> None:
        matrix = (
            ("导数与微分·求导运算·基本函数求导", "single_choice", "derivative"),
            ("导数应用·不等式证明·构造函数证明", "single_choice", "inequality"),
            ("概率统计·概率模型·全概率公式", "single_choice", "total_probability"),
            ("导数与微分·切线方程·求切线", "single_choice", "tangent"),
            ("函数综合·函数性质·奇偶性与周期性", "single_choice", "parity_periodicity"),
            ("导数与微分·导数定义·平均变化率与瞬时变化率", "single_choice", "derivative_definition"),
            ("导数应用·极值与最值·求最值", "fill_blank", "optimum"),
            ("导数应用·极值与最值·求极值", "single_choice", "extremum_single"),
            ("导数应用·极值与最值·求极值", "multiple_choice", "extremum"),
            ("导数应用·极值与最值·求极值", "fill_blank", "extremum_fill"),
            ("导数应用·极值与最值·求极值", "solution", "extremum_solution"),
            ("导数应用·单调性·判断单调区间", "fill_blank", "monotonicity"),
            ("导数应用·零点问题·函数零点个数", "solution", "zeros"),
            ("概率统计·数字特征·期望与方差", "solution", "expectation_variance"),
        )
        for knowledge, question_type, prefix in matrix:
            self.assertGreaterEqual(SAFETY_FAMILY_COUNTS[prefix], 5)
            structures: set[str] = set()
            for family in range(5):
                blueprint = _safety_blueprint(
                    {
                        "knowledge": knowledge,
                        "slot_index": family,
                        "attempt": 1,
                        "batch_id": "matrix-test",
                        "_family_override": family,
                    },
                    question_type,
                )
                self.assertIsNotNone(blueprint, (knowledge, question_type, family))
                assert blueprint is not None
                self.assertEqual(blueprint["question_type"], question_type)
                self.assertTrue(blueprint["stem_markdown"])
                self.assertTrue(blueprint["answer"])
                self.assertTrue(blueprint["solution_markdown"])
                self.assertTrue(str(blueprint["family_id"]).startswith(prefix + "-"))
                if question_type in {"single_choice", "multiple_choice"}:
                    self.assertEqual(set(blueprint["options"]), set("ABCD"))
                structures.add(_number_agnostic_structure(blueprint["stem_markdown"]))
            self.assertEqual(len(structures), 5, (knowledge, structures))

    def test_inequality_auto_three_slots_use_stable_type_plan_and_explicit_type_wins(self) -> None:
        knowledge = "导数应用·不等式证明·构造函数证明"
        pool_path = self.bank / "generation" / "student_generated_question_candidates.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        for slot in pool["slots"]:
            slot["primary_knowledge"] = knowledge
            slot["reference_questions"] = [
                {**row, "primary_knowledge": knowledge} for row in self.references
            ]
        pool_path.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")

        auto_types = []
        for slot_index in range(3):
            slot, _ = _slot_and_references(
                self.bank,
                "KNOWLEDGE",
                knowledge,
                3,
                mode="knowledge",
                question_type="auto",
                slot_index=slot_index,
            )
            auto_types.append(slot["question_type"])
        self.assertEqual(auto_types, ["single_choice", "solution", "fill_blank"])

        for explicit_type in ("single_choice", "multiple_choice", "fill_blank", "solution"):
            slot, _ = _slot_and_references(
                self.bank,
                "KNOWLEDGE",
                knowledge,
                3,
                mode="knowledge",
                question_type=explicit_type,
                slot_index=1,
            )
            self.assertEqual(slot["question_type"], explicit_type)

    def test_probability_recipe_requires_linked_tasks_and_feedback_changes_structure(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "概率统计·概率模型·全概率公式",
            "question_type": "solution",
            "difficulty": "matched",
            "slot_index": 0,
            "attempt": 2,
        }
        recipe = _dynamic_structure_recipe(request, "solution")
        self.assertTrue(str(recipe["recipe_id"]).startswith("total-probability-"))
        self.assertIn("（1）（2）", recipe["contract"])
        self.assertNotIn("导数", json.dumps(recipe, ensure_ascii=False))
        recovery = _feedback_recovery_contract(
            request,
            "solution",
            ["目标为同步，但当前仍是一步基础题"],
        )
        self.assertTrue(recovery["active"])
        self.assertTrue(any("全概率" in rule and "后验概率" in rule for rule in recovery["mandatory_changes"]))

    def test_probability_arithmetic_gate_rejects_consistent_model_miscalculation(self) -> None:
        stem = (
            "某工厂生产三种型号的零件：型号A占总产量的40%，其合格率为90%；"
            "型号B占35%，合格率为85%；型号C占25%，合格率为80%。\n\n"
            "（1）求该零件为合格品的概率；\n\n"
            "（2）若已知该零件为合格品，求它来自型号B的概率。"
        )
        wrong = _probability_branch_arithmetic_error(
            "概率统计·概率模型·全概率公式",
            stem,
            "0.8575; 0.3478",
            "由全概率与贝叶斯公式计算。",
        )
        self.assertIn("第（2）问", wrong)
        self.assertIn("17/49", wrong)
        self.assertEqual(
            _probability_branch_arithmetic_error(
                "概率统计·概率模型·全概率公式",
                stem,
                r"\frac{343}{400}; \frac{17}{49}",
                r"先得 $343/400$，再得 $17/49$。",
            ),
            "",
        )
        draft = {"stem_markdown": stem, "answer": "wrong", "solution_markdown": "wrong"}
        self.assertTrue(_repair_probability_branch_draft("概率统计·概率模型·全概率公式", draft))
        self.assertEqual(draft["answer"], r"\frac{343}{400}；\frac{17}{49}")
        self.assertEqual(
            _probability_branch_arithmetic_error(
                "概率统计·概率模型·全概率公式",
                stem,
                draft["answer"],
                draft["solution_markdown"],
            ),
            "",
        )

    def test_probability_gate_ignores_variable_prior_models_it_cannot_prove(self) -> None:
        stem = (
            "甲盒中红球占70%，白球占30%；乙盒中红球占40%，白球占60%。"
            "选甲盒的概率为x，选乙盒的概率为1-x。（1）求x；（2）求条件概率。"
        )
        self.assertEqual(
            _probability_branch_arithmetic_error(
                "概率统计·概率模型·全概率公式", stem, "0.7；0.8", "解析"
            ),
            "",
        )

    def test_probability_repair_supports_grouped_respectively_wording(self) -> None:
        stem = (
            "某工厂有三条生产线，生产线甲、乙、丙的产量占比分别为30%、40%、30%，"
            "其不合格率分别为5%、3%、4%。（1）求不合格概率；"
            "（2）已知不合格，求来自生产线乙的概率。"
        )
        draft = {"stem_markdown": stem, "answer": "0.038；0.63", "solution_markdown": "待核算"}
        self.assertTrue(_repair_probability_branch_draft("概率统计·概率模型·全概率公式", draft))
        self.assertEqual(draft["answer"], r"\frac{39}{1000}；\frac{4}{13}")
        self.assertEqual(
            _probability_branch_arithmetic_error(
                "概率统计·概率模型·全概率公式", stem, draft["answer"], draft["solution_markdown"]
            ),
            "",
        )

    def test_inequality_fill_blank_has_twelve_distinct_challenge_blueprints(self) -> None:
        knowledge = "导数应用·不等式证明·构造函数证明"
        structures: set[str] = set()
        families: set[str] = set()
        for family in range(SAFETY_FAMILY_COUNTS["inequality_fill"]):
            blueprint = _safety_blueprint(
                {
                    "knowledge": knowledge,
                    "slot_index": family,
                    "attempt": 1,
                    "diversity_round": 1,
                    "batch_size": 3,
                    "batch_id": "inequality-fill-blank-matrix",
                    "difficulty": "challenge",
                    "_family_override": family,
                },
                "fill_blank",
            )
            self.assertIsNotNone(blueprint, family)
            assert blueprint is not None
            self.assertEqual(blueprint["question_type"], "fill_blank")
            self.assertIn("______", blueprint["stem_markdown"])
            self.assertTrue(blueprint["answer"])
            self.assertTrue(blueprint["solution_markdown"])
            self.assertFalse(
                _difficulty_gate_error(
                    "challenge",
                    "fill_blank",
                    blueprint["stem_markdown"],
                    blueprint["solution_markdown"],
                ),
                blueprint["family_id"],
            )
            structures.add(_number_agnostic_structure(blueprint["stem_markdown"]))
            families.add(str(blueprint["family_id"]))
        self.assertEqual(len(structures), 12)
        self.assertEqual(len(families), 12)

    @staticmethod
    def draft(stem: str, answer: str = "B") -> dict:
        return {
            "question_type": "single_choice",
            "stem_markdown": stem,
            "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
            "answer": answer,
            "solution_markdown": "由周期性与奇偶性综合判断，答案为 B。",
            "changed_dimensions": ["question_angle", "reasoning_path"],
            "design_summary": "综合周期性与奇偶性。",
        }

    @staticmethod
    def verification(answer: str = "B") -> dict:
        return {
            "status": "passed",
            "answer": answer,
            "solution_markdown": "只根据新题题面独立求解，得到答案 B。",
            "notes": "选项唯一，定义完整。",
        }

    def request(self, reference_count: int = 5) -> dict:
        return {
            "student_id": "S001",
            "student_name": "测试学生",
            "knowledge": "函数性质",
            "question_type": "single_choice",
            "difficulty": "auto",
            "focus": "迁移应用",
            "reference_count": reference_count,
        }

    def test_requested_output_type_is_decoupled_from_reference_slot_type(self) -> None:
        slot, references = _slot_and_references(
            self.bank,
            "S001",
            self.request()["knowledge"],
            3,
            mode="knowledge",
            question_type="solution",
            slot_index=0,
        )
        self.assertEqual(slot["source_question_type"], "single_choice")
        self.assertEqual(slot["question_type"], "solution")
        self.assertEqual(len(references), 3)
        self.assertEqual(len({row["question_id"] for row in references}), 3)

    def test_auto_question_type_diversifies_batch_slots(self) -> None:
        types = []
        for slot_index in range(5):
            slot, _ = _slot_and_references(
                self.bank,
                "S001",
                self.request()["knowledge"],
                3,
                mode="knowledge",
                question_type="auto",
                slot_index=slot_index,
            )
            types.append(slot["question_type"])
        self.assertGreaterEqual(len(set(types[:3])), 3)
        self.assertGreaterEqual(len(set(types)), 4)

    def test_explicit_question_type_is_not_overridden_by_auto_rotation(self) -> None:
        types = {
            _slot_and_references(
                self.bank,
                "S001",
                self.request()["knowledge"],
                3,
                mode="knowledge",
                question_type="multiple_choice",
                slot_index=slot_index,
            )[0]["question_type"]
            for slot_index in range(5)
        }
        self.assertEqual(types, {"multiple_choice"})

    def test_generation_prompt_has_one_fulltext_anchor_and_metadata_only_evidence(self) -> None:
        references = [
            {
                **row,
                "stem_markdown": f"FULL_STEM_SECRET_{index}",
                "answer": f"FULL_ANSWER_SECRET_{index}",
                "solution_markdown": f"FULL_SOLUTION_SECRET_{index}",
            }
            for index, row in enumerate(self.references[:3], 1)
        ]
        request = {
            **self.request(3),
            "mode": "student",
            "batch_id": "single-anchor-prompt-test",
            "batch_size": 3,
            "slot_index": 0,
            "attempt": 1,
            "diversity_round": 1,
        }
        slot = {
            "question_type": "single_choice",
            "source_question_type": "single_choice",
            "primary_knowledge": request["knowledge"],
        }
        task_spec = _build_generation_task_spec(self.bank, request, slot, references)
        messages = _generation_messages(request, slot, references, [], [], None, task_spec)
        payload = json.loads(messages[1]["content"])

        anchor = payload["single_anchor_question_teacher_only"]
        self.assertEqual(anchor["question_id"], references[0]["question_id"])
        self.assertEqual(anchor["stem_markdown"], "FULL_STEM_SECRET_1")
        self.assertEqual(anchor["answer"], "FULL_ANSWER_SECRET_1")
        self.assertEqual(anchor["solution_excerpt"], "FULL_SOLUTION_SECRET_1")

        forbidden_fulltext_keys = {"stem_markdown", "answer", "solution_markdown", "solution_excerpt"}
        evidence = payload["diagnostic_evidence_summaries"]
        self.assertEqual(len(evidence), 3)
        self.assertTrue(all(not forbidden_fulltext_keys.intersection(row) for row in evidence))
        task_evidence = payload["generation_task_spec"]["diagnostic_profile"]["evidence_summaries"]
        self.assertTrue(all(not forbidden_fulltext_keys.intersection(row) for row in task_evidence))

        serialized = json.dumps(payload, ensure_ascii=False)
        for index in (2, 3):
            self.assertNotIn(f"FULL_STEM_SECRET_{index}", serialized)
            self.assertNotIn(f"FULL_ANSWER_SECRET_{index}", serialized)
            self.assertNotIn(f"FULL_SOLUTION_SECRET_{index}", serialized)

    def test_five_diagnostic_references_rotate_distinct_anchors_across_three_slots(self) -> None:
        anchor_ids = []
        task_ids = []
        for slot_index in range(3):
            slot, references = _slot_and_references(
                self.bank,
                "S001",
                self.request()["knowledge"],
                5,
                mode="student",
                question_type="single_choice",
                slot_index=slot_index,
            )
            request = {
                **self.request(5),
                "mode": "student",
                "batch_id": "five-evidence-three-anchor-test",
                "batch_size": 3,
                "slot_index": slot_index,
                "attempt": 1,
                "diversity_round": 1,
            }
            task_spec = _build_generation_task_spec(self.bank, request, slot, references)
            anchor_ids.append(task_spec["diagnostic_anchor_question_id"])
            task_ids.append(task_spec["task_spec_id"])
            self.assertEqual(task_spec["structure_anchor"]["kind"], "mother_question")
            self.assertEqual(task_spec["structure_anchor"]["id"], references[0]["question_id"])
            self.assertEqual(task_spec["diagnostic_profile"]["evidence_count"], 5)

        self.assertEqual(anchor_ids, ["source_q001", "source_q002", "source_q003"])
        self.assertEqual(len(set(anchor_ids)), 3)
        self.assertEqual(len(set(task_ids)), 3)

    def test_challenge_rejects_one_step_unconstrained_quadratic_optimum(self) -> None:
        stem = r"函数 $f(x)=x^2-6x+10$ 在 $\mathbb R$ 上的最小值为______。"
        solution = r"配方得 $f(x)=(x-3)^2+1$，故最小值为 $1$。"
        error = _difficulty_gate_error("challenge", "fill_blank", stem, solution)
        evidence = _difficulty_gate_evidence("challenge", "fill_blank", stem, solution)
        self.assertIn("目标为挑战", error)
        self.assertTrue(evidence["simple_one_step_pattern"])
        self.assertEqual(set(evidence), {"target", "target_label", "signals", "simple_one_step_pattern"})

    def test_request_preserves_exact_target_level(self) -> None:
        request = _validate_request(
            {
                "mode": "knowledge",
                "knowledge": "导数应用·极值与最值·求极值",
                "question_type": "solution",
                "difficulty": "challenge",
                "target_level": 5,
                "focus": "auto",
                "reference_count": 3,
            }
        )
        self.assertEqual(request["target_level"], 5)

    def test_exact_level_rejects_basic_derivative_as_high_level(self) -> None:
        stem = (
            r"设 $x>0$，函数 $f(x)=\sqrt{x}+\frac1x$，则 $f'(x)=（ ）$。"
            "\nA. $\\frac1{2\\sqrt{x}}-\\frac1{x^2}$"
            "\nB. $\\frac1{\\sqrt{x}}+\\frac1{x^2}$"
            "\nC. $\\frac1{2\\sqrt{x}}+\\frac1{x^2}$"
            "\nD. $2\\sqrt{x}-\\frac1x$"
        )
        solution = r"由幂函数求导得 $f'(x)=\frac1{2\sqrt{x}}-\frac1{x^2}$，故选 A。"
        high_error = _difficulty_gate_error("challenge", "single_choice", stem, solution, target_level=5)
        low_error = _difficulty_gate_error("consolidation", "single_choice", stem, solution, target_level=2)
        evidence = _difficulty_gate_evidence("challenge", "single_choice", stem, solution, target_level=5)
        self.assertTrue(high_error)
        self.assertEqual(low_error, "")
        self.assertTrue(evidence["basic_derivative_task"])
        self.assertEqual(evidence["target_level"], 5)
        self.assertEqual(evidence["exact_level_contract"]["target_level"], 5)

    def test_challenge_accepts_parameter_interval_case_analysis_optimum(self) -> None:
        stem = (
            r"已知实数 $a$，函数 $f_a(x)=\ln x+a/x$ 在区间 $[1,e]$ 上。"
            r"讨论 $a$ 的取值，并求 $f_a(x)$ 的最小值。"
        )
        solution = (
            r"构造导函数并按参数分类讨论驻点是否落在区间内，再比较驻点与两个端点；"
            r"因此分三种情况得到参数范围，从而确定最小值。"
        )
        evidence = _difficulty_gate_evidence("challenge", "solution", stem, solution)
        self.assertEqual(_difficulty_gate_error("challenge", "solution", stem, solution), "")
        self.assertEqual(set(evidence), {"target", "target_label", "signals", "simple_one_step_pattern"})
        self.assertGreaterEqual(len(evidence["signals"]), 3)

    def test_consolidation_accepts_one_step_quadratic_optimum(self) -> None:
        stem = r"函数 $f(x)=x^2-6x+10$ 在 $\mathbb R$ 上的最小值为______。"
        solution = r"配方得 $f(x)=(x-3)^2+1$，故最小值为 $1$。"
        self.assertEqual(_difficulty_gate_error("consolidation", "fill_blank", stem, solution), "")

    def test_challenge_does_not_seal_basic_optimum_blueprint(self) -> None:
        blueprint = _select_safety_blueprint(
            self.bank,
            {
                **self.request(3),
                "knowledge": "导数应用·极值与最值·求最值",
                "difficulty": "challenge",
                "question_type": "fill_blank",
                "mode": "knowledge",
                "batch_id": "difficulty-blueprint-test",
                "batch_size": 3,
                "slot_index": 0,
                "diversity_round": 1,
            },
            "fill_blank",
        )
        self.assertIsNone(blueprint)

    def test_verifier_receives_target_difficulty_contract(self) -> None:
        messages = _verification_messages(
            "fill_blank",
            r"函数 $f(x)=x^2$ 的最小值为______。",
            {},
            target_difficulty="challenge",
            knowledge="导数应用·极值与最值·求最值",
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["target_difficulty"], "挑战")
        self.assertEqual(payload["difficulty_contract"]["target_score"], "4–5/5")
        self.assertIn("简单二次函数", payload["difficulty_contract"]["forbidden"])

    def test_verifier_receives_exact_target_level_contract(self) -> None:
        messages = _verification_messages(
            "single_choice",
            r"设 $x>0$，函数 $f(x)=\sqrt{x}+\frac1x$，则 $f'(x)=（ ）$。",
            {"A": "1", "B": "2", "C": "3", "D": "4"},
            target_difficulty="challenge",
            target_level=5,
            knowledge="导数与微分·求导运算",
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["target_difficulty"], "5级")
        self.assertEqual(payload["difficulty_contract"]["target_score"], "5/5")
        self.assertEqual(payload["difficulty_contract"]["exact_level"], 5)
        self.assertGreaterEqual(payload["difficulty_contract"]["minimum_complexity_signals"], 3)

    def test_safe_blueprint_verifier_only_judges_mathematical_correctness(self) -> None:
        messages = _verification_messages(
            "fill_blank",
            r"设参数 $a$，若题设恒成立，则 $a=$______。",
            {},
            target_difficulty="challenge",
            knowledge="导数应用·不等式证明·构造函数证明",
            enforce_difficulty=False,
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["verification_scope"], "mathematical_correctness_only")
        self.assertEqual(payload["target_difficulty"], "由本地硬门禁校验")
        self.assertIn("不得因为主观认为题目不够难", messages[0]["content"])
        self.assertIn("status 必须为 passed", messages[0]["content"])

    def test_generates_valid_live_question_and_records_teacher_provenance(self) -> None:
        with patch(
            "live_personalized_generation._gateway_chat_json",
            side_effect=[
                self.draft("设函数同时满足新的周期与对称条件，求指定函数值。\n\nA. 1\nB. 2\nC. 3\nD. 4"),
                self.verification(),
            ],
        ):
            result = generate_personalized_question(self.bank, self.request())
        question = result["question"]
        self.assertEqual(result["mode"], "cherry_studio_live")
        self.assertEqual(question["generation"]["generator"], "cherry-studio-api-gateway-live-v1")
        self.assertEqual(question["generation"]["draft_source"], "cherry_studio_model")
        self.assertEqual(question["generation"]["model_role"], "draft_and_verifier")
        self.assertEqual(question["verification"]["provider"], "cherry-studio-api-gateway")
        self.assertEqual(question["generation"]["reference_count"], 5)
        self.assertEqual(len(set(question["generation"]["reference_question_ids"])), 5)
        self.assertEqual(question["generation"]["strategy"], "single_anchor_task_spec")
        self.assertEqual(
            question["generation"]["diagnostic_evidence_question_ids"],
            question["generation"]["reference_question_ids"],
        )
        self.assertEqual(question["generation"]["diagnostic_evidence_count"], 5)
        self.assertEqual(question["generation"]["fulltext_anchor_count"], 1)
        self.assertEqual(question["generation"]["structure_anchor"]["kind"], "mother_question")
        self.assertEqual(
            question["generation"]["structure_anchor"]["id"],
            question["generation"]["mother_question_id"],
        )
        self.assertEqual(question["generation"]["task_spec"]["schema_version"], "generation-task-spec-v2")
        self.assertEqual(
            question["generation"]["task_spec_id"],
            question["generation"]["task_spec"]["task_spec_id"],
        )
        self.assertEqual(question["teacher_review"]["status"], "pending")
        history = (self.bank / "generation" / "live_personalized_generation_history.jsonl").read_text(
            encoding="utf-8"
        )
        self.assertIn(question["question_id"], history)
        self.assertIn("reference_questions", history)

    def test_answer_mismatch_uses_two_independent_checks_and_corrects_draft(self) -> None:
        with patch(
            "live_personalized_generation._gateway_chat_json",
            side_effect=[
                self.draft("第一次生成的全新函数题。\nA. 1\nB. 2\nC. 3\nD. 4", answer="A"),
                self.verification(answer="B"),
                self.verification(),
            ],
        ) as gateway:
            result = generate_personalized_question(self.bank, self.request(3), max_attempts=2)
        self.assertEqual(result["attempt"], 1)
        self.assertEqual(result["question"]["answer"], "B")
        self.assertTrue(result["question"]["generation"]["draft_answer_corrected"])
        self.assertEqual(result["question"]["verification"]["consensus_checks"], 2)
        self.assertEqual(gateway.call_count, 3)

    def test_safety_blueprint_mismatch_requires_second_check_to_match_blueprint(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "导数应用·极值与最值·求极值",
            "question_type": "multiple_choice",
            "batch_id": "blueprint-adjudication-test",
            "slot_index": 0,
        }
        draft = _extremum_blueprint({**request, "_family_override": 8}, "multiple_choice")
        draft["_safety_blueprint_family"] = draft["family_id"]
        first = {
            "status": "passed",
            "answer": "AB",
            "solution_markdown": "A、B 正确，故选 AB。",
            "notes": "第一次审题漏判 C。",
        }
        second = {
            "status": "passed",
            "answer": "ABC",
            "solution_markdown": draft["solution_markdown"],
            "notes": "逐项核对完成。",
        }
        slot = {
            "question_type": "multiple_choice",
            "primary_knowledge": request["knowledge"],
            "reference_questions": self.references,
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json", side_effect=[first, second]):
                result = verify_personalized_draft(self.bank, {**request, "draft": draft})
        self.assertEqual(result["question"]["answer"], "ABC")
        self.assertEqual(result["question"]["verification"]["consensus_checks"], 2)
        self.assertFalse(result["question"]["generation"]["draft_answer_corrected"])

    def test_safe_blueprint_retries_a_failed_cherry_verdict_before_rejecting_question(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "导数与微分·求导运算·基本函数求导",
            "question_type": "single_choice",
            "batch_id": "blueprint-failed-verdict-retry",
            "slot_index": 0,
        }
        draft = _safety_blueprint(
            {**request, "_family_override": 0},
            "single_choice",
        )
        assert draft is not None
        draft["_safety_blueprint_family"] = draft["family_id"]
        failed = {
            "status": "failed",
            "answer": "",
            "solution_markdown": "",
            "notes": "第一次响应未完成计算。",
        }
        passed = {
            "status": "passed",
            "answer": draft["answer"],
            "solution_markdown": draft["solution_markdown"],
            "notes": "重新独立求解后确认。",
        }
        slot = {
            "question_type": "single_choice",
            "primary_knowledge": request["knowledge"],
            "reference_questions": self.references,
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json", side_effect=[failed, passed]) as gateway:
                result = verify_personalized_draft(self.bank, {**request, "draft": draft})
        self.assertEqual(gateway.call_count, 2)
        self.assertEqual(result["question"]["verification"]["status"], "passed")
        self.assertEqual(result["question"]["verification"]["consensus_checks"], 2)

    def test_choice_verification_rejects_answer_solution_contradiction(self) -> None:
        self.assertEqual(_choice_answer_labels("答案：A、C、D"), ("A", "C", "D"))
        error = _verification_consistency_error(
            "single_choice",
            {"A": "1", "B": "2", "C": "3", "D": "4"},
            "B",
            "逐项核对后可知 D 正确，故选 D。",
        )
        self.assertIn("答案与其解析最终结论矛盾", error)

    def test_safe_verification_recovers_explicit_final_choice_and_equivalent_fraction_style(self) -> None:
        draft = {
            "question_type": "single_choice",
            "options": {"A": "$y=2x$", "B": "$y=x+1$", "C": "$y=2x+1$", "D": "$y=2x-1$"},
            "answer": "A",
            "solution_markdown": "求导并代入切点，故选 A。",
        }
        verification = {
            "status": "passed",
            "answer": "B",
            "solution_markdown": "切线斜率为 2，方程为 $y=2x$，故选 A。",
            "notes": "答案字段误写，但最终推导明确。",
        }
        self.assertFalse(
            _safe_blueprint_verification_error(
                verification,
                draft,
                "single_choice",
                "导数与微分·切线方程·求切线",
            )
        )
        self.assertEqual(verification["answer"], "A")
        self.assertTrue(
            _answers_match(
                r"E(X)=\frac13,\quad D(X)=\frac29",
                r"E(X)=\frac{1}{3}, D(X)=\frac{2}{9}",
            )
        )
        self.assertTrue(_answers_match("$1$", "a=1"))
        self.assertTrue(_answers_match("$[0,1]$", r"a\in[0,1]"))
        self.assertTrue(_answers_match(r"$\frac12$", r"a 的最小值为 \frac{1}{2}"))
        self.assertTrue(_answers_match("$4$", r"0<a\leq4"))
        self.assertTrue(_answers_match(r"$\frac{2}{\pi^2}$", r"a\leq\frac{2}{\pi^2}"))
        self.assertTrue(_answers_match("$[0,1]$", r"0\leq a\leq1"))
        self.assertTrue(_answers_match(r"$\frac12$", "1/2"))
        self.assertTrue(_answers_match(r"$\frac12$", "0.5"))
        self.assertTrue(
            _answers_match(r"$\frac{4(\pi-2)}{\pi^3}$", "4(π−2)/π³")
        )
        self.assertFalse(
            _answers_match(r"$\frac{4(\pi-2)}{\pi^3}$", "4(π+2)/π³")
        )
        self.assertFalse(_answers_match(r"a\leq1", r"a\geq1"))

    def test_safe_verification_uses_proven_solution_when_model_narrates_a_retry(self) -> None:
        draft = {
            "question_type": "solution",
            "options": {},
            "answer": r"$\frac1e$",
            "solution_markdown": "由中值定理与端点极限可知下确界为 $1/e$。",
        }
        verification = {
            "status": "passed",
            "answer": r"\frac{1}{e}",
            "solution_markdown": "重新审视端点后，由中值定理得到答案 $1/e$。",
            "notes": "独立答案成立。",
        }
        self.assertFalse(
            _safe_blueprint_verification_error(
                verification,
                draft,
                "solution",
                "导数应用·不等式证明·构造函数证明",
            )
        )
        self.assertEqual(verification["solution_markdown"], draft["solution_markdown"])
        self.assertIn("已证明蓝图收口", verification["notes"])

    def test_multiple_choice_requires_at_least_two_correct_options(self) -> None:
        error = _verification_consistency_error(
            "multiple_choice",
            {"A": "1", "B": "2", "C": "3", "D": "4"},
            "B",
            "故选 B。",
        )
        self.assertIn("少于两个正确选项", error)

    def test_knowledge_mode_does_not_require_student(self) -> None:
        request = {
            "mode": "knowledge",
            "knowledge": "函数性质",
            "question_type": "single_choice",
            "difficulty": "auto",
            "focus": "概念辨析",
            "reference_count": 3,
        }
        with patch(
            "live_personalized_generation._gateway_chat_json",
            side_effect=[
                self.draft("按知识点直接生成的全新函数题。\nA. 1\nB. 2\nC. 3\nD. 4"),
                self.verification(),
            ],
        ):
            result = generate_personalized_question(self.bank, request)
        self.assertEqual(result["question"]["student_id"], "KNOWLEDGE")
        self.assertEqual(result["question"]["scope"], "knowledge_practice")
        self.assertEqual(result["question"]["generation"]["mode"], "knowledge")

    def test_two_stage_api_returns_draft_before_independent_verification(self) -> None:
        request = self.request(3)
        draft = self.draft("先展示、再校验的全新函数题。\nA. 1\nB. 2\nC. 3\nD. 4")
        with patch("live_personalized_generation._gateway_chat_json", return_value=draft) as create:
            draft_result = generate_personalized_draft(self.bank, request)
        self.assertEqual(draft_result["status"], "draft")
        self.assertEqual(draft_result["draft_source"], "cherry_studio_model")
        self.assertEqual(draft_result["reference_count"], 3)
        self.assertEqual(create.call_args.kwargs["max_tokens"], 2800)
        with patch(
            "live_personalized_generation._gateway_chat_json",
            return_value=self.verification(),
        ) as verify:
            result = verify_personalized_draft(
                self.bank,
                {**request, "attempt": 1, "draft": draft_result["draft"]},
            )
        self.assertEqual(result["question"]["verification"]["status"], "passed")
        self.assertEqual(verify.call_args.kwargs["max_tokens"], 2400)

    def test_answer_label_and_markdown_are_removed_before_persisting(self) -> None:
        draft = self.draft("答案格式清洗测试题。\nA. 1\nB. 2\nC. 3\nD. 4", answer="**答案：**B")
        verification = self.verification(answer="答案：B")
        with patch(
            "live_personalized_generation._gateway_chat_json",
            side_effect=[draft, verification],
        ):
            result = generate_personalized_question(self.bank, self.request(3))
        self.assertEqual(result["question"]["answer"], "B")
        self.assertNotIn("答案", result["question"]["answer"])
        self.assertNotIn("**", result["question"]["answer"])
        self.assertEqual(_clean_answer_content(r"**答案：**-\frac{5}{2}"), r"-\frac{5}{2}")

    def test_monotonicity_prompt_forbids_pointwise_global_property_confusion(self) -> None:
        constraints = "".join(_knowledge_safety_constraints("导数应用·单调性·判断单调区间"))
        self.assertIn("不得求‘使函数在某区间单调的自变量 x’", constraints)
        self.assertIn("导数零点", constraints)

    def test_monotonicity_answer_allows_endpoint_convention_difference(self) -> None:
        self.assertTrue(_answers_match(r"(-\infty, \ln 2]", r"(-\infty,\ln 2)", "判断单调区间"))
        self.assertFalse(_answers_match("(0, 2)", "(0, 3)", "判断单调区间"))

    def test_monotonicity_blueprints_are_exact_and_different_by_slot(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "导数应用·单调性·判断单调区间",
            "attempt": 1,
        }
        blueprints = [_monotonicity_blueprint({**request, "slot_index": index}, "fill_blank") for index in range(3)]
        stems = [blueprint["stem_markdown"] for blueprint in blueprints if blueprint]
        self.assertEqual(len(set(stems)), 3)
        self.assertTrue(all("单调递减区间" in stem for stem in stems))
        self.assertNotIn("x--", "".join(blueprint["solution_markdown"] for blueprint in blueprints if blueprint))
        batch_variants = {
            _monotonicity_blueprint({**request, "slot_index": 0, "batch_id": f"batch-{index}"}, "fill_blank")[
                "stem_markdown"
            ]
            for index in range(10)
        }
        self.assertGreater(len(batch_variants), 1)
        retry = _monotonicity_blueprint({**request, "slot_index": 0, "attempt": 2}, "fill_blank")
        self.assertEqual(blueprints[0]["family_id"], retry["family_id"])
        self.assertNotEqual(blueprints[0]["stem_markdown"], retry["stem_markdown"])

    def test_safety_blueprint_seals_model_drift(self) -> None:
        blueprint = _monotonicity_blueprint(
            {
                **self.request(3),
                "knowledge": "导数应用·单调性·判断单调区间",
                "slot_index": 1,
                "attempt": 1,
            },
            "fill_blank",
        )
        draft = self.draft("模型擅自改写的错误题干。", answer="错误答案")
        _seal_safety_blueprint(draft, blueprint)
        self.assertEqual(draft["stem_markdown"], blueprint["stem_markdown"])
        self.assertEqual(draft["answer"], blueprint["answer"])
        self.assertEqual(draft["solution_markdown"], blueprint["solution_markdown"])

    def test_safe_blueprint_skips_freeform_generation_call(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "导数应用·单调性·判断单调区间",
            "batch_id": "batch-json-blueprint-test",
            "slot_index": 0,
        }
        slot = {
            "question_type": "fill_blank",
            "primary_knowledge": request["knowledge"],
            "reference_questions": self.references,
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json") as gateway:
                result = generate_personalized_draft(self.bank, request)
        gateway.assert_not_called()
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["draft_source"], "skill_safety_blueprint")
        self.assertTrue(result["blueprint_family"])
        self.assertIn("单调递减区间", result["draft"]["stem_markdown"])
        _release_batch_draft(request["batch_id"], request["slot_index"])

    def test_second_diversity_round_rotates_to_new_exact_family(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "导数应用·单调性·判断单调区间",
            "batch_id": "batch-structural-regeneration",
            "slot_index": 0,
            "diversity_round": 2,
        }
        slot = {
            "question_type": "fill_blank",
            "primary_knowledge": request["knowledge"],
            "reference_questions": self.references,
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json") as gateway:
                result = generate_personalized_draft(self.bank, request)
        gateway.assert_not_called()
        self.assertEqual(result["draft_source"], "skill_safety_blueprint")
        self.assertEqual(result["diversity_round"], 2)
        self.assertEqual(result["blueprint_family"], "monotonicity-1")
        _release_batch_draft(request["batch_id"], request["slot_index"])

    def test_safe_blueprints_cover_all_structurally_distinct_rounds(self) -> None:
        monotonic_request = {
            **self.request(3),
            "knowledge": "导数应用·单调性·判断单调区间",
            "slot_index": 0,
            "batch_size": 1,
            "attempt": 1,
        }
        inequality_request = {
            **self.request(3),
            "knowledge": "导数应用·不等式证明·构造函数证明",
            "slot_index": 0,
            "batch_size": 1,
            "attempt": 1,
        }
        monotonic = [
            _monotonicity_blueprint({**monotonic_request, "diversity_round": round_}, "fill_blank")
            for round_ in range(1, 21)
        ]
        inequalities = [
            _inequality_blueprint({**inequality_request, "diversity_round": round_}, "single_choice")
            for round_ in range(1, 36)
        ]
        self.assertEqual(len({row["family_id"] for row in monotonic if row}), 20)
        self.assertEqual(len({row["family_id"] for row in inequalities if row}), 35)
        self.assertEqual(len({_number_agnostic_structure(row["stem_markdown"]) for row in monotonic if row}), 20)
        self.assertEqual(len({_number_agnostic_structure(row["stem_markdown"]) for row in inequalities if row}), 35)

    def test_extremum_blueprints_are_exact_multi_choice_questions(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "导数应用·极值与最值·求极值",
            "batch_id": "extremum-family-test",
            "batch_size": 3,
            "diversity_round": 1,
            "slot_index": 0,
            "attempt": 1,
        }
        rows = [
            _extremum_blueprint({**request, "_family_override": family}, "multiple_choice")
            for family in range(20)
        ]
        self.assertTrue(all(row is not None for row in rows))
        self.assertEqual(len({_number_agnostic_structure(row["stem_markdown"]) for row in rows}), 20)
        for row in rows:
            self.assertGreaterEqual(len(_choice_answer_labels(row["answer"])), 2)
            self.assertFalse(
                _verification_consistency_error(
                    row["question_type"], row["options"], row["answer"], row["solution_markdown"]
                )
            )
        first_batch = [
            _extremum_blueprint(
                {**request, "slot_index": slot, "_family_override": slot}, "multiple_choice"
            )
            for slot in range(3)
        ]
        self.assertEqual(len({tuple(row["changed_dimensions"]) for row in first_batch}), 3)
        self.assertEqual(len({row["stem_markdown"].splitlines()[0].split("$")[0] for row in first_batch}), 3)

    def test_inequality_uses_twenty_first_exact_family_before_model_fallback(self) -> None:
        knowledge = "导数应用·不等式证明·构造函数证明"
        history_rows = [
            {
                "student_id": "KNOWLEDGE",
                "knowledge": knowledge,
                "question": {
                    "question_type": "single_choice",
                    "generation": {"safety_blueprint_family": f"inequality-{family}"},
                },
            }
            for family in range(20)
        ]
        (self.bank / "generation" / "live_personalized_generation_history.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in history_rows) + "\n",
            encoding="utf-8",
        )
        request = {
            **self.request(3),
            "mode": "knowledge",
            "student_id": "",
            "knowledge": knowledge,
            "question_type": "single_choice",
            "batch_id": "batch-twenty-first-inequality-family",
            "slot_index": 0,
            "diversity_round": 21,
            "batch_size": 1,
        }
        slot = {
            "question_type": "single_choice",
            "primary_knowledge": knowledge,
            "reference_questions": self.references,
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json") as gateway:
                result = generate_personalized_draft(self.bank, request)
        gateway.assert_not_called()
        self.assertEqual(result["blueprint_family"], "inequality-20")
        _release_batch_draft(request["batch_id"], request["slot_index"])

    def test_browser_state_reset_selects_next_globally_unused_family(self) -> None:
        knowledge = "导数应用·单调性·判断单调区间"
        history = {
            "delivery_status": "committed",
            "student_id": "S001",
            "knowledge": knowledge,
            "question": {
                "question_type": "fill_blank",
                "generation": {"safety_blueprint_family": "monotonicity-0"},
            },
        }
        (self.bank / "generation" / "live_personalized_generation_history.jsonl").write_text(
            json.dumps(history, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        request = {
            **self.request(3),
            "knowledge": knowledge,
            "batch_id": "batch-reset-history",
            "slot_index": 0,
            "diversity_round": 1,
        }
        slot = {
            "question_type": "fill_blank",
            "primary_knowledge": knowledge,
            "reference_questions": self.references,
        }
        model_draft = {
            "question_type": "fill_blank",
            "stem_markdown": "设 $f(x)=xe^{-x}$，求其单调递增区间 ________。",
            "options": {},
            "answer": "(-\\infty,1]",
            "solution_markdown": "由导数符号得到。",
            "changed_dimensions": ["representation", "reasoning_path"],
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json", return_value=model_draft) as gateway:
                result = generate_personalized_draft(self.bank, request)
        gateway.assert_not_called()
        self.assertEqual(result["draft_source"], "skill_safety_blueprint")
        self.assertEqual(result["blueprint_family"], "monotonicity-1")
        _release_batch_draft(request["batch_id"], request["slot_index"])

    def test_global_family_history_is_not_truncated_after_five_hundred_rows(self) -> None:
        knowledge = "导数应用·单调性·判断单调区间"
        first_row = {
            "delivery_status": "committed",
            "student_id": "S001",
            "knowledge": knowledge,
            "question": {
                "question_type": "fill_blank",
                "generation": {"safety_blueprint_family": "monotonicity-0"},
            },
        }
        filler_rows = [
            {
                "student_id": f"OTHER-{index}",
                "knowledge": "other-knowledge",
                "question": {"question_type": "fill_blank", "generation": {}},
            }
            for index in range(510)
        ]
        (self.bank / "generation" / "live_personalized_generation_history.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in [first_row, *filler_rows]) + "\n",
            encoding="utf-8",
        )
        request = {
            **self.request(3),
            "knowledge": knowledge,
            "batch_id": "batch-history-over-five-hundred",
            "slot_index": 0,
            "diversity_round": 1,
        }
        slot = {
            "question_type": "fill_blank",
            "primary_knowledge": knowledge,
            "reference_questions": self.references,
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json") as gateway:
                result = generate_personalized_draft(self.bank, request)
        gateway.assert_not_called()
        self.assertEqual(result["blueprint_family"], "monotonicity-1")
        _release_batch_draft(request["batch_id"], request["slot_index"])

    def test_all_exact_families_used_falls_back_to_model_drafting(self) -> None:
        knowledge = "导数应用·单调性·判断单调区间"
        history_rows = [
            {
                "delivery_status": "committed",
                "student_id": "S001",
                "knowledge": knowledge,
                "question": {
                    "question_type": "fill_blank",
                    "generation": {"safety_blueprint_family": f"monotonicity-{family}"},
                },
            }
            for family in range(20)
        ]
        (self.bank / "generation" / "live_personalized_generation_history.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in history_rows) + "\n",
            encoding="utf-8",
        )
        request = {
            **self.request(3),
            "knowledge": knowledge,
            "batch_id": "batch-fallback-after-twenty-families",
            "slot_index": 0,
            "diversity_round": 1,
        }
        slot = {
            "question_type": "fill_blank",
            "primary_knowledge": knowledge,
            "reference_questions": self.references,
        }
        model_draft = {
            "question_type": "fill_blank",
            "stem_markdown": "Let $f(x)=xe^{-x}$. Find an increasing interval: ________.",
            "options": {},
            "answer": "(-\\infty,1]",
            "solution_markdown": "Use the derivative sign.",
            "changed_dimensions": ["representation", "reasoning_path"],
        }
        with patch("live_personalized_generation._slot_and_references", return_value=(slot, self.references[:3])):
            with patch("live_personalized_generation._gateway_chat_json", return_value=model_draft) as gateway:
                result = generate_personalized_draft(self.bank, request)
        gateway.assert_called_once()
        self.assertEqual(result["draft_source"], "cherry_studio_model")
        _release_batch_draft(request["batch_id"], request["slot_index"])

    def test_number_only_change_has_same_structure_and_is_rejected(self) -> None:
        previous_stem = "已知 $f(x)=x+\\frac{4}{x}$，求单调递减区间。"
        current_stem = "已知 $f(x)=x+\\frac{9}{x}$，求单调递减区间。"
        self.assertEqual(_number_agnostic_structure(previous_stem), _number_agnostic_structure(current_stem))
        self.assertEqual(_math_structure(previous_stem), _math_structure(current_stem))
        plain_interval = r"$x\in[\frac12,1]$"
        styled_interval = r"$x\in\left[\dfrac12,1\right]$"
        self.assertEqual(
            _number_agnostic_structure(plain_interval),
            _number_agnostic_structure(styled_interval),
        )
        self.assertEqual(_math_structure(plain_interval), _math_structure(styled_interval))
        history = {
            "delivery_status": "committed",
            "student_id": "KNOWLEDGE",
            "knowledge": "导数应用·单调性·判断单调区间",
            "question": {
                "question_id": "previous-number-swap",
                "student_id": "KNOWLEDGE",
                "primary_knowledge": "导数应用·单调性·判断单调区间",
                "question_type": "fill_blank",
                "stem_markdown": previous_stem,
                "generation": {},
            },
        }
        (self.bank / "generation" / "live_personalized_generation_history.jsonl").write_text(
            json.dumps(history, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        current = {
            "student_id": "KNOWLEDGE",
            "primary_knowledge": "导数应用·单调性·判断单调区间",
            "question_type": "fill_blank",
            "stem_markdown": current_stem,
            "generation": {},
        }
        errors = _history_similarity_errors(self.bank, current)
        self.assertTrue(any("仅替换数字" in error for error in errors))

    def test_inequality_blueprints_are_fast_exact_single_choice_questions(self) -> None:
        request = {
            **self.request(3),
            "knowledge": "导数应用·不等式证明·构造函数证明",
            "batch_id": "batch-inequality-blueprint-test",
            "attempt": 1,
        }
        blueprints = [_inequality_blueprint({**request, "slot_index": index}, "single_choice") for index in range(3)]
        self.assertEqual(len({row["family_id"] for row in blueprints if row}), 3)
        self.assertTrue(all(row["answer"] == "A" for row in blueprints if row))
        self.assertTrue(all("构造" in row["stem_markdown"] for row in blueprints if row))

    def test_inequality_challenge_single_choices_use_parameter_range_structures(self) -> None:
        request = {
            "knowledge": "导数应用·不等式证明·构造函数证明",
            "difficulty": "challenge",
            "batch_id": "batch-inequality-challenge-choice-test",
            "batch_size": 3,
            "slot_index": 0,
            "attempt": 1,
            "diversity_round": 1,
        }
        blueprints = [
            _inequality_challenge_choice_blueprint(
                {**request, "_family_override": family},
                "single_choice",
            )
            for family in range(SAFETY_FAMILY_COUNTS["inequality_challenge_choice"])
        ]
        self.assertTrue(all(blueprints))
        rows = [row for row in blueprints if row]
        self.assertEqual(len({_number_agnostic_structure(row["stem_markdown"]) for row in rows}), 6)
        for row in rows:
            self.assertEqual(row["question_type"], "single_choice")
            self.assertEqual(set(row["options"]), set("ABCD"))
            self.assertRegex(row["stem_markdown"], r"参数|取值范围|取值集合")
            self.assertIn("恒成立", row["stem_markdown"])
            self.assertEqual(
                _difficulty_gate_error(
                    "challenge",
                    "single_choice",
                    row["stem_markdown"],
                    row["solution_markdown"],
                ),
                "",
            )

    def test_inequality_solution_blueprints_are_exact_distinct_challenge_questions(self) -> None:
        request = {
            "knowledge": "导数应用·不等式证明·构造函数证明",
            "batch_id": "batch-inequality-solution-blueprint-test",
            "batch_size": 3,
            "slot_index": 0,
            "attempt": 1,
            "diversity_round": 1,
        }
        blueprints = [
            _inequality_solution_blueprint({**request, "_family_override": family}, "solution")
            for family in range(SAFETY_FAMILY_COUNTS["inequality_solution"])
        ]
        self.assertTrue(all(blueprints))
        rows = [row for row in blueprints if row]
        self.assertEqual(len({row["family_id"] for row in rows}), 12)
        self.assertEqual(len({_number_agnostic_structure(row["stem_markdown"]) for row in rows}), 12)
        self.assertEqual(
            [row["answer"] for row in rows],
            [
                "$1$",
                "$1$",
                "$1/2$",
                "$e$",
                "$1$",
                "$1$",
                "$[0,1]$",
                "$\\frac12$",
                "$\\frac{2}{\\pi^2}$",
                "$1$",
                "$4$",
                "$\\frac1e$",
            ],
        )
        for row in rows:
            self.assertEqual(row["question_type"], "solution")
            self.assertEqual(row["options"], {})
            self.assertNotRegex(row["stem_markdown"], r"(?m)^\s*[A-D][.．、:：]")
            self.assertEqual(
                _difficulty_gate_error(
                    "challenge",
                    "solution",
                    row["stem_markdown"],
                    row["solution_markdown"],
                ),
                "",
            )
        verifier_payload = json.loads(
            _verification_messages(
                "solution",
                rows[0]["stem_markdown"],
                {},
                target_difficulty="challenge",
                knowledge=request["knowledge"],
            )[1]["content"]
        )
        self.assertIn("只返回数值本身", verifier_payload["required_json"]["answer"])
        recipe = _dynamic_structure_recipe(request, "solution")
        self.assertIn("无 A/B/C/D", recipe["contract"])
        self.assertIn("禁止 f(a-x)", recipe["contract"])

    def test_inequality_solution_retry_skips_rejected_and_inflight_families(self) -> None:
        request = {
            "mode": "knowledge",
            "student_id": "KNOWLEDGE",
            "student_name": "按知识点生成",
            "knowledge": "导数应用·不等式证明·构造函数证明",
            "question_type": "solution",
            "difficulty": "challenge",
            "focus": "auto",
            "reference_count": 3,
            "batch_id": "batch-inflight-family-test",
            "batch_size": 3,
            "slot_index": 0,
            "attempt": 2,
            "diversity_round": 1,
        }
        rejected = _inequality_solution_blueprint({**request, "_family_override": 0}, "solution")
        reserved = _inequality_solution_blueprint({**request, "_family_override": 1}, "solution")
        assert rejected is not None and reserved is not None
        rejection_path = self.bank / "generation" / "live_personalized_generation_rejections.jsonl"
        rejection_path.write_text(
            json.dumps(
                {
                    "student_id": "KNOWLEDGE",
                    "knowledge": request["knowledge"],
                    "question_type": "solution",
                    "structure": _number_agnostic_structure(rejected["stem_markdown"]),
                    "math_structure": _math_structure(rejected["stem_markdown"]),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        _register_batch_draft(
            request["batch_id"],
            1,
            reserved["stem_markdown"],
            reserved["family_id"],
        )
        try:
            selected = _select_safety_blueprint(self.bank, request, "solution")
        finally:
            _release_batch_draft(request["batch_id"], 1)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["family_id"], "inequality_solution-2")

    def test_old_safe_verifier_rejections_do_not_exhaust_revised_families(self) -> None:
        request = {
            "mode": "knowledge",
            "student_id": "KNOWLEDGE",
            "student_name": "按知识点生成",
            "knowledge": "导数应用·不等式证明·构造函数证明",
            "question_type": "solution",
            "difficulty": "challenge",
            "focus": "auto",
            "reference_count": 3,
            "batch_id": "batch-policy-version-test",
            "batch_size": 3,
            "slot_index": 0,
            "attempt": 1,
            "diversity_round": 1,
        }
        family_zero = _inequality_solution_blueprint(
            {**request, "_family_override": 0}, "solution"
        )
        assert family_zero is not None
        base_rejection = {
            "student_id": "KNOWLEDGE",
            "knowledge": request["knowledge"],
            "question_type": "solution",
            "safety_blueprint_family": family_zero["family_id"],
            "structure": _number_agnostic_structure(family_zero["stem_markdown"]),
            "math_structure": _math_structure(family_zero["stem_markdown"]),
        }
        rejection_path = self.bank / "generation" / "live_personalized_generation_rejections.jsonl"
        rejection_path.write_text(json.dumps(base_rejection, ensure_ascii=False) + "\n", encoding="utf-8")
        selected = _select_safety_blueprint(self.bank, request, "solution")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["family_id"], family_zero["family_id"])

        current_rejection = {
            **base_rejection,
            "verification_policy_version": SAFE_BLUEPRINT_VERIFICATION_POLICY_VERSION,
        }
        rejection_path.write_text(
            json.dumps(current_rejection, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        selected = _select_safety_blueprint(
            self.bank,
            {**request, "batch_id": "batch-current-policy-rejection"},
            "solution",
        )
        self.assertIsNotNone(selected)
        self.assertNotEqual(selected["family_id"], family_zero["family_id"])

    def test_declared_equality_point_gate_catches_a_model_counterexample(self) -> None:
        invalid = {
            "stem_markdown": (
                r"设函数 $f(x)=e^x-\ln x-2x$（$x>0$）。证明 $f(x)\geq1$，"
                r"且等号仅在 $x=1$ 处成立。"
            )
        }
        error = _declared_equality_point_error(invalid)
        self.assertIn("直接代入", error)
        self.assertIn("不等于 1", error)
        valid = {
            "stem_markdown": (
                r"设函数 $f(x)=e^x-x$。证明 $f(x)\geq1$，且等号仅在 $x=0$ 处成立。"
            )
        }
        self.assertEqual(_declared_equality_point_error(valid), "")
        self.assertIn(
            "自我修正",
            _verification_solution_process_error("先得 a=e。重新分析后，最终答案为 1。"),
        )
        self.assertEqual(_verification_solution_process_error("由单调性可知最大值为 1。"), "")

    def test_safe_blueprint_uses_proven_solution_when_verifier_omits_explanation(self) -> None:
        request = {
            "mode": "knowledge",
            "student_id": "KNOWLEDGE",
            "student_name": "按知识点生成",
            "knowledge": "导数应用·单调性·判断单调区间",
            "question_type": "auto",
            "difficulty": "auto",
            "focus": "auto",
            "reference_count": 3,
            "slot_index": 0,
            "batch_id": "batch-solution-fallback-test",
            "attempt": 1,
        }
        blueprint = _monotonicity_blueprint(request, "fill_blank")
        question = _question_from_draft(
            GatewayConfig("http://127.0.0.1:24333", "test", "demo:model", 60),
            request,
            {"question_type": "fill_blank"},
            self.references[:3],
            blueprint,
            {"status": "passed", "answer": blueprint["answer"], "solution_markdown": "", "notes": "答案正确"},
            1,
        )
        self.assertEqual(question["solution_markdown"], blueprint["solution_markdown"])
        self.assertEqual(
            question["generation"]["generator"],
            "smart-question-recommender-safety-blueprint-v1",
        )
        self.assertEqual(question["generation"]["model_role"], "verifier")

    def test_cancelled_batch_stops_before_gateway_call(self) -> None:
        request = {**self.request(3), "batch_id": "batch-cancel-test", "slot_index": 0}
        cancel_personalized_batch({"batch_id": request["batch_id"]})
        with patch("live_personalized_generation._gateway_chat_json") as gateway:
            with self.assertRaisesRegex(GenerationCancelledError, "用户停止"):
                generate_personalized_draft(self.bank, request)
        gateway.assert_not_called()

    def test_pending_history_is_hidden_until_atomic_batch_commit(self) -> None:
        history_path = self.bank / "generation" / "live_personalized_generation_history.jsonl"
        pending = {
            "schema_version": "live-personalized-generation-history-v1",
            "delivery_status": "pending",
            "request": {"batch_id": "batch-pending-commit"},
            "question": {"question_id": "gen_pending_001", "stem_markdown": "待整批提交的新题"},
        }
        history_path.write_text(json.dumps(pending, ensure_ascii=False) + "\n", encoding="utf-8")

        self.assertEqual(_history_rows(self.bank), [])
        committed = commit_personalized_batch(
            self.bank,
            {
                "batch_ids": ["batch-pending-commit"],
                "question_ids": ["gen_pending_001"],
                "expected_count": 1,
            },
        )

        self.assertEqual(committed["status"], "committed")
        visible = _history_rows(self.bank)
        self.assertEqual([row["question"]["question_id"] for row in visible], ["gen_pending_001"])
        self.assertEqual(visible[0]["delivery_status"], "committed")
        self.assertTrue(visible[0]["committed_at"])

    def test_legacy_history_without_delivery_status_requires_teacher_approval(self) -> None:
        history_path = self.bank / "generation" / "live_personalized_generation_history.jsonl"
        rows = [
            {
                "request": {"batch_id": "legacy-pending"},
                "question": {
                    "question_id": "gen_legacy_pending",
                    "teacher_review": {"status": "pending"},
                },
            },
            {
                "request": {"batch_id": "legacy-approved"},
                "question": {
                    "question_id": "gen_legacy_approved",
                    "teacher_review": {"status": "approved"},
                },
            },
            {
                "delivery_status": "pending",
                "request": {"batch_id": "new-pending"},
                "question": {
                    "question_id": "gen_new_pending",
                    "teacher_review": {"status": "approved"},
                },
            },
            {
                "delivery_status": "committed",
                "request": {"batch_id": "new-committed"},
                "question": {
                    "question_id": "gen_new_committed",
                    "teacher_review": {"status": "pending"},
                },
            },
        ]
        history_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

        visible_ids = {
            row["question"]["question_id"] for row in _history_rows(self.bank)
        }
        self.assertEqual(visible_ids, {"gen_legacy_approved", "gen_new_committed"})

    def test_partial_batch_commit_is_idempotent_and_preserves_other_pending_rows(self) -> None:
        history_path = self.bank / "generation" / "live_personalized_generation_history.jsonl"
        rows = [
            {
                "delivery_status": "pending",
                "request": {"batch_id": "batch-partial"},
                "question": {"question_id": "gen_partial_001"},
            },
            {
                "delivery_status": "committed",
                "committed_at": "2026-07-16T00:00:00+00:00",
                "request": {"batch_id": "batch-partial"},
                "question": {"question_id": "gen_partial_002"},
            },
            {
                "delivery_status": "pending",
                "request": {"batch_id": "batch-partial"},
                "question": {"question_id": "gen_partial_003"},
            },
            {
                "delivery_status": "committed",
                "committed_at": "2026-07-15T00:00:00+00:00",
                "request": {"batch_id": "batch-unrelated"},
                "question": {"question_id": "gen_unrelated_committed"},
            },
        ]
        history_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        payload = {
            "batch_ids": ["batch-partial"],
            "question_ids": ["gen_partial_001", "gen_partial_002"],
            "expected_count": 2,
        }

        first = commit_personalized_batch(self.bank, payload)
        first_persisted = history_path.read_text(encoding="utf-8")
        second = commit_personalized_batch(self.bank, payload)
        second_persisted = history_path.read_text(encoding="utf-8")

        self.assertEqual(first["status"], "committed")
        self.assertEqual(first["question_count"], 2)
        self.assertEqual(second, first)
        self.assertEqual(second_persisted, first_persisted)
        persisted = {
            row["question"]["question_id"]: row
            for row in (json.loads(line) for line in second_persisted.splitlines())
        }
        self.assertEqual(persisted["gen_partial_001"]["delivery_status"], "committed")
        self.assertEqual(persisted["gen_partial_002"]["delivery_status"], "committed")
        self.assertEqual(
            persisted["gen_partial_002"]["committed_at"],
            "2026-07-16T00:00:00+00:00",
        )
        self.assertEqual(persisted["gen_partial_003"]["delivery_status"], "pending")
        self.assertEqual(
            {row["question"]["question_id"] for row in _history_rows(self.bank)},
            {"gen_partial_001", "gen_partial_002", "gen_unrelated_committed"},
        )

    def test_root_batch_cancel_covers_recovery_children_and_discards_only_pending_rows(self) -> None:
        history_path = self.bank / "generation" / "live_personalized_generation_history.jsonl"
        rows = [
            {
                "delivery_status": "pending",
                "request": {"batch_id": "batch-root-cancel"},
                "question": {"question_id": "gen_root_pending"},
            },
            {
                "delivery_status": "pending",
                "request": {"batch_id": "batch-root-cancel-recovery-1"},
                "question": {"question_id": "gen_recovery_pending"},
            },
            {
                "delivery_status": "committed",
                "request": {"batch_id": "batch-root-cancel"},
                "question": {"question_id": "gen_root_already_committed"},
            },
            {
                "delivery_status": "pending",
                "request": {"batch_id": "batch-unrelated"},
                "question": {"question_id": "gen_unrelated_pending"},
            },
        ]
        history_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

        cancelled = cancel_personalized_batch({"batch_id": "batch-root-cancel"}, self.bank)

        self.assertEqual(cancelled["discarded_pending"], 2)
        persisted = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(
            {row["question"]["question_id"] for row in persisted},
            {"gen_root_already_committed", "gen_unrelated_pending"},
        )
        recovery_request = {
            **self.request(3),
            "batch_id": "batch-root-cancel-recovery-2",
            "slot_index": 0,
        }
        with patch("live_personalized_generation._gateway_chat_json") as gateway:
            with self.assertRaisesRegex(GenerationCancelledError, "用户停止"):
                generate_personalized_draft(self.bank, recovery_request)
        gateway.assert_not_called()

    def test_cancel_stops_batch_but_preserves_verified_question_waiting_for_commit_retry(self) -> None:
        history_path = self.bank / "generation" / "live_personalized_generation_history.jsonl"
        rows = [
            {
                "delivery_status": "pending",
                "request": {"batch_id": "batch-preserve-sync"},
                "question": {"question_id": "gen_keep_for_retry"},
            },
            {
                "delivery_status": "pending",
                "request": {"batch_id": "batch-preserve-sync-recovery-1"},
                "question": {"question_id": "gen_discard_other_pending"},
            },
        ]
        history_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

        cancelled = cancel_personalized_batch(
            {
                "batch_id": "batch-preserve-sync",
                "preserve_question_ids": ["gen_keep_for_retry"],
            },
            self.bank,
        )

        self.assertEqual(cancelled["discarded_pending"], 1)
        self.assertEqual(cancelled["preserved_pending"], 1)
        persisted = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(
            {row["question"]["question_id"] for row in persisted},
            {"gen_keep_for_retry"},
        )
        committed = commit_personalized_batch(
            self.bank,
            {
                "batch_ids": ["batch-preserve-sync"],
                "question_ids": ["gen_keep_for_retry"],
                "expected_count": 1,
            },
        )
        self.assertEqual(committed["status"], "committed")

    def test_question_type_shape_gate_runs_before_verifier(self) -> None:
        invalid_solution = {
            "question_type": "solution",
            "stem_markdown": "下列结论正确的是（ ）\nA. 甲\nB. 乙\nC. 丙\nD. 丁",
            "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            "answer": "B",
            "solution_markdown": "选择 B。",
            "changed_dimensions": ["question_angle", "reasoning_path"],
        }
        request = {**self.request(3), "question_type": "solution"}

        with patch(
            "live_personalized_generation._gateway_chat_json", return_value=invalid_solution
        ) as gateway, patch("live_personalized_generation.verify_personalized_draft") as verifier:
            with self.assertRaisesRegex(LiveGenerationError, "A-D 选项|选择题措辞"):
                generate_personalized_question(self.bank, request, max_attempts=1)

        gateway.assert_called_once()
        verifier.assert_not_called()
        self.assertTrue(_question_type_shape_error(invalid_solution, "solution"))
        self.assertTrue(
            _question_type_shape_error(
                {
                    "stem_markdown": "求函数最小值。",
                    "options": {},
                    "answer": "1",
                    "solution_markdown": "配方。",
                },
                "fill_blank",
            )
        )
        self.assertTrue(
            _question_type_shape_error(
                {
                    "stem_markdown": "选择正确结论。",
                    "options": {"A": "甲", "B": "乙", "C": "丙"},
                    "answer": "A",
                    "solution_markdown": "检验。",
                },
                "single_choice",
            )
        )

    def test_batch_dedup_distinguishes_safe_function_families(self) -> None:
        batch_id = "batch-structured-family-test"
        first = "已知函数 $f(x)=x+4/x$，则函数的单调递减区间为____。"
        second = "已知函数 $f(x)=ln x+3/x$，则函数的单调递减区间为____。"
        try:
            _register_batch_draft(batch_id, 0, first, "monotonicity-1")
            _register_batch_draft(batch_id, 1, second, "monotonicity-2")
            with self.assertRaisesRegex(LiveGenerationError, "本批次已生成题过于相似"):
                _register_batch_draft(batch_id, 2, second.replace("3/x", "5/x"), "monotonicity-2")
        finally:
            for slot_index in range(3):
                _release_batch_draft(batch_id, slot_index)

    def test_common_model_field_aliases_are_normalized(self) -> None:
        draft = self.draft("字段别名兼容测试题。\nA. 1\nB. 2\nC. 3\nD. 4")
        draft["stem"] = draft.pop("stem_markdown")
        draft["correct_answer"] = draft.pop("answer")
        draft["solution"] = draft.pop("solution_markdown")
        verification = self.verification()
        verification["valid"] = True
        verification.pop("status")
        verification["correct_answer"] = verification.pop("answer")
        verification["analysis"] = verification.pop("solution_markdown")
        with patch("live_personalized_generation._gateway_chat_json", side_effect=[draft, verification]):
            result = generate_personalized_question(self.bank, self.request(3))
        self.assertEqual(result["question"]["verification"]["status"], "passed")
        self.assertEqual(result["question"]["answer"], "B")

    def test_same_batch_rejects_duplicate_draft_before_verification(self) -> None:
        request = {**self.request(3), "batch_id": "batch-dedup-test", "slot_index": 0}
        duplicate = self.draft("同批次重复题干。\nA. 1\nB. 2\nC. 3\nD. 4")
        with patch("live_personalized_generation._gateway_chat_json", return_value=duplicate):
            first = generate_personalized_draft(self.bank, request)
            self.assertEqual(first["status"], "draft")
            with self.assertRaisesRegex(LiveGenerationError, "本批次已生成题过于相似"):
                generate_personalized_draft(self.bank, {**request, "slot_index": 1})
        rejection_path = self.bank / "generation" / "live_personalized_generation_rejections.jsonl"
        self.assertIn("同批次重复题干", rejection_path.read_text(encoding="utf-8"))
        with patch("live_personalized_generation._gateway_chat_json", return_value=duplicate):
            with self.assertRaisesRegex(LiveGenerationError, "此前已经生成失败"):
                generate_personalized_draft(
                    self.bank,
                    {**request, "batch_id": "new-batch-after-dedup", "slot_index": 2},
                )

    def test_failed_verification_persists_rejection_and_blocks_future_reuse(self) -> None:
        request = {**self.request(3), "batch_id": "batch-release-test", "slot_index": 0}
        duplicate = self.draft("校验失败后不得重新使用的题干。\nA. 1\nB. 2\nC. 3\nD. 4")
        with patch("live_personalized_generation._gateway_chat_json", return_value=duplicate):
            first = generate_personalized_draft(self.bank, request)
        failed_verification = {
            "status": "failed",
            "answer": "",
            "solution_markdown": "",
            "notes": "题面不充分。",
        }
        with patch("live_personalized_generation._gateway_chat_json", return_value=failed_verification):
            with self.assertRaisesRegex(LiveGenerationError, "独立审题未通过"):
                verify_personalized_draft(self.bank, {**request, "draft": first["draft"]})
        rejection_path = self.bank / "generation" / "live_personalized_generation_rejections.jsonl"
        self.assertIn("校验失败后不得重新使用", rejection_path.read_text(encoding="utf-8"))
        with patch("live_personalized_generation._gateway_chat_json", return_value=duplicate):
            with self.assertRaisesRegex(LiveGenerationError, "此前已经生成失败"):
                generate_personalized_draft(
                    self.bank,
                    {**request, "batch_id": "new-batch-after-rejection", "slot_index": 1},
                )

    def test_incomplete_model_draft_is_completed_before_display(self) -> None:
        incomplete = {
            "question_type": "single_choice",
            "stem_markdown": "待补全的新结构题。\nA. 1\nB. 2\nC. 3\nD. 4",
            "answer": "B",
            "changed_dimensions": ["question_angle", "reasoning_path"],
        }
        completed = self.draft("补全后的新结构题。\nA. 1\nB. 2\nC. 3\nD. 4")
        with patch("live_personalized_generation._gateway_chat_json", side_effect=[incomplete, completed]) as gateway:
            result = generate_personalized_draft(self.bank, self.request(3))
        self.assertEqual(gateway.call_count, 2)
        self.assertEqual(result["draft"]["stem_markdown"], completed["stem_markdown"])
        self.assertTrue(result["draft"]["solution_markdown"])

    def test_dynamic_structure_recipes_do_not_cycle_within_sixty_rounds(self) -> None:
        recipes = [
            _dynamic_structure_recipe({"diversity_round": round_, "batch_size": 1, "slot_index": 0})
            for round_ in range(16, 76)
        ]
        self.assertEqual(len({row["recipe_id"] for row in recipes}), 60)
        self.assertEqual(len({(row["mathematical_object"], row["condition_transform"]) for row in recipes}), 60)
        first = _dynamic_structure_recipe({"diversity_round": 20, "batch_size": 1, "slot_index": 0, "attempt": 1})
        retry = _dynamic_structure_recipe({"diversity_round": 20, "batch_size": 1, "slot_index": 0, "attempt": 2})
        self.assertNotEqual(first["recipe_id"], retry["recipe_id"])
        self.assertNotEqual(first["mathematical_object"], retry["mathematical_object"])

    def test_model_json_repairs_unescaped_latex_and_trailing_comma(self) -> None:
        parsed = _parse_json_content(
            r'{"answer":"-\frac{5}{2}","solution_markdown":"由\ln x求导可得",}'
        )
        self.assertEqual(parsed["answer"], r"-\frac{5}{2}")
        self.assertIn(r"\ln", parsed["solution_markdown"])

    def test_gateway_json_mode_retries_a_truncated_response(self) -> None:
        incomplete = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"answer":"A","solution_markdown":"未闭合'},
                }
            ]
        }
        complete = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"answer":"A","solution_markdown":"完整解析"}'},
                }
            ]
        }
        config = GatewayConfig("http://127.0.0.1:24333", "secret", "demo:model", 60)
        with patch("live_personalized_generation._http_json", side_effect=[incomplete, complete]) as request:
            result = _gateway_chat_json(
                config,
                [{"role": "user", "content": "只返回 JSON 对象"}],
                temperature=0.45,
                max_tokens=1800,
            )
        self.assertEqual(result["answer"], "A")
        self.assertEqual(request.call_count, 2)
        first_payload = request.call_args_list[0].kwargs["payload"]
        retry_payload = request.call_args_list[1].kwargs["payload"]
        self.assertEqual(first_payload["response_format"], {"type": "json_object"})
        self.assertEqual(retry_payload["response_format"], {"type": "json_object"})
        self.assertEqual(first_payload["max_tokens"], 1800)
        self.assertEqual(retry_payload["max_tokens"], 3000)
        self.assertLessEqual(retry_payload["temperature"], 0.15)
        self.assertIn("raw_response", retry_payload["messages"][1]["content"])
        self.assertIn("未闭合", retry_payload["messages"][1]["content"])

    def test_gateway_json_mode_can_repair_twice_before_failing_slot(self) -> None:
        first = {"choices": [{"finish_reason": "length", "message": {"content": '{"answer":"C"'}}]}
        second = {
            "choices": [
                {"finish_reason": "stop", "message": {"content": '{"answer":"C","solution_markdown":'}}
            ]
        }
        third = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"answer":"C","solution_markdown":"完整解析"}'},
                }
            ]
        }
        config = GatewayConfig("http://127.0.0.1:24333", "secret", "demo:model", 60)
        with patch("live_personalized_generation._http_json", side_effect=[first, second, third]) as request:
            result = _gateway_chat_json(
                config,
                [{"role": "user", "content": "只返回 JSON 对象"}],
                temperature=0.45,
                max_tokens=1800,
            )
        self.assertEqual(result["answer"], "C")
        self.assertEqual(request.call_count, 3)
        final_repair_payload = request.call_args_list[2].kwargs["payload"]
        final_repair_content = json.loads(final_repair_payload["messages"][1]["content"])
        self.assertIn('"answer":"C"', final_repair_content["raw_response"])
        self.assertIn("solution_markdown", final_repair_content["raw_response"])

    def test_approved_review_is_validated_and_persisted(self) -> None:
        with patch(
            "live_personalized_generation._gateway_chat_json",
            side_effect=[
                self.draft("一道人机协同生成的全新函数题。\nA. 1\nB. 2\nC. 3\nD. 4"),
                self.verification(),
            ],
        ):
            question = generate_personalized_question(self.bank, self.request(3))["question"]
        saved = save_personalized_review(
            self.bank,
            {"question": question, "status": "approved", "reviewer": "teacher", "notes": "通过"},
        )
        self.assertEqual(saved["teacher_review"]["status"], "approved")
        content = (self.bank / "review" / "personalized_question_reviews.jsonl").read_text(encoding="utf-8")
        self.assertIn(question["question_id"], content)

    def test_status_does_not_require_or_expose_a_key_when_unconfigured(self) -> None:
        with patch.dict(os.environ, {"CHERRY_STUDIO_API_KEY": "", "CHERRY_STUDIO_MODEL": ""}, clear=False):
            status = gateway_status()
        self.assertFalse(status["configured"])
        self.assertNotIn("api_key", status)
        self.assertNotIn("cs-sk-test", json.dumps(status))

    def test_gateway_config_can_read_user_scoped_environment_fallback(self) -> None:
        values = {
            "CHERRY_STUDIO_BASE_URL": "http://127.0.0.1:24333",
            "CHERRY_STUDIO_API_KEY": "user-scope-key",
            "CHERRY_STUDIO_MODEL": "demo:model",
            "CHERRY_STUDIO_TIMEOUT_SECONDS": "240",
        }
        with patch.dict(os.environ, {}, clear=True), patch(
            "live_personalized_generation._user_environment_value",
            side_effect=lambda name: values.get(name, ""),
        ):
            config = GatewayConfig.from_env()
        self.assertEqual(config.base_url, "http://127.0.0.1:24333")
        self.assertEqual(config.api_key, "user-scope-key")
        self.assertEqual(config.model, "demo:model")
        self.assertEqual(config.timeout_seconds, 240)

    def test_real_http_round_trip_uses_gateway_auth_model_and_two_pass_validation(self) -> None:
        calls: list[dict] = []
        draft = self.draft("通过本机网关生成的全新函数题。\nA. 1\nB. 2\nC. 3\nD. 4")
        verification = self.verification()

        class FakeGateway(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

            def send_json(self, payload: dict, status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/health":
                    self.send_json({"status": "ok"})
                    return
                if self.path.startswith("/v1/models"):
                    calls.append({"route": "models", "authorization": self.headers.get("Authorization")})
                    self.send_json({"data": [{"id": "demo:model"}]})
                    return
                self.send_json({"error": "not found"}, 404)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append(
                    {
                        "route": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "model": payload.get("model"),
                        "reasoning_effort": payload.get("reasoning_effort"),
                        "response_format": payload.get("response_format"),
                    }
                )
                content = draft if len([row for row in calls if row["route"] == self.path]) == 1 else verification
                self.send_json({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]})

        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(
                os.environ,
                {"CHERRY_STUDIO_BASE_URL": f"http://127.0.0.1:{server.server_port}"},
                clear=False,
            ):
                status = gateway_status()
                result = generate_personalized_question(self.bank, self.request(3))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(status["authenticated"])
        self.assertTrue(status["model_available"])
        chat_calls = [row for row in calls if row["route"] == "/v1/chat/completions"]
        self.assertEqual(len(chat_calls), 2)
        self.assertTrue(all(row["authorization"] == "Bearer cs-sk-test" for row in calls))
        self.assertTrue(all(row["model"] == "demo:model" for row in chat_calls))
        self.assertTrue(all(row["reasoning_effort"] == "low" for row in chat_calls))
        self.assertTrue(all(row["response_format"] == {"type": "json_object"} for row in chat_calls))
        self.assertEqual(result["question"]["verification"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
