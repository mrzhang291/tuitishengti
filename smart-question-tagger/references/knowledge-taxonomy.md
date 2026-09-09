# Knowledge Taxonomy

Use this reference whenever assigning, auditing, or extending Gaokao-curriculum-aligned tags. The tagging order is:

1. Identify `curriculum_theme`.
2. Identify `knowledge_unit`.
3. Identify concrete `skill_tags`.
4. Write legacy-compatible `primary_knowledge` and `secondary_knowledge`.

Keep legacy fields for recommender compatibility, but do not let legacy strings drive classification directly.

`KNOWLEDGE_TAXONOMY` in `scripts/gaokao_taxonomy.py` is the machine-readable source of truth for `primary_knowledge`. Tag generation, normalization, and audit must import the same list; do not copy the values into a second audit-only allowlist.

## Curriculum Themes

- `T1预备知识`
- `T2函数`
- `T3三角函数`
- `T4向量与复数`
- `T5立体几何`
- `T6数列`
- `T7导数`
- `T8解析几何`
- `T9概率统计`

## Knowledge Units

### T1预备知识

- `U1.1 集合与常用逻辑用语`
- `U1.2 等式与不等式`
- `U1.3 一元二次函数、方程和不等式`

### T2函数

- `U2.1 函数概念与性质（单调/奇偶/周期）`
- `U2.2 幂函数、指数函数、对数函数`
- `U2.3 函数零点与方程解`
- `U2.4 函数模型与应用`

### T3三角函数

- `U3.1 三角函数概念与性质`
- `U3.2 三角恒等变换`
- `U3.3 解三角形（正弦/余弦定理）`

### T4向量与复数

- `U4.1 平面向量及其运算`
- `U4.2 平面向量基本定理与坐标表示`
- `U4.3 复数概念与运算`

### T5立体几何

- `U5.1 空间几何体与三视图`
- `U5.2 空间点线面位置关系`
- `U5.3 空间向量及其运算`
- `U5.4 空间向量与立体几何综合`

### T6数列

- `U6.1 等差数列与等比数列`
- `U6.2 数列求和（裂项/错位/分组）`
- `U6.3 数列通项与递推`

### T7导数

- `U7.1 导数概念与几何意义（切线）`
- `U7.2 导数运算（基本函数/复合/隐函数）`
- `U7.3 导数与函数单调性`
- `U7.4 导数与极值/最值`
- `U7.5 导数与不等式证明`
- `U7.6 导数与函数零点`

### T8解析几何

- `U8.1 直线与圆的方程`
- `U8.2 椭圆的定义与性质`
- `U8.3 双曲线的定义与性质`
- `U8.4 抛物线的定义与性质`
- `U8.5 圆锥曲线综合（弦长/面积/最值）`

### T9概率统计

- `U9.1 计数原理（排列/组合/二项式）`
- `U9.2 概率（古典/条件/全概率/贝叶斯）`
- `U9.3 离散型随机变量及其分布`
- `U9.4 统计（抽样/图表/数字特征）`
- `U9.5 成对数据统计分析（回归/独立性检验）`

## High-Value Skill Tags

Prefer 1-3 operation-level tags that describe the actual solution move.

- 导数定义
- 平均变化率
- 瞬时变化率
- 基本函数求导
- 复合函数求导
- 导数几何意义
- 切线斜率
- 切线方程
- 导数符号判断
- 单调区间
- 含参单调性
- 极值点判断
- 导数符号表
- 函数极值
- 利用导数求最值
- 端点与极值比较
- 函数建模最值
- 零点个数判断
- 单调性与零点
- 函数图像分析
- 构造函数
- 导数证明不等式
- 函数奇偶性
- 函数周期性
- 函数对称性
- 函数性质转化
- 条件概率
- 全概率公式
- 概率模型
- 分布列
- 数学期望
- 方差
- 空间几何体体积
- 体积建模
- 几何量关系

## Assignment Rules

- Classify by the main solution method, not the story setting.
- If a geometry-looking maximum/minimum problem is solved by derivatives, set `curriculum_theme` to `T7导数`, `knowledge_unit` to `U7.4 导数与极值/最值`, and put `T5立体几何` context in `secondary_knowledge`.
- If the solution mainly uses parity, periodicity, symmetry, or monotonicity without derivatives, use `T2函数`.
- If a function zero-count problem is solved with derivatives, use `T7导数` + `U7.6 导数与函数零点`.
- If a probability problem asks for expectation, variance, or a distribution table, use `T9概率统计` + `U9.3 离散型随机变量及其分布`.
- If it only asks for probability via conditional probability, full probability, or Bayes, use `T9概率统计` + `U9.2 概率（古典/条件/全概率/贝叶斯）`.
- If tangent line is central, use `T7导数` + `U7.1 导数概念与几何意义（切线）`.
- If a parameter must be split into cases, add `分类讨论` to `ability_tags`.
- Record the concrete phrases/formulas that support the selected label and retain the second-best candidate. A small top-1/top-2 margin is a review signal even when both candidates are valid taxonomy values.

## Legacy Field Pattern

Use readable legacy strings that keep recommender compatibility while exposing curriculum alignment:

- `primary_knowledge`: concise main method, e.g. `导数应用·极值与最值·求最值`.
- `secondary_knowledge`: preferably aligned strings such as `T5立体几何·U5.1 空间几何体与三视图·体积建模`.

Example:

```json
{
  "curriculum_theme": "T7导数",
  "knowledge_unit": "U7.4 导数与极值/最值",
  "skill_tags": ["利用导数求最值", "端点与极值比较"],
  "primary_knowledge": "导数应用·极值与最值·求最值",
  "secondary_knowledge": ["T5立体几何·U5.1 空间几何体与三视图·体积建模"]
}
```

## Review Outcome

Record whether the tag is:

- `rule_only`: only local rules have been applied.
- `codex_agent_review_v1`: the current Codex agent selectively reviewed the rule draft.
- `offline_llm_review_v1`: a separate local/offline model or manual offline packet supplied a correction.
- `teacher_reviewed`: a teacher explicitly approved or corrected the tag.
