import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_variants import validate_variant_set


class VariantValidationTests(unittest.TestCase):
    def payload(self):
        original = {
            "question_type": "solution",
            "stem_markdown": "已知函数 $f(x)=x^2-2x$，求最小值。",
        }
        stems = [
            "设关于 $x$ 的方程 $x^2-ax+1=0$ 有两个相等实根，求 $a$ 的正值。",
            "已知 $g(x)=x^3-3x$，求其在 $[-2,2]$ 上的最大值并说明理由。",
            "若函数 $h(x)=e^x-bx$ 在 $x=0$ 处取得最小值，求参数 $b$。",
        ]
        candidates = []
        for index, stem in enumerate(stems, 1):
            candidates.append(
                {
                    "candidate_id": f"v{index}",
                    "question_type": "solution",
                    "difficulty_level": 3,
                    "stem_markdown": stem,
                    "options": {},
                    "answer": "1",
                    "solution_markdown": "独立推导得到唯一结论。",
                    "variation_note": "改变提问对象与推理路径。",
                    "relative_difficulty": "Similar",
                    "changed_dimensions": ["question_angle", "reasoning_path"],
                    "independent_verification": {"status": "passed", "answer_matches": True},
                }
            )
        return {
            "original": original,
            "target": {"question_type": "solution", "difficulty_level": 3},
            "candidates": candidates,
            "recommendation": {"candidate_id": "v2"},
        }

    def test_accepts_complete_structurally_distinct_variants(self):
        self.assertEqual(validate_variant_set(self.payload()), [])

    def test_rejects_number_only_change_and_wrong_exact_level(self):
        payload = self.payload()
        payload["candidates"][0]["stem_markdown"] = "已知函数 $f(x)=x^2-4x$，求最小值。"
        payload["candidates"][0]["difficulty_level"] = 2
        errors = validate_variant_set(payload)
        self.assertTrue(any("only numbers" in error for error in errors), errors)
        self.assertTrue(any("exact level 3" in error for error in errors), errors)

    def test_rejects_solution_with_options_and_unresolved_image(self):
        payload = self.payload()
        payload["candidates"][1]["stem_markdown"] += "\nA. 1\nB. 2\n![](assets/missing.png)"
        errors = validate_variant_set(payload)
        self.assertTrue(any("must not contain A/B/C/D" in error for error in errors), errors)
        self.assertTrue(any("image references" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
