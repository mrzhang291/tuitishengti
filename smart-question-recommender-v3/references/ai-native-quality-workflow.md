# AI-Native 推题生题质量工作流

本文件用于修改或执行 `smart-question-recommender-v3` 的推题、生题、实时校验、教师反馈闭环。目标是让系统像教师一样先按知识点组织教学链，再诊断训练目标、规划题目，而不是先找学生画像或只按规则分数挑母题。

## 核心链路

1. **题源质量门**：题库原题必须先通过题面、答案、解析一致性检查。解析错误、答案只写“见解析”、解析与题面不对应、选项答案冲突、定义域缺失导致结论不唯一的题，不得进入安全题池、教学证据、诊断证据、结构母题或安全锚点。
2. **知识点教学链**：默认先为每个知识点建立 `knowledge_practice` 链路，按 `prototype_consolidation -> method_variant -> transfer_variant` 排布题位。学生作答不是主入口，只能在“按学情生成”时作为后置加权。
3. **AI 诊断器**：从题库标签、解法信号、安全题、标签置信度、近期生成历史、教师反馈，以及可选学生/班级作答中抽取训练画像。输出不是单纯知识点，而是 `knowledge_gap`、`method_gap`、`error_pattern`、`transfer_gap`、`target_question_type`、`required_reasoning`。
4. **AI 训练规划器**：为每个知识点教学链阶段生成 1-5 个训练任务；按学情模式只调整排序、难度或薄弱点权重。每个任务必须声明 `target_level`、`question_type`、`method_family`、`function_family`、`must_change_dimensions`、`forbidden_structures`、`minimum_complexity_signals`。
5. **任务单驱动生题**：模型只能接收任务单、诊断摘要和恰好一个结构锚点；多道原题只用于诊断摘要，不得拼题。生成题必须显式写入 `generation.task_spec` 和 `generation.structure_anchor`。
6. **双审题机制**：第一审做数学正确性和答案解析一致性；第二审做教学质量，返回 `estimated_level`、`complexity_signals`、`pedagogical_fingerprint`、`duplication_risk`、`training_value`。任一失败不得进入教师审核或试卷。
7. **教师反馈记忆**：页面反馈必须落盘为 `generation/teacher_quality_feedback.jsonl`，后续生成把同类结构、函数族、错误解析和难度偏差作为负样本。

## 1-5 级难度硬规则

后端必须真实消费 `target_level: 1..5`，不得只把 4/5 合并为 `challenge`。

| 等级 | 允许结构 | 必须拒绝 |
| --- | --- | --- |
| 1级 | 直接公式、单步计算、概念识别 | 参数讨论、多层推理、复杂复合函数 |
| 2级 | 一个知识点 + 简单变形，通常 1-2 步 | 直接套公式标高难度 |
| 3级 | 2-3 步衔接，含定义域、边界、等价变换或一个非机械推理点 | 纯求导、纯代入、简单顶点最值 |
| 4级 | 参数/区间/分类/多结论逐项判断/复合函数之一或之二，至少 3 个关键判断 | 一眼公式题、多选但四项同一套路 |
| 5级 | 恒成立/存在性/范围证明/多问联动/高阶或辅助函数分析中至少三类复杂度信号，且关键推理链 ≥3 段 | 一次求导、一次代入、简单二次函数或简单分式最值 |

特别规则：

- `sqrt(x) + 1/x` 求导这类基础函数求导，最高 2级。
- `x^2 + a/x` 的常规定义域极值，多选也不能自动升到 5级；没有参数范围、存在性或多问联动时通常为 3级。
- 参数单调区间、多命题逐项判断可到 4级；只有在还包含恒成立/范围证明/多问联动或高阶构造时才可到 5级。
- `target_level=5` 不能靠“题型是多选题”或“表达式看起来复杂”通过，必须有复杂度信号和推理段数证据。

## 教学结构指纹

每道生成题必须计算并保存 `generation.pedagogical_fingerprint`，至少包含：

- `knowledge`
- `question_type`
- `target_level`
- `method_family`，如 `basic_derivative_formula`、`monotonicity_parameter_interval`、`extremum_boundary_analysis`
- `function_family`，如 `radical_plus_reciprocal`、`exponential_parameter`、`quadratic_plus_reciprocal`
- `task_intent`，如 `求导运算`、`判断单调区间`、`求极值与最值`
- `reasoning_pattern`
- `option_pattern`，选择题/多选题记录正确项数量和干扰项类型
- `complexity_signals`

