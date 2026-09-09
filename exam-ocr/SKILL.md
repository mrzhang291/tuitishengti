---
name: exam-ocr
description: Convert scanned exam papers, math-heavy PDFs, answer sheets, and Word exam documents into polished Markdown/HTML, then optionally rebuild those OCR outputs into a fresh structured per-question bank and bridge it into smart-question-tagger. Use for exam OCR, PDF/Word recognition, formula/table extraction, local OCR cleanup or auditing, OCR output structuring, question splitting, answer/solution pairing, source-provenance preservation, structure validation, or preparing OCR questions for tagging and knowledge-first AI-native recommendation.
---

# Exam OCR

Use MinerU for OCR/layout parsing, then run the bundled local polish and audit steps so exam papers are easier to read and print without silently changing OCR content.

## Scan With MinerU

Run the main script from the skill folder or pass its absolute path:

```bash
python scripts/ocr_exam.py input.pdf -o output_dir
python scripts/ocr_exam.py paper.pdf answers.pdf -o output_dir
python scripts/ocr_exam.py input.docx -o output_dir
```

The script reads the MinerU API token from `MINERU_TOKEN`. Tokens copied as `mineru:<jwt>` are accepted. Do not hardcode API tokens in the skill.
The official API base is the safe default. For a compatible local/test gateway, set `MINERU_API_BASE` or pass `--api-base`; credentials and query strings are rejected from the base URL.

PowerShell example:

```powershell
$env:MINERU_TOKEN = "mineru:<token-or-jwt>"
python scripts\ocr_exam.py .\paper.pdf .\answers.pdf -o .\exam-ocr
```

Default behavior:

1. Convert `.docx` or `.doc` inputs to PDF with `scripts/convert_to_pdf.py`.
2. Submit PDFs to MinerU with `model_version=vlm`, `language=ch`, formulas/tables enabled, and `extra_formats=["html"]`.
3. Upload with a bare PUT; do not add upload headers because MinerU OSS signatures are header-sensitive.
4. Download and cache each MinerU ZIP locally.
5. Copy `full.md`, `full.html`, and `images/` to the output directory.
6. Run `scripts/polish_outputs.py` automatically. When a same-name Markdown file exists, rebuild the polished HTML from Markdown and render formulas with MathJax instead of trusting MinerU `full.html` alone.
7. Run `scripts/audit_outputs.py` automatically.
8. Generate `_reports/quality_report.json` and `_reports/quality_report.html` automatically.
9. Verify Markdown image references before reporting completion.

The default polling interval is 3 seconds. Multiple PDFs upload in parallel by default. If complete same-name Markdown/HTML outputs already exist and are newer than the input PDF/Word file, the script reuses them and skips MinerU; if cached MinerU extracted results match the same input PDF, the script restores from cache and skips upload. Use `--force` to call MinerU again.

Use `--no-polish` only when the user explicitly wants raw MinerU output. Use `--no-audit` only for debugging. Use `--no-report` only when another pipeline already generates quality metadata.

Output contract:

- Always deliver complete `<original-name>.md` and `<original-name>.html` files for downstream processing whenever MinerU returns usable content.
- Do not block the main output for OCR quality warnings. Insert visible `OCR异常` markers in the document and record details in `ocr_warnings.json` plus a human-readable Chinese `ocr_warnings.md`.
- Treat missing files, missing referenced images, empty output, unprotected raw TeX without MathJax, or HTML/Markdown mismatch as blocking failures.
- Treat suspicious OCR repetition, known OCR glyph glue, and inserted `OCR异常` markers as warnings unless `--strict` is used.

## Structure OCR Outputs For Tagging

When polished OCR Markdown already exists, build a new structured bank directly from the top-level exam and answer files. Do not reuse an old `questions.jsonl`, generated bank, or ZIP as the source:

```powershell
python scripts\structure_outputs.py output_dir `
  --structured-output structured_bank `
  --tag
```

The structurer:

- pairs question and answer Markdown by normalized exam identity
- ignores `_raw_mineru`, caches, reports, and prior structured outputs
- removes answer-card blocks before numbered-question splitting
- repairs OCR-broken decimals such as `0. 5` without creating question 0
- recovers a solution question embedded after an inline section heading
- extracts answers from HTML tables, Markdown tables, and answer-summary lines
- separates question stems, A-D options, answers, and solutions
- copies referenced images into each question's `assets/` directory
- writes per-question JSON and MathJax previews
- generates `manifest.json`, a responsive/filterable MathJax-enabled `index.html`, and `structure_report.json/html`
- renders Markdown answers as HTML before MathJax typesetting, including long multi-part solution answers; never HTML-escape the entire answer cell into literal `$...$` text
- balances a single missing `$` delimiter in the HTML display layer (for example `-\frac{...}$`) while preserving the original extracted answer in JSON
- protects complete TeX fragments before Markdown conversion so Markdown cannot consume `\{` or formula row separators such as `\\` inside `aligned`, `array`, and cases-style expressions
- bounds fill-answer summaries at the next solution heading and separates explicitly numbered answers before positional answers, preventing text such as `### 5. A` or `1. A 由...` from leaking into the previous answer
- writes a concise `display_id` such as `SZ24-G2-W03-Q014` for pages and search while preserving the existing `question_id` and directory names for downstream compatibility
- compares structure before and after safe normalization, so the quality report distinguishes unresolved missing/duplicate numbers from numbers repaired automatically
- uses declared section counts such as `本题共2小题` to detect a missing trailing question that cannot be found from `max(seen)` alone
- presents empty missing/duplicate results as `无` and localizes automatic-repair codes in HTML while retaining stable machine codes in JSON

