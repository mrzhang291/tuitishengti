# HTML/Markdown Tagging Workflow

Use this reference when the input is a structured exam or practice document in `.html` or `.md` form.

## Project Command Pattern

Prefer the current repository's production scripts when they exist. They are shared project resources, not files owned by this skill. Keep concrete paths here instead of in `SKILL.md`.

Use explicit input and output paths from the current project; the Skill does not assume a repository name or school folder:

```powershell
python scripts\build_html_homework_question_bank.py `
  --input-root <converted-exam-root> `
  --output <structured-output> `
  --scope all

python scripts\tag_structured_bank.py <structured-output>
```

For already split JSON question banks handled by the skill's bundled runner, use a skill-relative path:

```powershell
python scripts\run_tagger.py --workspace . --scope full --output output\full
```

If the project has a newer build/import script, prefer the project script but keep outputs compatible with `references/tagging-contract.md`.

## 1. Split Documents Into Questions

Expected source layout usually contains one question paper and optionally one answer/solution file per exam folder. Prefer Markdown as the text source and HTML as the preview source. Preserve image placeholders and asset records; do not send raw base64 image data into LLM prompts.

Target output:

```text
output-root/
  questions.jsonl
  questions/<question_id>/question.json
  questions/<question_id>/preview.html
  index.html
  manifest.json
```

Each `question.json` should include `question_id`, `source_exam`, `source_file`, `question_number`, `question_type`, `stem_markdown`, `stem_html`, `options`, `answer`, `solution_markdown`, `solution_html`, `score`, `assets`, and `quality_flags`.

### Known Splitting Pitfalls

Use these checks before trusting numbered-question splitting:

- Skip answer-card sections embedded inside the question paper. Converted Word papers often place a table for choice/fill-in answers after the fill-in section and before the solution section. Do not treat its numbered blanks as questions. Remove blocks headed like `答题卡` or blocks that are only `1. ____ 2. ____ ...`.
- Do not stop parsing after fill-in questions. Some papers put the answer-card table between fill-in and solution questions; after skipping the table, continue splitting later numbered solution questions.
- Treat section instructions as split boundaries, not stem content. Lines such as `二、选择题：本题共...`, `三、填空题：...`, and `四、解答题：...` often appear immediately after the last question of the previous section in converted Markdown. Strip them from the previous stem and use them only to infer type/score.
- Record question scores for exam papers when section score rules are available. For solution sections, preserve per-question scores by order when the paper gives a total section and known allocation, rather than leaving all solution scores blank.
- Treat headings plus numbers as valid starts. Converted Markdown may emit question starts like `### 13.` or `<img ...>13.`. The splitter should accept optional heading markers and leading image tags before the question number.
- Detect duplicate or fake question numbers. Answer-card tables, score tables, and option grids can create repeated numbers. Report duplicates in `manifest.json`; do not silently keep all repeated blocks.
- Validate missing numbers per exam folder. If a number is absent, inspect the source before inventing a record. Only repair a missing number when the full stem can be located by a reliable source anchor.
- Repair OCR/Markdown断号 narrowly. If a question number is missing but the stem is clearly attached to the previous block, first compare the main `.md/.html` with `_textin` cache or other structured source, then split by a distinctive original-text anchor and document the repair in code. Keep such repairs source-specific rather than broad regex guesses.
- Pair question and solution files conservatively. Prefer files whose names indicate `试题`/`试卷` for questions and `答案`/`解析`/`参考答案` for solutions. If no solution file exists or no matching solution block is found, keep the question and add `missing_solution`.
- Preserve image records but flag them. Keep `<img>` placeholders/assets in structured output, avoid sending raw base64 into prompts, and add image-related quality flags so teacher review sees that visual information may affect tags.
- Materialize per-question image assets. If a stem or solution contains an image, save it under `questions/<question_id>/assets/`, rewrite Markdown/HTML references to local relative paths, and verify `preview.html` renders the image. Image-dependent stems without saved assets are not usable for teacher review or tagging.
- Strip answer-card text from both question and solution sources before extracting answers. Otherwise answer tables may pollute stems, duplicate question numbers, or create false answers.
- Render formulas, Markdown tables, and answer tables in previews. `preview.html` should load MathJax or an equivalent renderer, and answer fields containing Markdown tables should be rendered through the same Markdown-to-HTML path as stems/solutions.
- After splitting, inspect `manifest.json` and quality flags before tagging. A clean run should have expected counts, no duplicates, and any missing numbers explicitly explained.

