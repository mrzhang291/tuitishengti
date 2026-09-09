import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_review_workbench import _math_html, _normalise_formula_text


class FormulaHtmlNormalizationTests(unittest.TestCase):
    def test_wraps_and_normalises_prime_caret(self):
        markup = '<span class="formula" data-formula-id="f001">f^&#x27;(x)=(1)/(x)</span>'
        self.assertEqual(
            _math_html(markup),
            '<span class="formula" data-formula-id="f001">$f\'(x)=(1)/(x)$</span>',
        )

    def test_does_not_double_wrap_math(self):
        markup = '<span class="formula" data-formula-id="f001">$f(x)=x^2$</span>'
        self.assertEqual(_math_html(markup), markup)

    def test_leaves_unrelated_markup_unchanged(self):
        markup = '<p>文本 <img src="assets/pic.png"></p>'
        self.assertEqual(_math_html(markup), markup)

    def test_normalises_prime_caret_inside_tex_delimiters(self):
        markup = '<p>$f^&#x27;(x)=(1)/(x)$</p>'
        self.assertEqual(_math_html(markup), "<p>$f'(x)=(1)/(x)$</p>")

    def test_normalises_raw_prime_caret_in_markdown(self):
        self.assertEqual(_normalise_formula_text("$f^'(x)=(1)/(x)$"), "$f'(x)=(1)/(x)$")

    def test_fixes_double_subscripts(self):
        self.assertEqual(_normalise_formula_text("$k_l_1=F'(x)>0$"), "$k_{l_1}=F'(x)>0$")

    def test_normalises_unicode_primes(self):
        self.assertEqual(_normalise_formula_text("f^′x"), "f'x")
        self.assertEqual(_normalise_formula_text("f^″x"), "f''x")


if __name__ == "__main__":
    unittest.main()
