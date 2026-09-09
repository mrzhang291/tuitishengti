---
name: smart-question-tagger
description: Load when the user needs to split, tag, audit, calibrate, source-quality review, or teacher-review local school math question banks, including exam-ocr structured directories containing questions.jsonl, and prepare question-level teaching evidence plus optional student performance data for later knowledge-first AI-native recommendation.
---

# Smart Question Tagger

Use this skill to produce tagged school-math question-bank artifacts. It owns data production: splitting structured `.html`/`.md` papers or already split question files, creating rule-based Gaokao-aligned tags, checking source answer/solution quality, selectively reviewing risky tags with the current Codex agent, and preparing teacher-review outputs.

Do not implement ranking or paper assembly inside this skill. After tags and source-quality review are stable, invoke the sibling `smart-question-recommender-v3`; student records are optional and only affect the “按学情生成” layer. This skill embeds both intelligent generation and the class paper into the shared teacher workbench.

## Workflow

1. Identify the input shape: `exam-ocr-structured`, `html/md`, `question-json`, or `tags-only`.
2. For `exam-ocr-structured`, run `scripts/tag_structured_bank.py <structured-dir>` as the canonical one-command handoff. Do not duplicate its tagging and audit orchestration in another skill.
3. For `html/md`, read `references/html-md-tagging-workflow.md`, then split into per-question artifacts before tagging.
4. Run structural QA before trusting tags: counts, answer-only fields, answer-solution consistency, image assets, formula/table previews, section-heading leakage, and score fields.
5. Run local rule tagging first. Always classify in this order: `curriculum_theme` -> `knowledge_unit` -> `skill_tags` -> `primary_knowledge`/`secondary_knowledge`. Record matched evidence, top candidates, and their margin.
6. Use the current Codex agent as the default LLM calibration layer only for low-confidence or high-risk questions. Keep local/offline models and online APIs optional.
7. Validate outputs and produce teacher-review CSVs. Never set `tags_reviewed: true` without teacher confirmation.

## Resources

- Read `references/html-md-tagging-workflow.md` for document splitting, project command patterns, answer extraction pitfalls, and post-build QA.
- Read `references/knowledge-taxonomy.md` when assigning or auditing Gaokao curriculum themes, knowledge units, skill tags, and legacy knowledge fields.
- Read `references/tagging-contract.md` when exact question/tag fields, difficulty calibration, source-quality flags, or review handoff rules matter.
- Bundled scripts in `scripts/`:
  - `tag_structured_bank.py` — canonical `exam-ocr`/`questions.jsonl` handoff; writes tags, indices, review CSV, summary, and audit packets in place
  - `build_review_workbench.py` — builds the teacher-facing review and student-performance UI with prioritized queues, evidence, candidate comparison, and friendly backup controls
  - `apply_teacher_review.py` — validates exported decisions, backs up current tags, applies approvals/changes, then rebuilds indices, CSVs, audit packets, and the remaining queue
  - `review_server.py` — localhost-only teacher mode with automatic draft saving, friendly backups, and in-page application of approved labels
  - `generate_student_performance.py` — creates clearly marked simulated student records plus per-question correct/wrong/omitted aggregates for workbench and recommendation development
  - `build_review_workbench.py` also refreshes and embeds sibling recommender output from tags alone when possible; student records add optional 学情排序, and teachers use the `智能组卷` tab in the same page
  - 个性新题题型控件必须消费 recommender 槽位声明的 `supported_target_question_types`；`source_question_type`/`question_type` 只是参考题属性，不得据此裁剪新题目标题型。旧候选池没有能力字段时，只要存在安全参考槽位，就兼容开放单选、多选、填空和解答四类。
  - `build_html_homework_question_bank.py` — HTML/MD → `questions.jsonl` + `questions/<id>/` + `preview.html` + `manifest.json` + `index.html`
  - `tagging.py` + `gaokao_taxonomy.py` — rule-based tagging producing the full tag shape (curriculum-aligned + legacy fields)
  - `audit_tags.py`, `suggest_calibrations.py`, `apply_calibrations.py` — post-tagging audit, external teacher/model override validation, and calibration application; no question ID correction is embedded in the Skill
  - `run_tagger.py` — portable entrypoint for a single/array question JSON; its closed-loop compatibility mode uses this Skill's local `tagging.py`

