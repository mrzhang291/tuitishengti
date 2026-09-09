# Tagging Contract

## Input Sources

The skill supports two input levels:

- Structured `.html`/`.md` exam or practice documents that need question splitting.
- Already split `question.json` or `questions.jsonl` files.

For document inputs, the pipeline should create per-question artifacts before tagging. For scored exams, include `score_rate`, `avg_score`, and `full_score` when available.

## Question Fields

Each structured question should contain:

- `question_id`
- `source_exam`
- `source_file`
- `question_number`
- `question_type`
- `stem_markdown`, `stem_html`
- `options`
- `answer`
- `solution_markdown`, `solution_html`
- `score`
- `assets`
- `quality_flags`

## Tag Fields

The `tags` object should contain:

- `curriculum_theme`: Gaokao curriculum theme such as `T7导数`.
- `knowledge_unit`: curriculum unit such as `U7.4 导数与极值/最值`.
- `skill_tags`: concrete skills, preferably 1-3 strings.
- `primary_knowledge`: main solution method retained for recommender compatibility.
- `secondary_knowledge`: supporting knowledge contexts, preferably `T/U/技能` strings.
- `ability_tags`: model/rule ability tags.
- `difficulty`: integer 1-5.
- `difficulty_basis`: `score_rate xx.x%` when scored, otherwise explicit estimate source.
- `teaching_tag`: teaching use category.
- `error_prone_points`: common mistakes.
- `prerequisite_knowledge`: prerequisite topics.
- `tag_reasoning`: short rationale.
- `tags_generated_by`: generation source.
- `tags_reviewed`: true only after teacher review.
- `tags_confidence`: generated confidence score.
- `tags_confidence_detail`: per-field confidence for theme, unit, primary knowledge, skills, abilities, difficulty, and structure.
- `tag_evidence`: matched phrases/formulas and their source/weight.
- `tag_candidates`: ranked primary-knowledge candidates with scores.
- `classification_margin`: normalized top-1 versus top-2 margin.
- `needs_teacher_review`: boolean.
- `review_reasons`: machine-readable reasons for entering the active review queue.

## Difficulty Calibration

Use score-rate thresholds for scored questions:

- `score_rate > 90`: difficulty 1
- `score_rate > 70`: difficulty 2
- `score_rate > 50`: difficulty 3
- `score_rate > 30`: difficulty 4
- otherwise: difficulty 5

When score-rate is absent, keep difficulty as rule/model estimated and state that reliability is lower.

`tags_confidence` measures curriculum/knowledge classification confidence. Keep difficulty reliability in `tags_confidence_detail.difficulty`; do not force every otherwise-clear tag below the review threshold merely because score-rate is absent.

## Review Handoff

The CSV review sheet should contain model fields and empty teacher fields. Teachers can fill:

- `primary_knowledge_teacher`
- `difficulty_teacher`
- `review_status`
- `review_notes`

Generated tags are teacher-reviewable drafts until review fields are populated. Do not silently overwrite teacher-reviewed fields.

An unreviewed draft does not automatically belong in the active review queue. Queue selection should be driven by confidence, classification margin, taxonomy conflicts, image dependence, and source-quality flags; `tags_reviewed` remains false for non-queued drafts until a teacher explicitly approves them.

### Teacher Decision JSONL

Each exported decision must contain:

- `schema_version`: `teacher-review-v1`
- `question_id`, `display_id`
- `decision`: `approve`, `change`, or `defer`
- `source_signature`: hash of the source tag fields used to reject stale decisions
- `reviewer`, `reviewed_at`, `review_notes`
- `teacher_tags`: `primary_knowledge`, `secondary_knowledge`, `ability_tags`, and `difficulty`

For `change`, require a canonical `primary_knowledge`, derive its theme/unit/skills from `PRIMARY_KNOWLEDGE_META`, and validate ability tags and difficulty. For `approve` and `change`, set `tags_reviewed: true` and store the decision under `tags.teacher_review`. Preserve model confidence and evidence as provenance. Teacher-approved labels supersede model uncertainty flags, but taxonomy validity remains mandatory.

## Student Performance Contract

Question-level aggregates should contain `question_id`, `display_id`, source exam, question number, `attempts`, `correct`, `wrong`, `omitted`, `correct_rate`, difficulty, and primary knowledge. Require `correct + wrong = attempts`; omitted answers remain outside attempts.

Student-level records should contain `student_id`, name/class, and an `answers` array with `question_id`, `attempted`, `is_correct`, `score`, and `full_score`. This level is required for later personalized recommendations; aggregates alone are insufficient.

Set `simulated: true` and include an explicit simulation note on generated development data. Real imports must set `simulated: false` and preserve source/provenance fields.
