---
name: html-math-to-docx
description: Convert HTML or web-exported math problem sets into DOCX while preserving equations as editable native Word formulas. Use when working with .html/.htm files that contain LaTeX, MathJax, KaTeX, MathML, textbook questions, generated exercises, or exam problems and the output must keep accurate Word formulas instead of screenshots or lost rendering.
---

# HTML Math To DOCX

Use this skill to convert math-heavy HTML into `.docx` with native Word OMML equations.

This skill is an export/conversion layer only. It preserves visible math and layout; it does not validate question correctness, generated-question novelty, answer-solution consistency, or difficulty labels.

## Requirements

- Pandoc must be installed and available on `PATH`.
- Python 3 must be available.
- Formula accuracy is only guaranteed when the HTML still contains TeX, MathML, or another machine-readable math source.

## Workflow

Always diagnose before converting:

```bash
python scripts/diagnose_html_math.py input.html --manifest input.math-manifest.json
```

If the diagnostic report finds TeX delimiters, MathML, Pandoc math spans, or `script type="math/tex"` blocks, convert:

```bash
python scripts/convert_html_math_to_docx.py input.html -o output.docx
```

Validate the generated Word formulas:

```bash
python scripts/validate_docx_math.py output.docx
```

Polish the Word layout and recover HTML image sizes:

```bash
python scripts/postprocess_docx_layout.py output.docx -o output-polished.docx --html input.html
```

For exam papers that need more writing room, increase question spacing and solution blanks:

```bash
python scripts/postprocess_docx_layout.py output.docx -o output-polished.docx --html input.html --question-after-pt 8 --solution-blank-lines 10
```

Accept the output only when the DOCX contains native Word OMML formulas and the count matches the expected source formulas closely.

When converting output from the smart-question workflow, export student-facing generated questions only after `teacher_review.status: approved`, `verification.status: passed`, and answer-solution consistency have already passed upstream. Teaching-chain stages may appear as public training labels, but diagnostic evidence, task specs, structure anchors, rejection reasons, and teacher feedback memory are teacher records; include them only in an explicitly teacher-facing document or appendix, never in a student exercise by accident.

## Accuracy Rules

- Prefer original TeX or MathML over rendered HTML, SVG, or images.
- Treat `\(...\)`, `\[...\]`, `$...$`, `$$...$$`, MathML, and Pandoc math spans as convertible sources.
- Ignore non-math JavaScript and CSS while diagnosing; embedded MathJax libraries often contain delimiter-like code that is not source content.
- Do not claim editable formula accuracy when formulas exist only as images, canvas, or path-only SVG.
- Preserve image-only formulas as images and report that editable formulas require OCR or the original TeX/MathML source.
- Do not “fix” mathematical content during conversion. If the source HTML shows a suspected wrong answer, repeated generated structure, or mismatched difficulty label, stop and send it back to the source workflow instead of silently editing the DOCX.

## Bundled Scripts

- `scripts/diagnose_html_math.py`: detect formula source types and write a JSON manifest.
- `scripts/convert_html_math_to_docx.py`: normalize common HTML math forms and run Pandoc.
- `scripts/postprocess_docx_layout.py`: apply exam-paper DOCX spacing, margins, Chinese font defaults, and image sizing from HTML `data-width/data-height`.
- `scripts/validate_docx_math.py`: count Word OMML formula tags inside a `.docx`.

## Reference

Read `references/conversion-policy.md` when judging whether a conversion is accurate enough to deliver.
