from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import acceptance_matrix as acceptance


class AcceptanceMatrixTests(unittest.TestCase):
    def test_discovered_cases_are_complete_runnable_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            generation = bank / "generation"
            generation.mkdir()
            (generation / "student_generated_question_candidates.json").write_text(
                json.dumps(
                    {
                        "slots": [
                            {"primary_knowledge": "知识点甲"},
                            {"primary_knowledge": "知识点甲"},
                            {"primary_knowledge": "知识点乙"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            cases = acceptance.discover_default_cases(bank, 2)
        self.assertEqual([case["knowledge"] for case in cases], ["知识点甲", "知识点乙"])
        self.assertTrue(all(case["reference_count"] == 3 for case in cases))
        self.assertTrue(all(case["count"] == 1 for case in cases))

    def test_explicit_case_requires_supported_batch_size(self) -> None:
        with self.assertRaises(SystemExit):
            acceptance.parse_case('{"knowledge":"知识点甲","count":2}')


if __name__ == "__main__":
    unittest.main()