## End-to-End Pipeline (HTML/MD → Tagged Bank)

For this project's `input/html-homework-database` layout, the canonical pipeline is:

```powershell
# 1. Split HTML/MD papers into per-question artifacts
python .claude/skills/smart-question-tagger/scripts/build_html_homework_question_bank.py `
  --input-root input/html-homework-database `
  --output output/full `
  --scope all

# 2. Tag, index, summarize, and audit the structured bank
python .claude/skills/smart-question-tagger/scripts/tag_structured_bank.py `
  output/full `
  --threshold 0.8
```

Step 1 auto-discovers all exam folders under `--input-root`. Each folder must contain one question Markdown (with `试卷`/`试题`/`周测`/`作业` in the name) and optionally one solution Markdown (with `答案`/`解析`/`参考答案` in the name). Use `--scope pilot --pilot-limit 2` for a portable two-folder smoke test, or repeat `--pilot-case <folder-or-exam-name>` to choose explicit cases. The Skill contains no school-specific pilot folder or filename list.

Step 2 reads `questions.jsonl`, rejects blocking structural flags, applies the evidence-scored taxonomy classifier, and writes `tags/`, `index/`, `review/`, `audit/`, and `tagging_summary.json` under the same bank directory. Use `--no-audit` only for isolated smoke tests.

## Teacher Review Loop

1. Have teachers open `<structured-dir>/review/index.html` directly. Do not expose launch scripts, command-line instructions, or preview/official mode terminology in the teacher UI.
2. Keep the default workbench visually quiet: the header shows only the four task tabs and one `工具` menu. Put reviewer identity, return-to-bank, generated-question import, review backup restore/download, and apply-review actions inside that menu; do not scatter global utility buttons across the header.
3. Review high priority, medium priority, then image/structure-only items. Source answer/solution mismatch, missing derivation, impossible answer, and OCR-contaminated analysis are high priority even when knowledge tags look confident. Use teacher-facing actions: `标签正确`, `保存修改`, or `暂时跳过`.
4. Save every decision in the current browser immediately. Keep technical serialization formats hidden behind `下载审核备份` and `恢复审核备份`.
5. When the workbench is hosted with the optional local service, apply results inside the page: back up current tags under `review/history/`, validate source signatures, apply reviewed labels, rebuild indices/audit, and refresh the remaining queue.

Retain `apply_teacher_review.py --dry-run` as an operator recovery/validation path, not a normal teacher workflow. Preserve reviewed tags on later `tag_structured_bank.py` runs unless `--reset-reviewed` is explicitly passed.

## Cherry Studio 实时新题工作台

共享工作台展示由 sibling `smart-question-recommender-v3` 负责的实时生题时，必须共享一个批次进度，但按题位独立保留已经通过的结果：

- 新题生成依据默认使用 `知识点教学链 -> 多题诊断 -> 出题任务单 -> 单一结构锚点 -> 独立生成与校验`。多道安全题形成教学证据；作答记录只在“按学情生成”时作为后置证据。每个题位的结构锚点必须恰好一个，类型只能是单一道安全母题或一个安全蓝图。
- 只有通过题面-答案-解析一致性门的原题可以进入 recommender 安全题池或作为结构母题。带有 `source_solution_invalid`、`answer_solution_mismatch`、`answer_contamination`、`solution_alignment_warning` 或教师标记 `解析错误` 的题，只能作为待修复材料或诊断风险提示，不得直接锚定新题。
- 目标等级必须按 recommender 的精确 1-5 级合同贯穿工作台展示、请求和记录；不要把 4级/5级合并成 `challenge`，也不要把基础公式求导误标为高阶训练题。
- 读取离线教学链候选时，优先消费 `candidate_ranking`、`generation.recommendation_score`、`generation.pedagogical_fingerprint`、`verification.estimated_level` 和 `verification.difficulty_matches`。这些字段只用于预览、解释和实时生成前换结构；`difficulty_matches=false` 或 `duplication_risk=high` 的缓存候选不得显示为可直接通过的新题。
- 工作台默认采用渐进展开：每个页签只暴露一个主任务和一个主按钮，导入、导出、恢复、打印、技术字段和批量设置统一放入 `工具`、`更多操作`、`更多生成设置` 或 `调整组卷要求` 折叠区。优化界面时不得删除功能或改掉既有控件 ID。
- 工作台必须分别展示“教学/诊断依据 N 道”“出题任务单”和“结构锚点 1 个”，并明确多题只用于诊断、不会拼成新题。知识点模式展示教学链阶段，不得称作某个学生的学情。页面进度使用 `知识点教学链与任务单 -> 单锚点草稿 -> Cherry 独立求解 -> 本地硬校验 -> 完成`。
- 普通起草请求最多携带一份结构母题全文；安全蓝图请求不得携带原题全文。Cherry Studio 独立复核只能看到新题题面，不得看到母题答案、蓝图证明答案、任务单预期答案或上一次复核结论。
- 安全蓝图卡片读取 `generation.verification_policy_version` 与 `verification.scope`：Cherry Studio 只独立重算数学正确性，本地硬门单独判难度；页面不得把主观难度意见显示成数学错误。答案展示与比较要兼容纯数值、变量等式/范围、有理数小数/分数及 Unicode/LaTeX 公式的数学等价写法，最终仍统一渲染为规范数学格式。
- 优先读取 `scope: knowledge_practice` 的教学链槽位，以及 `generation.strategy: single_anchor_task_spec`、`diagnostic_evidence_question_ids`、`structure_anchor` 和 `task_spec`。`task_spec.schema_version` 必须为 `generation-task-spec-v2`；诊断摘要读取 `task_spec.diagnostic_profile`，提示边界读取 `task_spec.generation_contract`，锚点 `kind` 只能为 `mother_question` 或 `skill_blueprint`。旧 `reference_question_ids`、`reference_count`、`mother_question_id` 与 `multi_source_synthesis` 仅用于历史读取：分别映射为诊断证据、诊断证据数量和单一原题锚点，并显示“旧版生成依据”；不得用旧语义发起新的多题共同构题。
- 每个题位全程只有 5 次总机会：第 1–3 次初始生成，第 4、5 次分别用于两轮结构补位。补位不得把次数重新从 1 开始，也不得在后台无限尝试。
- 同时最多生成 2 道。3 题整批最多等待 150 秒，其他批次的硬截止也不得超过 180 秒；达到截止后自动结束请求、恢复按钮并给出结果说明。
- 清楚区分“待生成/待补位”和“最终失败”。仍有机会的题位属于 `pending`，不得显示为失败；只有预算耗尽、整批超时或不可重试错误才计入 `failed`。
- 点击“停止生成”时，同时终止浏览器请求和整棵服务端批次。取消根批次必须覆盖它派生的补位子批次，避免退出页面后 Cherry Studio 仍继续生成。
- 单题通过 Cherry Studio 独立求解与本地硬门后立即提交并保留；提交接口按题位调用、支持 1–5 道子集和幂等重试。少一道、超时或用户停止时，只恢复未通过题位生成前的旧题并清理其暂存记录，绝不能撤回同批已经通过的题。提交瞬时失败时在本地保留并标记“保存待同步”，有限重试后仍失败也不得伪装成数学校验失败。
- 进度和结束文案统一显示“已保留 N/M，K 道待补齐”；禁止重复显示“本轮未补齐，结果已撤回”。页面重新渲染、停止生成和超时结算都不得清空已通过题。
- 每道题在等待、生成、校验和右侧试卷中都要显示实际中文题型。智能匹配注明“智能匹配自动分配”，显式题型注明“按教师选择”；不得只显示 `auto`、`single_choice` 等内部字段。
- 在把草稿交给 Cherry Studio 独立校验前先检查题型外形。解答题不得含 A/B/C/D 选项或选择题问法；选择题必须有完整选项；填空题必须有明确空位。题型不符应直接补位，不要让明显错误的草稿占用独立校验时间。
- 服务端每个草稿/校验阶段必须使用一个覆盖其内部修复链的总截止，并保证早于浏览器单次请求截止。这样页面会先收到明确结果，不会因浏览器提前断开而重复提交同一道题。
- 失败提示要面向老师说明“在哪一步、为什么、是否会自动补位”，例如连接未开启、模型返回不完整、题型不符、数学答案不一致、结构重复或达到批次时间上限。禁止只报“JSON 错误”“全部驳回”，也禁止进度按钮一直停在“生成中”。

工作台进度至少显示题面已返回、独立校验中、待补位、已保留与最终失败；只有已独立校验通过的题才能进入右侧试卷。仍为 `pending` 的失败草稿不得进入下载记录或下一批正式去重历史，已经 `committed` 的通过题不得因同批其他题失败而删除。

## Optional Student Performance Data

- Store aggregate question statistics in `student/question_performance.json`: attempts, correct, wrong, omitted, correct rate, difficulty, and primary knowledge. These support 原题错题集 and optional 学情加权, not the default knowledge-first generation entry.
- Store student-level recommendation inputs in `student/simulated_student_records.jsonl` using `student_id`, class, and per-question score/correctness fields.
- Mark generated data with `simulated: true` and show a visible simulation notice in the workbench. Never present simulated results as real student performance.
- Generate development data explicitly with `tag_structured_bank.py <structured-dir> --simulate-students 48`. Do not overwrite real imported performance data unless the user requests it.
- The canonical pipeline refreshes knowledge-first recommendation artifacts plus `paper/class_paper.json` through `smart-question-recommender-v3` before rebuilding the workbench. When student records and per-question aggregates exist, they are added as optional 学情 artifacts. Use `--no-recommendations` only for isolated tagging tests.

## Required Tag Shape

Every `tags` object must preserve recommender-compatible fields and add curriculum alignment:

- `curriculum_theme`, `knowledge_unit`, `skill_tags`
- `primary_knowledge`, `secondary_knowledge`
- `ability_tags`, `difficulty`, `difficulty_basis`, `teaching_tag`
- `error_prone_points`, `prerequisite_knowledge`, `tag_reasoning`
- `tags_generated_by`, `tags_reviewed`, `tags_confidence`, `needs_teacher_review`
- `tag_evidence`, `tag_candidates`, `classification_margin`, `tags_confidence_detail`, `review_reasons`

Use controlled values from `references/knowledge-taxonomy.md`; do not invent a new `curriculum_theme` or `knowledge_unit` without updating that file.

## Confidence And Audit

- Use `gaokao_taxonomy.KNOWLEDGE_TAXONOMY` as the single source for valid `primary_knowledge` values. Audit code must import it instead of maintaining a duplicate allowlist.
- Compute separate confidence values for curriculum theme, knowledge unit, primary knowledge, skill tags, ability tags, difficulty, and structure quality.
- Derive classification confidence from matched evidence strength and the margin between the top two candidates. Store both candidates and evidence for teacher review.
- Keep difficulty confidence separate when score rate is unavailable; low difficulty confidence must not make an otherwise explicit knowledge label impossible to triage.
- `tags_reviewed: false` means the tag is still a draft, not that it must appear in the active review queue. Add a question to the queue only for low confidence, low margin, taxonomy/semantic conflicts, images requiring inspection, or source-quality warnings.
- Never set `tags_reviewed: true` without teacher confirmation.

## Gotchas

- Answer fields must be answer-only. Move `【详解】`, `【解析】`, "由...", derivative computations, and long reasoning into `solution_markdown`.
- A source solution that solves a different problem, gives a conflicting answer, skips the key derivation, or relies on OCR-corrupted formulas must be flagged for teacher repair and excluded from generated-question anchors until fixed.
- Converted Word papers often include answer-card tables between fill-in and solution sections. Skip them and keep parsing later solution questions.
- Section instructions such as `二、选择题：本题共...` are split boundaries, not stem text.
- Image-dependent stems must materialize assets under each question folder and render in `preview.html`.
- Difficulty from missing score-rate is a lower-confidence estimate; surface it for teacher review.