Treat duplicate/missing question numbers and missing image assets as blocking structure failures. Treat missing answers, missing solutions, malformed option sets, answer/solution numbering drift, likely answer-solution mismatch, OCR-contaminated derivations, and unknown types as review warnings. Do not invent missing content.

The structure report keeps compatibility fields (`seen`, `missing`, `duplicates`, `repairs`) and also records `structure_before_repair`, `structure_after_repair`, `resolved_missing`, `resolved_duplicates`, and human-readable `repair_details`. `contains_images` and `answer_math_display_repaired` are informational and must not by themselves downgrade the overall structure status to `warn`. Treat `answer_contamination` as a review warning and fix the extractor before accepting the bank.

For downstream knowledge-first AI-native recommendation, preserve provenance instead of hiding uncertainty. When the extractor sees evidence that a solution belongs to another item, conflicts with the answer key, omits the core derivation, or contains truncated/hallucinated OCR math, record stable warnings such as `solution_alignment_warning`, `answer_solution_mismatch`, `source_solution_invalid`, or `ocr_solution_contamination` on the question and in the structure report. These warnings must survive tagging so the recommender can exclude the item from teaching-chain evidence and structure anchors until a teacher repairs it.

`--tag` automatically bridges a non-failing structure into the sibling `smart-question-tagger` skill. The canonical implementation lives in `smart-question-tagger/scripts/tag_structured_bank.py`; this skill's command remains a backward-compatible proxy. To run that bridge separately:

```powershell
python scripts\tag_structured_outputs.py structured_bank
```

This writes `tags/`, `index/`, `review/`, `audit/`, and `tagging_summary.json`. It preserves `tags_reviewed: false`; rule tags remain teacher-review drafts.

### Pre-Tag Acceptance Gate

Do not hand a question to the tagger when any of these flags is present:

- `missing_image_asset`
- `answer_contamination`
- `markdown_math_loss`

The bridge enforces this gate even if `structure_report.json` is missing or stale. Missing answers/solutions, malformed option sets, unknown question types, source answer/solution warnings, and non-blocking display repairs may still be tagged, but must remain review candidates and must not be marked safe for generated-question anchors. Preserve `display_id` in tag outputs and keep `question_id` as the stable join key.

`tagging_summary.json` must include the confidence minimum, maximum, average, confidence bands, and review-reason counts. Never use a review threshold above the classifier's achievable confidence range: if every rule tag falls below the threshold, fix or calibrate confidence before attempting to review the entire bank.

The current bridge expects evidence-scored tags from `smart-question-tagger`: `tag_evidence`, ranked `tag_candidates`, `classification_margin`, and per-field `tags_confidence_detail`. It preserves these fields and selects the active review queue from confidence, source-quality warnings, and images while leaving `tags_reviewed: false` until teacher confirmation.

### Tagging Optimization Roadmap

1. **Separate label dimensions.** Score `curriculum_theme`, `knowledge_unit`, `primary_knowledge`, `skill_tags`, `ability_tags`, and `difficulty` independently; do not use one confidence value to hide which field is uncertain.
2. **Attach evidence.** Each rule label should record matched phrases/formulas and the competing second-best label. Confidence should use evidence strength, taxonomy consistency, and the margin between the top two candidates.
3. **Calibrate on teacher labels.** Review a stratified seed set across themes, question types, images, and confidence bands; fit thresholds from measured precision instead of choosing `0.8` by convention.
4. **Triage review.** Send taxonomy conflicts, missing content, image-dependent questions, and close label margins to high priority; sample medium-confidence tags; auto-pass only calibrated high-confidence tags. Never mark `tags_reviewed: true` automatically.
5. **Handle images selectively.** Distinguish “contains a decorative/solution image” from “the image is required to classify the stem”; only the second should force review.
6. **Learn from corrections.** Store teacher changes as calibration fixtures, add them to regression tests, and report per-field precision/coverage plus review workload on every run.

## Word To PDF

