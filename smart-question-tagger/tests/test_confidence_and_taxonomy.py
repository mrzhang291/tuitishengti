from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_tags  # noqa: E402
import gaokao_taxonomy as taxonomy  # noqa: E402
import tagging  # noqa: E402


class ConfidenceAndTaxonomyTests(unittest.TestCase):
    def tag(self, stem: str, solution: str = "", question_type: str = "single_choice") -> dict:
        return tagging.tag_question(
            {
                "question_id": "demo_q001",
                "display_id": "DEMO-Q001",
                "question_type": question_type,
                "stem_markdown": stem,
                "solution_markdown": solution,
                "quality_flags": [],
            }
        )

    def test_shared_taxonomy_accepts_all_canonical_primary_labels(self) -> None:
        self.assertEqual(audit_tags.TAXONOMY, set(taxonomy.KNOWLEDGE_TAXONOMY))
        for label in (
            "导数与微分·导数定义·平均变化率与瞬时变化率",
            "函数综合·函数性质·奇偶性与周期性",
            "导数应用·不等式证明·构造函数证明",
            "概率统计·数字特征·期望与方差",
        ):
            self.assertIn(label, audit_tags.TAXONOMY)

    def test_specific_solution_methods_win_with_evidence(self) -> None:
        cases = [
            (
                "求函数在区间上的平均变化率",
                "根据导数定义计算瞬时变化率",
                "导数与微分·导数定义·平均变化率与瞬时变化率",
            ),
            (
                "求曲线在点 P 处的切线方程",
                "先求导并代入切点得到切线斜率",
                "导数与微分·切线方程·求切线",
            ),
            (
                "求随机变量 X 的数学期望和方差",
                "计算 E(X) 与 D(X)",
                "概率统计·数字特征·期望与方差",
            ),
            (
                "求棱锥体积的最大值",
                "建立 V(x) 并求导，利用 V'(x) 判断最大值",
                "导数应用·极值与最值·求最值",
            ),
        ]
        for stem, solution, expected in cases:
            with self.subTest(expected=expected):
                tags = self.tag(stem, solution, "solution")["tags"]
                self.assertEqual(tags["primary_knowledge"], expected)
                self.assertTrue(tags["tag_evidence"])
                self.assertGreater(tags["tags_confidence_detail"]["primary_knowledge"], 0.75)

    def test_confidence_is_field_level_and_discriminative(self) -> None:
        clear = self.tag("求曲线切线方程", "由导数几何意义求切线斜率")["tags"]
        ambiguous = self.tag("已知函数 f(x)，回答下列问题")["tags"]
        self.assertGreater(clear["tags_confidence"], ambiguous["tags_confidence"])
        self.assertGreaterEqual(clear["tags_confidence"], 0.8)
        self.assertLess(ambiguous["tags_confidence"], 0.8)
        self.assertEqual(
            set(clear["tags_confidence_detail"]),
            {
                "curriculum_theme",
                "knowledge_unit",
                "primary_knowledge",
                "skill_tags",
                "ability_tags",
                "difficulty",
                "structure",
                "overall",
                "classification_margin",
            },
        )

    def test_audit_does_not_flag_canonical_primary_as_outside(self) -> None:
        tagged = self.tag("求随机变量的数学期望", "利用分布列计算 E(X)")
        audit = audit_tags._audit_question(tagged, threshold=0.8)
        self.assertNotIn("primary_outside_taxonomy", audit["flags"])

    def test_lowercase_function_symbols_do_not_trigger_probability_statistics(self) -> None:
        tags = self.tag("函数有三个零点 a、b、c", "展开得 d(x)，再利用导数判断零点个数")["tags"]
        self.assertNotEqual(tags["curriculum_theme"], "T9概率统计")


if __name__ == "__main__":
    unittest.main()