### Answer Extraction Pitfalls

Keep `answer` answer-only; all reasoning belongs in `solution_markdown` and `solution_html`.

- Prefer source answer summary lines/tables when present. For example, a line like `单选题：ACDCBCBA；多选题：ABC，ACD，AD` and `填空题：...` is more reliable for q1-q14 answers than per-question explanation blocks.
- Truncate inline answers at detail markers. Patterns such as `14. 答案【详解】...` or `14. 答案【解析】...` should store only the part before the marker.
- Watch for no-marker explanation blocks. Some answer files write `1.A由...`, `12.(0,1) f'(x)=...`, or `14.(0,6)由方程...`. In these cases, use the answer summary if available; do not keep the explanatory text in `answer`.
- For objective questions, a valid answer is usually only option letters such as `A`, `BD`, or `ACD`. If an objective answer contains long Chinese reasoning, it is polluted.
- For fill-in questions, prefer compact mathematical values such as intervals, equations, or numbers. If a fill-in answer contains derivative computations or "由/因为/所以" reasoning, inspect the source summary or mark for review.
- For solution questions, allow `见解析` only when the answer really cannot be summarized without the full solution; otherwise preserve subquestion answers such as `(1)... (2)...` before `【详解】`.
- When changing answer extraction, rebuild the full bank and then spot-check both `questions/<id>/question.json` and `tags/all_question_tags.json`, because tag snapshots can retain stale answer text unless explicitly synced.

### Post-Build QA Checklist

Run these checks before presenting the bank as ready:

- Confirm counts: `questions.jsonl`, `tags/all_question_tags.json`, and review CSV should have the same question count.
- Scan question JSON for section instructions: `本题共`, `选择题：`, `多选题：`, `填空题：`, `解答题：`, `多项符合题目要求`.
- Scan answer fields for leaked reasoning markers: `【详解】`, `【解析】`, and suspicious long objective/fill-in answers.
- Open or inspect representative `preview.html` files for image questions, table questions, and formula-heavy questions.
- After any rebuild, regenerate draft tags, import them, merge selective Codex-agent reviews, sync snapshots, refresh JSONL/index/review CSV, and rerun validation.

## 2. Rule-Based Gaokao First Pass

Run local rules first. They should produce curriculum-aligned fields before legacy fields:

- `curriculum_theme`
- `knowledge_unit`
- `skill_tags`
- `primary_knowledge`
- `secondary_knowledge`

Use this layer for deterministic coverage, indexes, review CSVs, and smoke tests. Treat it as a teacher-reviewable draft.

## 3. Selective Codex-Agent Review

Use the current Codex agent as the default LLM review layer after rule-based tagging. Review only candidates that are likely to benefit from model judgment:

- `needs_teacher_review: true`
- low `tags_confidence`
- image-dependent stems
- missing answer or missing solution
- high difficulty
- derivative optimization with geometry context
- probability/statistics questions where expectation or variance may be the real method

Write response-shaped JSONL, import it, and label the source as `codex_agent_review_v1`. Do not mark `tags_reviewed: true`; teacher review remains separate.

## 4. Optional Local Or Online LLM

Export prompt packets with the strict JSON schema only when a separate local model, manual offline review, or online API review is requested. Local/offline responses may be produced manually or by a local model. Online LLM/API tagging is not part of the default workflow. Use it only when the user asks for it and credentials are available. Keep raw model/API responses for audit.

## 5. Validation And Handoff

Run validation after every import. Review these first:

- image-dependent questions
- missing answers or missing solutions
- low-confidence tags
- derivative optimization with geometry context
- probability/statistics questions where expectation is the real method

Teacher-reviewed tags are the only final tags. Until then, outputs are calibrated drafts.
