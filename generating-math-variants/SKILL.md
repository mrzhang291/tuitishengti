---
name: generating-math-variants
description: Load when the user asks to generate high-school math variant questions from an original problem or document, especially same-difficulty variants with answers, solutions, variation notes, relative difficulty rationale, exact 1-5 difficulty labels, or structural-duplicate feedback.
---

# Generating Math Variants

## Overview

Generate reviewable Chinese high-school math variants from an original question. Default to three same-difficulty candidates and recommend the best one, using relative difficulty comparison rather than unsupported measured-difficulty claims.

This Skill handles an ad-hoc mother-question variation request. The production teacher workbench is owned by `smart-question-recommender-v3`: it starts from a knowledge-point teaching chain, then uses multi-question teaching evidence, one structure anchor, Cherry Studio drafting/verification, exact 1-5 difficulty gates, pedagogical fingerprints, history deduplication, teacher-feedback memory, and teacher approval. Do not bypass that workflow by copying this Skill's conversational candidates into the live workbench or presenting them as already verified.

## Input Handling

- If the user provides a document, first extract the relevant question text, answer, and solution with the appropriate document/PDF/image-reading tools available in the session.
- For diagram-dependent questions, extract the visual-only facts into explicit mathematical conditions before generating variants. If the diagram or asset description is missing or insufficient, ask for the diagram or state the missing condition; do not leave an unresolved "as shown" dependency in final candidates.
- If a document contains multiple questions and the user did not specify which one to vary, list the detected questions briefly and ask which to use.
- If the original answer or solution is missing, derive it before generating variants and state that the original solution was inferred.
- If the original answer and solution conflict, solve the original from the stem, mark `source_solution_invalid`, and do not use the flawed solution as the solution skeleton.
- Preserve LaTeX and mathematical notation. Do not invent diagrams, image references, or external assets.

## Core Workflow

1. Parse the original question.
   - Identify question type, stem, options if any, answer, solution path, primary knowledge point, secondary knowledge points, common error points, any visual conditions required to solve the problem, and any source constraints on formulas, tables, models, definitions, or data.

2. Compare difficulty relative to the original.
   - Do not output measured difficulty coefficients or claim real student score rates.
   - Describe difficulty by comparing structural features: knowledge count, reasoning steps, computation burden, parameter use, classification discussion, construction/transformation, hidden conditions, abstraction level, and likely misconceptions.
   - Keep difficulty separate from variation breadth. A pure or proportional number change can still be "Similar" in difficulty if it preserves the same reasoning steps, computation burden, and error traps; mark it as low-variation breadth in the variation note rather than calling it easier.
   - When an exact 1-5 level is requested, use the same AI-native rubric as the recommender: level 1 is direct formula or one step, level 2 is one knowledge point with a simple transformation, level 3 has 2-3 connected steps with a real boundary or equivalence check, level 4 uses parameter/interval/classification/multiple conclusions, and level 5 requires at least three high-complexity signals such as existence, range proof, multi-part linkage, higher-order analysis, or auxiliary-function reasoning.
   - Basic derivative formulas such as `sqrt(x)+1/x` are at most level 2. A routine `x^2+c/x` monotonicity or extremum task is usually level 3. Do not promote a question to level 5 just because it is multiple choice or visually dense.

3. Build a difficulty-preservation contract.
   - List what must stay: core knowledge, main solution method, key difficulty source, at least one original error-prone point, and any externally sourced or reality-framed model/data structure.
   - List what may change: numbers, expressions, condition order, setting, question angle, equivalent representation, or event definition. Change a formula/table/model structure only when the original is clearly a hypothetical setup, or when the candidate explicitly reframes it as a hypothetical model.
   - List what must not happen: direct paraphrase, unintentional pure number swap, collapse into one-step substitution, changing a reality-framed research/data/model structure as if it were arbitrary, or adding unrelated advanced techniques. If a conservative number-change candidate is useful, label it as low-variation breadth and prefer recommending a candidate with a meaningful angle or condition change when difficulty is also similar.

4. Generate three candidates with distinct teaching-chain roles.
   - Candidate A, 原型巩固: keep the solution skeleton and change values, expressions, or condition presentation without collapsing the reasoning.
   - Candidate B, 同法变式: keep the knowledge point and key difficulty source but alter the question angle or condition organization.
   - Candidate C, 迁移提升: keep the core method while moving to a nearby representation or modest new context, without rewriting externally sourced models or data unless the new candidate clearly states a new hypothetical setup.

5. Self-solve each candidate.
   - Solve the generated question independently and verify the stated answer matches the solution.
   - Reject or repair candidates with ambiguous conditions, inconsistent answers, invalid options, missing domains, or unsupported conclusions.
   - Record `independent_verification.status: passed` and `answer_matches: true` only after a separate stem-only solve. A statement such as “已检查” without a second solution is not verification.