去重规则：

- 同一知识点、教学链阶段和题型下，`method_family + task_intent + reasoning_pattern` 相同，应视为同类型重复，即使函数或数字不同；按学情生成时再加上学生维度做更严格去重。
- 同一批次内，`function_family` 或 `option_pattern` 重复时应优先换结构。
- 教师点过“结构重复”的指纹必须加入负样本；以后同知识点同题型不得再次生成相同指纹。

## 教学链推荐分

离线教学链候选不是最终生成题，必须用可解释分数辅助排序和换结构。每道候选写入 `generation.recommendation_score`：

- `source_quality_score`：题源安全、答案解析完整、来源证据数量与多样性。
- `stage_match_score`：是否符合 `prototype_consolidation -> method_variant -> transfer_variant` 的阶段目标。
- `difficulty_match_score`：规则估计等级与阶段目标等级是否匹配。原型巩固默认 2级，同法变式默认 3级，迁移提升默认 4级；5级只能由实时任务或教师明确选择触发。
- `structure_novelty_score`：与参考题的 `method_family + function_family + task_intent + reasoning_pattern + option_pattern` 相似度。
- `feedback_penalty`：教师反馈负样本造成的扣分。
- `duplication_risk` 与 `rationale`：给教师解释为什么推荐或为什么需要换结构。

若缓存候选 `difficulty_matches=false` 或 `duplication_risk=high`，只能作为诊断和规划证据，不得被视为已通过的新题；实时生成必须换方法族、函数族或推理路径。

## 答案解析一致性门

每个候选原题和生成题都要通过以下检查：

- 选择题答案必须与 A-D 选项、解析最终“故选 X”一致。
- 多选题至少两个正确项；解析需逐项说明，不得只有一个结论。
- 非选择题答案必须能从解析最后结论直接读出；解析不得只写“见解析”“略”“由题意得”。
- 解析中出现自我修正、试错痕迹、前后答案冲突时，必须重做或拒绝。
- 生成题的第二审必须检查 `answer_solution_consistency: passed`；题源原题若失败，标记 `source_solution_invalid` 并排除出安全池。

## 教师反馈事件

写入 `generation/teacher_quality_feedback.jsonl`，一行一个 JSON：

```json
{
  "schema_version": "teacher-quality-feedback-v1",
  "created_at": "ISO-8601",
  "question_id": "...",
  "source": "generated | source_question",
  "feedback_type": "solution_error | difficulty_too_low | difficulty_too_high | duplicate_structure | unsuitable_training_value",
  "target_level": 5,
  "teacher_suggested_level": 4,
  "pedagogical_fingerprint": {},
  "notes": ""
}
```

后续生成必须读取最近反馈：

- `solution_error`：相同原题或同一解析来源不得再作锚点。
- `difficulty_too_low`：同指纹下调可用最高等级；若目标等级仍高，强制换方法族。
- `difficulty_too_high`：同指纹不得用于低等级目标。
- `duplicate_structure`：同知识点同题型禁用该 `pedagogical_fingerprint`。

## 教师工作台要求

- 新题卡展示 `目标等级` 和系统二审 `估计等级`。两者不一致时不得显示“已通过”，必须显示失败原因。
- 知识点模式展示 `教学链阶段` 与 `教学目标`，不要把题库证据称作某个学生的学情；只有按学情模式才显示学生薄弱点。
- 每题提供轻反馈按钮：`解析错误`、`难度偏低`、`难度偏高`、`结构重复`。
- 教师反馈不替代数学审题，但会影响后续规划和去重。
- 导出的学生练习只含教师通过的新题；教师记录可含诊断摘要、任务单、结构锚点、二审结果和反馈历史。

## 验收用例

执行或修改本 skill 后，至少用下列用例回归：

- 基础求导单选题不得通过 5级目标。
- 没有任何学生计划时，仍应从题库安全题生成 `knowledge_practice` 教学链候选。
- 与已有 Q1 同为“基本函数求导 + 单选 + 直接公式”的题，即使函数不同，也应判为教学结构重复。
- 参数单调性多选题可判 4级，但不能无证据升为 5级。
- `x^2 + c/x` 常规极值多选通常为 3级；若标 5级必须包含额外参数范围、恒成立或多问联动。
- 解析与答案冲突的题源不得进入安全题池。
