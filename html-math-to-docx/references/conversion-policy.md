# Conversion Policy

Use this reference when deciding whether an HTML math conversion can be considered accurate.

## Source Priority

1. Original TeX inside delimiters, `script type="math/tex"`, `data-latex`, or similar attributes.
2. MathML inside `<math>` or hidden KaTeX MathML.
3. Pandoc math spans such as `<span class="math inline">...</span>`.
4. Rendered SVG or HTML with accessible TeX/MathML annotations.
5. Images, canvas, or SVG paths with no source annotation.

Only levels 1-3 are safe for unattended editable Word equation conversion. Level 4 requires inspection. Level 5 requires OCR/manual recovery or should be preserved as an image.

## Acceptance Checks

- Diagnostic report finds convertible math sources before conversion.
- DOCX validation finds `<m:oMath>` tags after conversion.
- The number of DOCX OMML formulas is close to the number of expected source formulas.
- Any mismatch is explained before delivering the file.