6. Run the same-difficulty check.
   - Compare each candidate against the original on the preserved difficulty dimensions.
   - Penalize candidates that are easier because they remove parameter handling, classification discussion, construction, multi-step reasoning, or the original misconception trap.
   - Penalize candidates that are harder because they add extra knowledge points, nonstandard methods, excessive computation, or unfamiliar contexts not present in the original.
   - Record a pedagogical fingerprint for each candidate: knowledge point, question type, exact difficulty level when present, method family, function/object family, task intent, reasoning pattern, option pattern, and complexity signals.
   - Reject candidates whose fingerprint repeats the original or another candidate when the user asked for structural variety. Different text with the same “basic derivative + single choice + direct formula” fingerprint is still a duplicate.
   - If all three candidates are too easy or invalid, generate one repair round. If still unsatisfactory, do not force a recommendation; explain why regeneration is needed.

## Output Contract

Return this structure:

```markdown
## Original Analysis
- Question type:
- Knowledge points:
- Solution skeleton:
- Key difficulty reference points:
- Error-prone points:

## Candidate 1
**Question**

**Answer**

**Solution**

**Variation note**

**Relative difficulty note**

## Candidate 2
...

## Candidate 3
...

## Recommendation
- Recommended candidate:
- Reason:
- Same-difficulty check:
- Teacher review notes:
```

Keep solutions concise but complete enough for teacher review. For choice questions, include options and ensure the answer key matches exactly one option for single choice, or the intended set for multiple choice.

When the result will be saved or handed to another Skill, use JSON with `original`, `target`, exactly three `candidates`, and `recommendation`. Each candidate must include `candidate_id`, `question_type`, `stem_markdown`, `options`, `answer`, `solution_markdown`, `changed_dimensions`, `variation_note`, `relative_difficulty`, `pedagogical_fingerprint`, `answer_solution_consistency`, and `independent_verification`. If the teacher asks for a numbered 1–5 difficulty, write one exact integer in `target.difficulty_level` and every candidate; never write a range such as “2–3级”. When handing candidates to `smart-question-recommender-v3`, also include recommender-compatible `recommendation_score` components or a plain `recommendation.rationale` that explains source safety, stage fit, difficulty fit, structural novelty, and any duplicate-structure risk.

Run the deterministic structural gate before handoff:

```powershell
python scripts/validate_variants.py variants.json
python -m unittest discover -s tests -v
```

This gate checks completeness, requested question type, exact difficulty label, option shape, image self-containment, structural changes, source/candidate similarity, and the independent-verification record. It does not replace mathematical teacher/model review.

## Relative Difficulty Guide

Use these labels only as relative judgments:

| Label | Meaning |
| --- | --- |
| Similar | Best match; core difficulty reference points are preserved with only surface or equivalent structural changes. |
| Slightly Easier | Some computation, reasoning, or misconception pressure is reduced, but the core method remains. |
| Slightly Harder | Adds modest complexity while staying in the same knowledge neighborhood. |
| Not Recommended | Invalid, too similar, too easy, too hard, ambiguous, or mathematically inconsistent. |

Prefer recommending a "Similar" candidate. A "Slightly Easier" candidate can be recommended only if no Similar candidate exists and the user accepts easier practice.

## Gotchas

- Measured difficulty requires real student response data. Never present generated-question difficulty as measured.
- LLMs often make variants easier by removing the original bottleneck. Always identify the original bottleneck before generating.
- A changed number is not automatically easier or harder. Judge difficulty by preserved reasoning pressure; judge variant quality separately by how much the candidate changes the representation, condition organization, or question angle.
- Structure novelty is not wording novelty. If the method family, function family, task intent, reasoning pattern, and option pattern are unchanged, mark the candidate as structurally repeated even when coefficients or names changed. In a production teaching chain, do not fill 原型巩固、同法变式 and 迁移提升 with the same fingerprint.
- A source解析 that does not solve the stated stem is not a valid mother-question skeleton. Re-solve the source and disclose the inconsistency before generating.
- Treat reality-framed models, research findings, official definitions, experimental data, and given statistical tables as source constraints, not arbitrary algebraic material. Preserve their structure by default; vary the event, parameter value, question target, or interpretation task instead. If changing the model structure is pedagogically useful, rewrite the background as a hypothetical setup and mark the candidate as less faithful to the original context.
- For diagram-dependent geometry, convert every visual-only condition into explicit mathematical text before generating variants. If a final candidate says "as shown" but no diagram is supplied, the candidate is incomplete.
- A harder-looking question with longer algebra may be worse, not better. Prefer preserved reasoning difficulty over added calculation.
- For same-difficulty generation, changing both knowledge point and solution method usually breaks the reference basis.
