# 原题错题集与智能新题数据契约

## 目录

- [输入](#输入)
- [原题错题集](#原题错题集)
- [诊断、任务单与结构锚点](#诊断任务单与结构锚点)
- [教学链候选池与旧候选兼容](#教学链候选池与旧候选兼容)
- [Cherry Studio 实时生成](#cherry-studio-实时生成)
- [学生练习与教师记录](#学生练习与教师记录)

## 输入

- `tags/all_question_tags.json`：题库原题、知识标签、置信度、答案和解析。
- `questions/<question_id>/`：题干、答案、解析和图片资源。
- `generation/knowledge_teaching_chains.json`：按知识点生成的教学链计划；可由候选池生成脚本创建。
- `student/question_performance.json`：班级题目作答聚合，仅原题错题集排序、错误率分析或按学情生成需要。
- `student/student_exam_records.jsonl`：真实学生作答，仅按学情生成需要。
- `student/simulated_student_records.jsonl`：仅开发演示使用。
- `student/recommendation_history.jsonl`：历史诊断证据、结构锚点与生成题指纹，可用于去重和反馈记忆。

`attempted: false` 表示没有掌握度证据，不得算作答错。

## 原题错题集

`paper/class_paper.json` 在本产品中是班级原题错题集计划，不是生成新题卷。顶层至少包含：

```json
{
  "schema_version": "class-paper-v1",
  "artifact_role": "source_wrong_question_set",
  "paper_title": "高二数学原题错题集",
  "class_name": "...",
  "questions": [],
  "alternates": []
}
```

`questions[]` 和 `alternates[]` 可以包含原题题干、答案、解析、来源试卷、题号、班级正确率和答错人数。学生打印版只隐藏答案解析，不需要隐藏原题来源身份。

`paper/student_paper.json` 可以镜像所选原题并设置 `artifact_role: source_wrong_question_set`、`student_visible: true`、`requires_new_question_generation: false`。旧班级新题文件 `generation/mother_question_plan.json`、`generation/generated_questions.json` 和 `generation/generated_question_candidates.json` 只保留兼容占位，状态必须为 `not_applicable_source_questions_are_delivered_directly`，且题位、题目和候选均为空。

## 诊断、任务单与结构锚点

`generation/knowledge_teaching_chains.json` 是默认知识点推题入口，提供不依赖学生的教学链阶段、目标等级、题型建议和安全证据。`generation/student_mother_question_plans.json` 保留旧文件名，只在按学情生成时提供学生掌握度与作答证据；缺少该文件不得阻断按知识点生成。`generation/student_generated_question_candidates.json` 是兼容文件名，既可包含 `scope: knowledge_practice` 的教学链候选，也可包含 `scope: student_practice` 的可选学情候选。实时服务读取这些教师端数据后，为每个题位即时固化一份 `generation-task-spec-v2`，不会把旧候选中的多道题全文送入模型。

任务单至少包含：

```json
{
  "schema_version": "generation-task-spec-v2",
  "strategy": "single_anchor_task_spec",
  "task_spec_id": "task-...",
  "target": {
    "knowledge": "利用导数证明不等式",
    "question_type": "solution",
    "question_type_label": "解答题",
    "difficulty": "challenge",
    "difficulty_level": 5,
    "difficulty_label": "5级",
    "training_focus": "迁移应用",
    "training_goal": "构造辅助函数并完成严格证明",
    "required_features": ["定义域", "导数符号链", "等号条件"],
    "forbidden_features": ["A/B/C/D 选项", "数值试根"]
  },
  "diagnostic_profile": {
    "source": "knowledge_teaching_chain",
    "student_specific": false,
    "teaching_chain_stage": "transfer_variant",
    "evidence_count": 5,
    "evidence_question_ids": ["q001", "q002", "q003", "q004", "q005"],
    "evidence_confidence": 0.86,
    "teaching_status": "题库证据充分",
    "evidence_summaries": [
      {"question_id": "q001", "question_type": "single_choice", "reasoning_signals": ["参数或分类讨论"], "usage": "diagnosis_only_no_stem_no_answer"}
    ]
  },
  "structure_anchor": {
    "kind": "mother_question",
    "id": "q003",
    "role": "single_fulltext_structure_anchor"
  },
  "diagnostic_anchor_question_id": "q003",
  "generation_contract": {
    "fulltext_source_question_count": 1,
    "use_only_one_fulltext_anchor": true,
    "forbidden": ["merge_multiple_source_stems", "copy_or_paraphrase_anchor", "numbers_only_change"]
  }
}
```

`diagnostic_profile.evidence_question_ids` 是多题诊断证据，不是共同构题输入。当前工作台每题使用 3–5 道安全证据；按知识点生成没有学生作答时，使用题库标签、解法信号和教学链阶段形成中性教学诊断，不得伪装成某个学生的学情。按学情生成时，`diagnostic_profile.source` 可以为 `student_mastery_profile`，但仍必须保留知识点教学链阶段。

`structure_anchor` 必须恰好一个：

- `kind: mother_question`：`id` 指向一道人审或高置信安全原题。只有这一道原题全文可以进入 Cherry Studio 起草提示，`fulltext_source_question_count` 必须为 1。
- `kind: skill_blueprint`：`id` 指向一个已证明、未耗尽且未被拒绝的安全蓝图族；起草阶段不包含原题全文，`fulltext_source_question_count` 必须为 0。

`task_spec` 必须在起草前由 Skill 固化。普通模型起草只能看到 `task_spec.diagnostic_profile` 的无题干摘要、完整任务单和一个 `mother_question` 全文；安全蓝图路径不调用自由起草。独立复核只接收新题题面，不接收母题答案、蓝图证明答案、任务单预期答案或上一次复核结论。

实时生成题至少包含：

```json
{
  "question_id": "gen_knowledge_...",
  "is_generated": true,
  "student_visible": true,
  "scope": "knowledge_practice",
  "student_id": "KNOWLEDGE",
  "question_type": "solution",
  "primary_knowledge": "利用导数证明不等式",
  "stem_markdown": "...",
  "options": {},
  "answer": "...",
  "solution_markdown": "...",
  "personalization": {
    "chain_stage": "transfer_variant",
    "teaching_goal": "迁移提升：换情境或换问法但保留核心方法",
    "difficulty_key": "challenge",
    "target_level": 5,
    "difficulty_label": "5级",
    "training_focus": "迁移应用"
  },
  "generation": {
    "strategy": "single_anchor_task_spec",
    "diagnostic_evidence_question_ids": ["q001", "q002", "q003", "q004", "q005"],
    "diagnostic_evidence_count": 5,
    "reference_question_ids": ["q001", "q002", "q003", "q004", "q005"],
    "reference_count": 5,
    "mother_question_id": "q003",
    "anchor_question_id": "q003",
    "fulltext_anchor_count": 1,
    "task_spec": {"schema_version": "generation-task-spec-v2", "diagnostic_profile": {}, "generation_contract": {}},
    "task_spec_id": "task-...",
    "structure_anchor": {"kind": "mother_question", "id": "q003"},
    "changed_dimensions": ["function_structure", "reasoning_path"],
    "novelty_review": "passed"
  },
  "verification": {
    "checked_from_stem_only": true,
    "independent_answer": "...",
    "answer_matches": true,
    "status": "passed"
  },
  "teacher_review": {"status": "pending"}
}
```

约束：

- 新题 ID 不得复用任何题库 ID。
- 实时新题的 `generation.strategy` 必须为 `single_anchor_task_spec`；离线教学链候选可为 `knowledge_teaching_chain_template`。`structure_anchor` 恰好一个，`task_spec.schema_version` 必须为 `generation-task-spec-v2`。
- 普通母题路径的 `fulltext_anchor_count` 必须为 1；安全蓝图路径必须为 0。`task_spec.diagnostic_profile.evidence_summaries` 不得包含 `stem_markdown`、`answer` 或解析字段。
- 新题不得与结构锚点、任一诊断证据、同批新题、历史新题或题库题高度近似。
- 目标题型只由 `task_spec.target.question_type` 决定，不得从诊断题或结构母题题型推断。
- 改变知识点、题型、难度或训练侧重后，必须重建任务单和锚点，并把 `teacher_review.status` 重置为 `pending`。

## 教学链候选池与旧候选兼容

`generation/student_generated_question_candidates.json` 和 `generated-question-candidate-pool-v1` 是兼容载体。新版本应优先写入 `scope: knowledge_practice` 槽位，作为按知识点模式的教学链预览和任务单来源；`scope: student_practice` 只在存在真实或模拟学生计划时写入。该文件不代表教师点击按钮后刚刚完成的实时生成，也不得进入活跃批次、校验统计或试卷。

每个槽位应写入 `recommendation_logic_version` 和 `candidate_ranking`。每道候选题必须写入 `generation.pedagogical_fingerprint`、`generation.recommendation_score`、`generation.difficulty_contract`、`verification.estimated_level`、`verification.difficulty_matches`、`verification.difficulty_gate_evidence` 与 `verification.answer_solution_consistency: passed`。`recommendation_score` 至少包含 `source_quality_score`、`stage_match_score`、`difficulty_match_score`、`structure_novelty_score`、`feedback_penalty`、`duplication_risk` 和 `rationale`。这些字段用于教师端预览、任务单规划和实时生成前的结构换题；若 `difficulty_matches` 为 `false`，不得把缓存候选当作已通过新题。

`generation/knowledge_teaching_chains.json` 至少包含：

```json
{
  "schema_version": "knowledge-teaching-chains-v1",
  "logic": "knowledge_first_before_student_profile",
  "recommendation_logic_version": "knowledge-teaching-chain-scoring-v1",
  "chains": [
    {
      "teaching_chain_id": "chain-...",
      "primary_knowledge": "导数与微分·求导运算",
      "stages": [
        {"stage": "prototype_consolidation", "target_level": 2, "candidate_score": 0.92},
        {"stage": "method_variant", "target_level": 3, "candidate_score": 0.78},
        {"stage": "transfer_variant", "target_level": 4, "candidate_score": 0.61}
      ],
      "candidate_ranking": [{"question_id": "...", "score": 0.92}]
    }
  ]
}
```

读取旧记录时按以下规则映射：

- `reference_question_ids` → `diagnostic_evidence_question_ids`
- `reference_count` → `task_spec.diagnostic_profile.evidence_count`
- `mother_question_id` → `structure_anchor.id`，并设置 `structure_anchor.kind: mother_question`
- `strategy: multi_source_synthesis` → 仅标记为“旧版生成依据”

这些旧字段可以随旧记录继续保留，保证历史页面和导出可读；新请求、新草稿和新历史禁止继续写入 `multi_source_synthesis`。如果旧记录没有唯一、可解析的 `mother_question_id`，不得自动猜选锚点或再次实时生成，只能在教师端查看或由迁移流程补齐任务单与锚点。

## Cherry Studio 实时生成

实时调用仅由本机教师工作台服务发起：

- `GET /api/personalized-generation/status`：返回网关是否配置、是否连通、模型是否可用；不得返回 API Key。
- `POST /api/personalized-generation/draft`：接收 `mode`、`knowledge`、`question_type`、`difficulty`、`focus`、兼容字段 `student_id`、`student_name`、`reference_count`、`slot_index`、批次与重试字段。`mode=knowledge` 时不得要求学生；`mode=student` 时才读取真实学生作答。`reference_count` 在新流程中表示诊断证据数；诊断、任务单和锚点由服务端形成，浏览器不得上传多道原题全文。
- `POST /api/personalized-generation/verify`：接收当前新题草稿和批次字段；独立复核请求只使用草稿题面。
- `POST /api/personalized-generation/commit`：按题位提交已经通过的题；支持子集提交与幂等重试。
- `POST /api/personalized-generation/cancel`：取消根批次及其补位子批次，同时允许保留已通过或保存待同步的题。
- `POST /api/personalized-generation/review`：接收完整 `question`、`status`、`reviewer` 和 `notes`。

旧客户端仍发送 `reference_count` 时，服务端只把它解释为诊断证据数量提示，并在响应中标明兼容来源；不得据此向 Cherry Studio 注入多道原题全文。旧 `POST /api/personalized-generation/generate` 若暂时保留，只能作为 `draft + verify` 的兼容包装，返回的新记录仍必须使用 v2 策略。

工作台服务从环境变量读取 `CHERRY_STUDIO_BASE_URL`、`CHERRY_STUDIO_API_KEY`、`CHERRY_STUDIO_MODEL` 和可选 `CHERRY_STUDIO_TIMEOUT_SECONDS`。密钥不得传给浏览器或落盘。

成功响应中的 `question` 沿用上方新题字段，并额外包含：

```json
{
  "generation": {
    "generator": "cherry-studio-api-gateway-live-v1",
    "strategy": "single_anchor_task_spec",
    "diagnostic_evidence_question_ids": ["q001", "q002", "q003"],
    "diagnostic_evidence_count": 3,
    "fulltext_anchor_count": 1,
    "task_spec": {"schema_version": "generation-task-spec-v2", "diagnostic_profile": {}, "generation_contract": {}},
    "structure_anchor": {"kind": "mother_question", "id": "q003"},
    "model": "providerId:modelId",
    "generated_at": "...",
    "attempt": 1
  },
  "verification": {
    "checked_from_stem_only": true,
    "independent_answer": "...",
    "answer_matches": true,
    "status": "passed",
    "model": "providerId:modelId"
  },
  "teacher_review": {"status": "pending"}
}
```

`generation/live_personalized_generation_history.jsonl` 保存生成条件、诊断摘要、任务单、单一结构锚点、提示边界和最终通过的新题；`review/personalized_question_reviews.jsonl` 按新题 ID 保存教师决定。两者均为教师端数据。旧 `live-personalized-generation-record-v1` 继续可读；新记录通过 `generation.strategy: single_anchor_task_spec` 与 `task_spec.schema_version` 明确区分。

单个题位实时生成失败时不写入正式历史，也不得覆盖该题位上一道实时题；同批其他已经通过并提交的题继续保留。成功题先以 `delivery_status: pending` 暂存，通过独立校验后按题位提交为 `committed`，再把 `teacher_review.status` 重置为 `pending`；经教师批准后方可进入学生打印。缺少 `delivery_status` 的旧记录仅在教师状态为 `approved` 时视为正式历史。

## 学生练习与教师记录

学生练习只保留：

- 新题题干、题型、分值
- 新题 ID
- 可公开的目标知识点和训练说明

学生打印禁止包含：

- 诊断证据和结构母题的题干、答案、解析
- `diagnostic_evidence_question_ids`、`task_spec`、`structure_anchor`
- 兼容字段 `reference_question_ids`、`mother_question_id`
- 来源试卷、原题编号、预览链接
- 未审核新题

教师生成记录可以保存诊断证据 ID 与摘要、出题任务单、单一结构锚点及其原文、候选轮换、提示边界、控制条件、独立求解记录和审核记录。诊断证据中的非锚点原题全文不得进入模型提示，但可以留在教师端审计记录中。