If the input is `.docx` or `.doc`, convert it to PDF before uploading to MinerU. The main OCR script does this automatically and stores generated PDFs under `<cache_dir>/_converted`:

```bash
python scripts/ocr_exam.py input.docx -o output_dir
```

To pre-convert a Word file without calling MinerU or needing an API token, run:

```bash
python scripts/convert_to_pdf.py input.docx -o input.pdf
python scripts/convert_to_pdf.py input.docx -o input.pdf --prefer libreoffice
python scripts/convert_to_pdf.py input.docx -o input.pdf --prefer docx2pdf
```

Prefer `docx2pdf` when Microsoft Word is installed because it preserves Word layout most faithfully. Use LibreOffice when Word is unavailable or when converting older `.doc` files. On Windows, the converter checks `LIBREOFFICE_PATH`, `soffice`/`libreoffice` on `PATH`, and common LibreOffice installation paths.

## Local Polish Only

When the user says not to call the API, or asks to make existing OCR output look better, run only:

```bash
python scripts/polish_outputs.py output_dir
```

This does not contact MinerU. It updates `.md` and `.html` files in place, backs up the pre-polish files to `<output_dir>/_raw_mineru`, and writes `<output_dir>/ocr_warnings.json` plus `<output_dir>/ocr_warnings.md`.

The polish step:

