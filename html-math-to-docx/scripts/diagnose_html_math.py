#!/usr/bin/env python3
"""Diagnose machine-readable math sources in an HTML file."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path


SCRIPT_MATH_RE = re.compile(
    r"<script\b(?=[^>]*\btype=[\"']?math/tex(?:;\s*mode=display)?[\"']?)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
MATHML_RE = re.compile(r"<math\b[^>]*>.*?</math>", re.IGNORECASE | re.DOTALL)
PANDOC_MATH_SPAN_RE = re.compile(
    r"<span\b(?=[^>]*\bclass=[\"'][^\"']*\bmath\b[^\"']*[\"'])[^>]*>.*?</span>",
    re.IGNORECASE | re.DOTALL,
)
DATA_LATEX_RE = re.compile(
    r"\b(?:data-latex|data-tex|data-math|alttext|aria-label)=[\"']([^\"']{1,2000})[\"']",
    re.IGNORECASE | re.DOTALL,
)
IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
SVG_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.IGNORECASE | re.DOTALL)
CANVAS_RE = re.compile(r"<canvas\b[^>]*>", re.IGNORECASE | re.DOTALL)
KATEX_RE = re.compile(r"\bclass=[\"'][^\"']*\bkatex\b", re.IGNORECASE)
MATHJAX_RE = re.compile(r"\b(?:MathJax|mjx-|class=[\"'][^\"']*\bmathjax\b)", re.IGNORECASE)

DISPLAY_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
INLINE_BRACKET_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
DOUBLE_DOLLAR_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.DOTALL)
SINGLE_DOLLAR_RE = re.compile(r"(?<!\\)\$(?!\$)(.+?)(?<!\\)\$(?!\$)", re.DOTALL)


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def clean_sample(value: str) -> str:
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return value[:180]


def strip_non_content_blocks(text: str) -> str:
    """Remove scripts and styles before scanning visible HTML math content."""
    text = SCRIPT_BLOCK_RE.sub(" ", text)
    return STYLE_BLOCK_RE.sub(" ", text)


def count_tex_delimiters(text: str) -> dict[str, list[str]]:
    return {
        "display_bracket": [m.group(1) for m in DISPLAY_BRACKET_RE.finditer(text)],
        "inline_bracket": [m.group(1) for m in INLINE_BRACKET_RE.finditer(text)],
        "double_dollar": [m.group(1) for m in DOUBLE_DOLLAR_RE.finditer(text)],
        "single_dollar": [m.group(1) for m in SINGLE_DOLLAR_RE.finditer(text)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--manifest", type=Path, help="Write JSON diagnostic manifest.")
    args = parser.parse_args()

    source = read_text(args.html_file)
    script_math = SCRIPT_MATH_RE.findall(source)
    content_source = strip_non_content_blocks(source)
    tex_sources = count_tex_delimiters(content_source)
    mathml = MATHML_RE.findall(content_source)
    pandoc_spans = PANDOC_MATH_SPAN_RE.findall(content_source)
    data_latex = [m.group(1) for m in DATA_LATEX_RE.finditer(content_source)]
    images = IMG_RE.findall(content_source)
    image_math_like = [
        tag
        for tag in images
        if re.search(r"(math|formula|equation|latex|tex|katex|mathjax)", tag, re.IGNORECASE)
    ]

    convertible_tex_count = sum(len(v) for v in tex_sources.values()) + len(script_math) + len(data_latex)
    convertible_count = convertible_tex_count + len(mathml) + len(pandoc_spans)
    rendered_only_count = (
        len(image_math_like) + len(SVG_RE.findall(content_source)) + len(CANVAS_RE.findall(content_source))
    )

    report = {
        "file": str(args.html_file),
        "convertible_formula_sources": convertible_count,
        "tex_delimiters": {key: len(value) for key, value in tex_sources.items()},
        "script_type_math_tex": len(script_math),
        "mathml_blocks": len(mathml),
        "pandoc_math_spans": len(pandoc_spans),
        "data_latex_like_attributes": len(data_latex),
        "katex_markup_detected": bool(KATEX_RE.search(source)),
        "mathjax_markup_detected": bool(MATHJAX_RE.search(source)),
        "math_like_images": len(image_math_like),
        "svg_blocks": len(SVG_RE.findall(content_source)),
        "canvas_blocks": len(CANVAS_RE.findall(content_source)),
        "rendered_only_risk_count": rendered_only_count,
        "samples": {
            "script_type_math_tex": [clean_sample(v) for v in script_math[:5]],
            "mathml": [clean_sample(v) for v in mathml[:3]],
            "data_latex_like_attributes": [clean_sample(v) for v in data_latex[:5]],
            "tex_delimiters": {
                key: [clean_sample(v) for v in values[:5]] for key, values in tex_sources.items()
            },
        },
    }

    if convertible_count:
        recommendation = "Convertible math sources found. Proceed with Pandoc conversion and validate OMML."
    elif rendered_only_count:
        recommendation = (
            "Only rendered or image-like math was detected. Editable Word formulas are not guaranteed "
            "without original TeX/MathML or OCR."
        )
    else:
        recommendation = "No obvious math sources detected."
    report["recommendation"] = recommendation

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.manifest:
        args.manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