- widens HTML into a clean paper-like reading layout
- rebuilds HTML from same-name Markdown when available, because MinerU Markdown often preserves LaTeX better than `full.html`
- keeps body text, list items, options, table cells, and inline formulas at a uniform `16px`
- gives MathML a consistent math font stack, keeps block formulas at the same base size as text, and avoids card-like formula backgrounds
- loads MathJax in HTML so TeX and MathML render correctly even when the browser's native MathML support is incomplete
- detects obvious repeated OCR hallucination tails, truncates the polluted tail, and inserts an `OCR异常` warning instead of presenting invented math as valid content
- records truncated OCR fragments in `ocr_warnings.json` for programs and `ocr_warnings.md` in Chinese for manual review, so the main Markdown/HTML stays clean but the source problem remains traceable
- keeps raw answer/solution fragments and source-quality warning codes available for downstream teacher review instead of silently rewriting an inconsistent解析 into a clean-looking result
- fixes narrow OCR glyph glue such as `Qg'(x)` when it clearly means `g'(x)` inside derivative notation
- fixes set-builder TeX such as `\left\{x\left| ... \right\}` to use `\mid`, preventing MathJax `Extra \left or missing \right` errors
- normalizes any remaining unbalanced `\left` / `\right` pair by downgrading stretch delimiters to plain delimiters before HTML generation
- uses a neutral black/gray palette so body text, formulas, option labels, and table text have the same ink color
- reserves light gray only for section, option, and table-header backgrounds; formulas should look like normal exam content
- converts raw LaTeX fragments left in HTML text nodes (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`) into MathML with `latex2mathml`
- improves line height, margins, print styles, and mobile behavior
- upgrades section paragraphs such as `四、解答题` into headings
- turns inline answer links into answer badges
- formats tables, images, and block formulas
- splits multiple-choice options into a two-column grid in HTML
- splits option lines and question numbers in Markdown
- verifies Markdown image references remain valid

## Local Audit Only

When the user asks whether polished files differ from the cache/raw MinerU output, run:

```bash
python scripts/audit_outputs.py output_dir
```

This does not call MinerU. It compares polished files against `<output_dir>/_raw_mineru` and, when present, `<output_dir>/mineru_cache`.

The audit returns `status: pass | warn | fail`. By default it returns non-zero only for blocking failures; use `--strict` to return non-zero for warnings too.

Blocking failures:

- raw backup is missing
- compacted body text differs from raw MinerU text
- MathML `annotation` values differ
- image `src` values differ
- table cell text differs
- generated option labels are empty
- polished HTML still contains raw LaTeX fragments without MathJax protection
- polished HTML still contains TeX delimiter patterns or unbalanced `\left` / `\right` pairs that can trigger MathJax errors
- Markdown-derived HTML no longer matches the same-name polished Markdown
- Markdown image references are missing
- `_raw_mineru/*.html` cannot be found byte-for-byte in cached `full.html` files when cache exists

Warnings:

- polished HTML still contains suspicious repeated OCR hallucination text
- polished HTML still contains known OCR glyph glue such as `Qg'(x)`
- polished HTML contains `OCR异常` markers from the polish step

## Local Quality Report Only

When the user needs a machine-readable handoff report without calling MinerU, run:

```bash
python scripts/quality_report.py output_dir
python scripts/quality_report.py output_dir --audit-report output_dir/mineru_cache/audit_report.json
```

This writes:

- `<output_dir>/_reports/quality_report.json`
- `<output_dir>/_reports/quality_report.html`

The quality report summarizes output pairs, file sizes, formula/image counts, missing images, OCR warning markers, audit status, warnings, and blocking failures. Its default exit code is non-zero only for `fail`; use `--strict` to make `warn` non-zero.

`_reports/*` is the overall acceptance summary. `ocr_warnings.md` is the human-readable OCR abnormal-fragment detail. Keep both: they serve different readers.

## Useful Options

```bash
python scripts/ocr_exam.py input.pdf -o out --cache-dir cache
python scripts/ocr_exam.py input.pdf --model-version pipeline
python scripts/ocr_exam.py input.pdf --language en
python scripts/ocr_exam.py input.pdf --interval 10 --timeout 1800
python scripts/ocr_exam.py input.pdf --upload-workers 4
python scripts/ocr_exam.py input.pdf --force
python scripts/ocr_exam.py input.pdf --no-ocr
python scripts/ocr_exam.py input.pdf --no-polish
python scripts/ocr_exam.py input.pdf --no-audit
python scripts/ocr_exam.py input.pdf --no-report
python scripts/convert_to_pdf.py input.docx -o input.pdf
python scripts/convert_to_pdf.py input.docx -o input.pdf --prefer libreoffice
python scripts/polish_outputs.py out --refresh-backup
python scripts/polish_outputs.py out --no-backup
python scripts/audit_outputs.py out --json-out out/audit_report.json
python scripts/audit_outputs.py out --strict
python scripts/quality_report.py out
python scripts/quality_report.py out --strict
```

Use `--model-version vlm` by default for scanned/math-heavy exams. Use `pipeline` only when MinerU VLM output is worse for a specific document.

## Outputs

For each input document, deliver:

- `<output_dir>/<original-name>.md`
- `<output_dir>/<original-name>.html`
- `<output_dir>/images/*` when Markdown references extracted images
- `<output_dir>/ocr_warnings.json` with warning markers and truncated OCR fragments for machine use
- `<output_dir>/ocr_warnings.md` with the same OCR warning details in Chinese for manual review
- `<output_dir>/_reports/quality_report.json` and `<output_dir>/_reports/quality_report.html` for downstream validation
- `<output_dir>/_raw_mineru/*` with pre-polish Markdown/HTML
- `<cache_dir>/<batch_id>/` with submit payloads, polling JSON, final result JSON, downloaded ZIPs, and extracted MinerU package contents
- `<cache_dir>/<batch_id>/audit_report.json` with post-polish content-preservation checks

When structuring is requested, also deliver:

- `<structured_dir>/questions.jsonl`
- `<structured_dir>/questions/<question_id>/question.json`
- `<structured_dir>/questions/<question_id>/preview.html` and `assets/*`
- `<structured_dir>/manifest.json`, `index.html`, and `structure_report.json/html`
- `<structured_dir>/tags/*`, `review/*`, `index/*`, and `audit/*` after tagging

Run the structurer regression tests after changing question splitting, answer rendering, or report generation:

```powershell
python -m unittest discover -s tests -v
```

## Troubleshooting

- `401 user authenticate failed`: token is missing, expired, or includes unsupported characters. Re-set `MINERU_TOKEN`.
- `SignatureDoesNotMatch` during upload: use `scripts/ocr_exam.py`; it sends a bare PUT with only `Content-Length`.
- Missing HTML: ensure `extra_formats=["html"]` is enabled; the main script enables it by default.
- Word conversion fails: install Microsoft Word plus `docx2pdf`, or install LibreOffice for the fallback converter.
- Polish fails because `bs4` is missing: `scripts/polish_outputs.py` attempts to install `beautifulsoup4`; install it manually if the environment blocks pip.
- Raw `$...$` formulas appear in HTML: re-run `scripts/polish_outputs.py`; it attempts to install `latex2mathml` and converts leftover LaTeX to MathML.
- Formulas appear as flat text like `e x` or `( x (0,) )`: re-run `scripts/polish_outputs.py`; polished HTML is rebuilt from Markdown and uses MathJax rather than depending on native MathML.
- `Extra \left or missing \right` appears in HTML: re-run `scripts/polish_outputs.py`; it normalizes common set-builder TeX such as `\left\{x\left| ... \right\}` and downgrades any remaining unbalanced `\left` / `\right` pair to plain delimiters.
- Repeated text such as many `p(x)>0`, `e^{w}`, or `p(x)\geq e^{-u}(x)` fragments means the MinerU source OCR is polluted. Re-run `scripts/polish_outputs.py`; if an `OCR异常` warning remains, verify that part against the original PDF instead of trusting the generated answer.
- Audit fails after polish: inspect `audit_report.json`. Prefer fixing `scripts/polish_outputs.py` over editing OCR text by hand.
- MinerU returns `failed`: inspect `<cache_dir>/<batch_id>/final_result.json` and report the file's `err_msg`.
