#!/usr/bin/env python3
"""Gaokao-aligned taxonomy helpers for question tagging."""

from __future__ import annotations

import re

CURRICULUM_THEMES = [
    "T1预备知识",
    "T2函数",
    "T3三角函数",
    "T4向量与复数",
    "T5立体几何",
    "T6数列",
    "T7导数",
    "T8解析几何",
    "T9概率统计",
]

KNOWLEDGE_UNITS = [
    "U1.1 集合与常用逻辑用语",
    "U1.2 等式与不等式",
    "U1.3 一元二次函数、方程和不等式",
    "U2.1 函数概念与性质（单调/奇偶/周期）",
    "U2.2 幂函数、指数函数、对数函数",
    "U2.3 函数零点与方程解",
    "U2.4 函数模型与应用",
    "U3.1 三角函数概念与性质",
    "U3.2 三角恒等变换",
    "U3.3 解三角形（正弦/余弦定理）",
    "U4.1 平面向量及其运算",
    "U4.2 平面向量基本定理与坐标表示",
    "U4.3 复数概念与运算",
    "U5.1 空间几何体与三视图",
    "U5.2 空间点线面位置关系",
    "U5.3 空间向量及其运算",
    "U5.4 空间向量与立体几何综合",
    "U6.1 等差数列与等比数列",
    "U6.2 数列求和（裂项/错位/分组）",
    "U6.3 数列通项与递推",
    "U7.1 导数概念与几何意义（切线）",
    "U7.2 导数运算（基本函数/复合/隐函数）",
    "U7.3 导数与函数单调性",
    "U7.4 导数与极值/最值",
    "U7.5 导数与不等式证明",
    "U7.6 导数与函数零点",
    "U8.1 直线与圆的方程",
    "U8.2 椭圆的定义与性质",
    "U8.3 双曲线的定义与性质",
    "U8.4 抛物线的定义与性质",
    "U8.5 圆锥曲线综合（弦长/面积/最值）",
    "U9.1 计数原理（排列/组合/二项式）",
    "U9.2 概率（古典/条件/全概率/贝叶斯）",
    "U9.3 离散型随机变量及其分布",
    "U9.4 统计（抽样/图表/数字特征）",
    "U9.5 成对数据统计分析（回归/独立性检验）",
]

KNOWLEDGE_TAXONOMY = [
    "导数与微分·导数定义·平均变化率与瞬时变化率",
    "导数与微分·求导运算·基本函数求导",
    "导数与微分·切线方程·求切线",
    "导数应用·单调性·判断单调区间",
    "导数应用·极值与最值·求极值",
    "导数应用·极值与最值·求最值",
    "导数应用·零点问题·函数零点个数",
    "导数应用·不等式证明·构造函数证明",
    "函数综合·函数性质·奇偶性与周期性",
    "函数综合·函数构造·构造函数证不等式",
    "概率统计·概率模型·全概率公式",
    "概率统计·数字特征·期望与方差",
    "立体几何·空间几何体·体积建模",
]

PRIMARY_KNOWLEDGE_META = {
    "导数与微分·导数定义·平均变化率与瞬时变化率": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.1 导数概念与几何意义（切线）",
        "skill_tags": ["平均变化率", "瞬时变化率", "导数定义"],
    },
    "导数与微分·求导运算·基本函数求导": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.2 导数运算（基本函数/复合/隐函数）",
        "skill_tags": ["基本函数求导", "复合函数求导", "导数运算"],
    },
    "导数与微分·切线方程·求切线": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.1 导数概念与几何意义（切线）",
        "skill_tags": ["导数几何意义", "切线斜率", "切线方程"],
    },
    "导数应用·单调性·判断单调区间": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.3 导数与函数单调性",
        "skill_tags": ["导数符号判断", "单调区间", "含参单调性"],
    },
    "导数应用·极值与最值·求极值": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.4 导数与极值/最值",
        "skill_tags": ["极值点判断", "导数符号表", "函数极值"],
    },
    "导数应用·极值与最值·求最值": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.4 导数与极值/最值",
        "skill_tags": ["利用导数求最值", "端点与极值比较", "函数建模最值"],
    },
    "导数应用·零点问题·函数零点个数": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.6 导数与函数零点",
        "skill_tags": ["零点个数判断", "单调性与零点", "函数图像分析"],
    },
    "导数应用·不等式证明·构造函数证明": {
        "curriculum_theme": "T7导数",
        "knowledge_unit": "U7.5 导数与不等式证明",
        "skill_tags": ["构造函数", "导数证明不等式", "极值点偏移"],
    },
    "函数综合·函数性质·奇偶性与周期性": {
        "curriculum_theme": "T2函数",
        "knowledge_unit": "U2.1 函数概念与性质（单调/奇偶/周期）",
        "skill_tags": ["函数奇偶性", "函数周期性", "函数对称性"],
    },
    "函数综合·函数构造·构造函数证不等式": {
        "curriculum_theme": "T2函数",
        "knowledge_unit": "U2.1 函数概念与性质（单调/奇偶/周期）",
        "skill_tags": ["构造函数", "函数性质转化", "不等式证明"],
    },
    "概率统计·概率模型·全概率公式": {
        "curriculum_theme": "T9概率统计",
        "knowledge_unit": "U9.2 概率（古典/条件/全概率/贝叶斯）",
        "skill_tags": ["条件概率", "全概率公式", "概率模型"],
    },
    "概率统计·数字特征·期望与方差": {
        "curriculum_theme": "T9概率统计",
        "knowledge_unit": "U9.3 离散型随机变量及其分布",
        "skill_tags": ["分布列", "数学期望", "方差"],
    },
    "立体几何·空间几何体·体积建模": {
        "curriculum_theme": "T5立体几何",
        "knowledge_unit": "U5.1 空间几何体与三视图",
        "skill_tags": ["空间几何体体积", "体积建模", "几何量关系"],
    },
}

# Patterns are scored once per text section.  Specific solution-method evidence
# deliberately outweighs broad surface words such as "函数" or "证明".
PRIMARY_CLASSIFICATION_RULES = {
    "概率统计·数字特征·期望与方差": [
        (r"数学期望", 6.0, "数学期望"),
        (r"期望", 4.5, "期望"),
        (r"方差", 4.5, "方差"),
        (r"(?<![A-Za-z])E\s*\(", 4.5, "E(·)"),
        (r"(?<![A-Za-z])D\s*\(", 4.5, "D(·)"),
    ],
    "概率统计·概率模型·全概率公式": [
        (r"条件概率", 6.0, "条件概率"),
        (r"全概率", 6.0, "全概率"),
        (r"贝叶斯", 6.0, "贝叶斯"),
        (r"分布列", 4.0, "分布列"),
        (r"随机变量", 3.0, "随机变量"),
        (r"概率", 3.5, "概率"),
        (r"(?<![A-Za-z])P\s*\(", 2.5, "P(·)"),
        (r"排列|组合|二项分布|两点分布", 2.5, "计数/分布模型"),
    ],
    "导数与微分·导数定义·平均变化率与瞬时变化率": [
        (r"平均变化率", 6.0, "平均变化率"),
        (r"瞬时变化率|瞬时速度", 5.5, "瞬时变化率"),
        (r"导数定义", 6.0, "导数定义"),
        (r"\\lim|(?<![A-Za-z])lim", 4.5, "导数极限"),
        (r"\\Delta\s*x|Δx", 3.0, "增量 Δx"),
    ],
    "导数与微分·切线方程·求切线": [
        (r"切线方程", 6.0, "切线方程"),
        (r"相切|切点", 4.5, "相切/切点"),
        (r"切线", 4.0, "切线"),
        (r"倾斜角|斜率", 1.5, "斜率"),
    ],
    "导数应用·零点问题·函数零点个数": [
        (r"零点个数|有几个零点|唯一.*零点", 6.0, "零点个数"),
        (r"零点", 4.0, "函数零点"),
        (r"根的个数|实根个数", 4.0, "方程实根个数"),
    ],
    "导数应用·极值与最值·求最值": [
        (r"最值", 5.5, "最值"),
        (r"最大值|最小值", 4.5, "最大/最小值"),
        (r"距离的最小|面积的最大|体积的最大", 4.5, "建模最值"),
    ],
    "导数应用·极值与最值·求极值": [
        (r"极值点", 5.5, "极值点"),
        (r"极大值|极小值", 4.5, "极大/极小值"),
        (r"极值", 4.0, "函数极值"),
    ],
    "导数应用·不等式证明·构造函数证明": [
        (r"不等式.*(?:证明|恒成立)|(?:证明|证).*不等式", 6.0, "不等式证明"),
        (r"恒成立", 3.5, "恒成立"),
        (r"不等式", 2.5, "不等式"),
        (r"构造函数", 3.5, "构造函数"),
    ],
    "导数应用·单调性·判断单调区间": [
        (r"单调区间", 5.5, "单调区间"),
        (r"含参.*单调|单调.*参数", 5.0, "含参单调性"),
        (r"单调递增|单调递减", 3.5, "导数符号与单调"),
        (r"单调性|增函数|减函数", 3.0, "函数单调性"),
    ],
    "函数综合·函数性质·奇偶性与周期性": [
        (r"奇偶性|奇函数|偶函数", 5.5, "函数奇偶性"),
        (r"周期性|最小正周期", 5.5, "函数周期性"),
        (r"函数对称|对称中心|对称轴", 4.5, "函数对称性"),
        (r"周期", 3.5, "周期"),
        (r"\\sin|\\cos|sin|cos", 1.0, "三角函数性质"),
    ],
    "函数综合·函数构造·构造函数证不等式": [
        (r"构造新函数|令.*函数", 3.0, "构造函数"),
        (r"函数性质转化", 4.0, "函数性质转化"),
    ],
    "立体几何·空间几何体·体积建模": [
        (r"正四棱锥|棱锥|棱柱|空间几何体", 5.5, "空间几何体"),
        (r"体积|容积|方盒|容器|铁片", 3.5, "体积建模"),
    ],
    "导数与微分·求导运算·基本函数求导": [
        (r"求导|导函数", 4.5, "求导"),
        (r"f\s*['′]|y\s*['′]|\\prime", 3.5, "导数符号"),
        (r"导数", 2.5, "导数"),
    ],
}

SKILL_TAGS = sorted({tag for item in PRIMARY_KNOWLEDGE_META.values() for tag in item["skill_tags"]})


def resolve_knowledge(primary_knowledge):
    """Return gaokao-aligned metadata for a legacy primary_knowledge label."""
    primary = normalize_primary_knowledge(primary_knowledge)
    meta = PRIMARY_KNOWLEDGE_META.get(primary, PRIMARY_KNOWLEDGE_META[KNOWLEDGE_TAXONOMY[1]])
    return {
        "primary_knowledge": primary,
        "curriculum_theme": meta["curriculum_theme"],
        "knowledge_unit": meta["knowledge_unit"],
        "skill_tags": list(meta["skill_tags"]),
    }


def classify_text(text):
    """Classify raw question text into the legacy label plus curriculum metadata."""
    return {key: value for key, value in classify_text_detailed(text).items() if key != "classification"}


def classify_text_detailed(text, solution_text=""):
    """Return taxonomy labels plus scored evidence, runner-up, margin, and confidence."""
    stem = str(text or "")
    solution = str(solution_text or "")
    candidates = []
    for order, primary in enumerate(KNOWLEDGE_TAXONOMY):
        rules = PRIMARY_CLASSIFICATION_RULES.get(primary, [])
        score = 0.0
        evidence = []
        for pattern, weight, label in rules:
            flags = re.S if label in {"E(·)", "D(·)", "P(·)"} else re.I | re.S
            stem_match = re.search(pattern, stem, flags)
            solution_match = re.search(pattern, solution, flags)
            if stem_match:
                score += weight
                evidence.append({"source": "stem", "label": label, "match": stem_match.group(0)[:80], "weight": weight})
            if solution_match:
                adjusted = round(weight * 1.15, 3)
                score += adjusted
                evidence.append(
                    {"source": "solution", "label": label, "match": solution_match.group(0)[:80], "weight": adjusted}
                )
        candidates.append({"primary_knowledge": primary, "score": round(score, 3), "evidence": evidence, "order": order})

    # Curriculum assignment rules: derivative-based optimization outranks geometry context;
    # expectation/variance outranks generic probability vocabulary.
    combined = f"{stem} {solution}"
    if _is_geometry_optimization(combined):
        _boost_candidate(candidates, "导数应用·极值与最值·求最值", 4.0, "几何建模采用最值方法")
    if re.search(r"数学期望|期望|方差|(?<![A-Za-z])[ED]\s*\(", combined):
        _boost_candidate(candidates, "概率统计·数字特征·期望与方差", 5.0, "题目目标包含期望或方差")
    if _looks_derivative_method(combined):
        for primary in (
            "导数应用·极值与最值·求最值",
            "导数应用·极值与最值·求极值",
            "导数应用·零点问题·函数零点个数",
            "导数应用·不等式证明·构造函数证明",
            "导数应用·单调性·判断单调区间",
        ):
            candidate = next(item for item in candidates if item["primary_knowledge"] == primary)
            if candidate["score"] > 0:
                _boost_candidate(candidates, primary, 1.5, "解析包含导数方法")

    ranked = sorted(candidates, key=lambda item: (-item["score"], item["order"]))
    if ranked[0]["score"] <= 0:
        default = next(
            item for item in candidates if item["primary_knowledge"] == "导数与微分·求导运算·基本函数求导"
        )
        default["score"] = 1.0
        default["evidence"].append({"source": "fallback", "label": "默认求导运算", "match": "", "weight": 1.0})
        ranked = sorted(candidates, key=lambda item: (-item["score"], item["order"]))

    top, runner_up = ranked[0], ranked[1]
    top_score = float(top["score"])
    second_score = float(runner_up["score"])
    margin = max(0.0, top_score - second_score)
    margin_ratio = margin / max(top_score, 1.0)
    strength = min(top_score / 10.0, 1.0)
    evidence_strength = min(len(top["evidence"]) / 3.0, 1.0)
    primary_confidence = round(min(0.98, 0.54 + 0.22 * strength + 0.18 * margin_ratio + 0.04 * evidence_strength), 3)
    info = resolve_knowledge(top["primary_knowledge"])
    info["classification"] = {
        "primary_confidence": primary_confidence,
        "top_score": round(top_score, 3),
        "runner_up": runner_up["primary_knowledge"],
        "runner_up_score": round(second_score, 3),
        "margin": round(margin, 3),
        "margin_ratio": round(margin_ratio, 3),
        "evidence": top["evidence"],
        "candidates": [
            {"primary_knowledge": item["primary_knowledge"], "score": item["score"]}
            for item in ranked[:3]
        ],
    }
    return info


def _boost_candidate(candidates, primary, value, reason):
    candidate = next(item for item in candidates if item["primary_knowledge"] == primary)
    candidate["score"] = round(float(candidate["score"]) + value, 3)
    candidate["evidence"].append({"source": "rule", "label": reason, "match": "", "weight": value})


def _looks_derivative_method(text):
    return re.search(r"求导|导数|导函数|f\s*['′]|y\s*['′]|\\prime", str(text), re.I) is not None


def aligned_knowledge_label(primary_knowledge):
    value = str(primary_knowledge or "")
    if value.startswith("T") and "·U" in value:
        return value
    info = resolve_knowledge(primary_knowledge)
    skill = info["skill_tags"][0] if info["skill_tags"] else info["primary_knowledge"].split("·")[-1]
    return f"{info['curriculum_theme']}·{info['knowledge_unit']}·{skill}"


def normalize_secondary_knowledge(items):
    if not isinstance(items, list):
        return []
    return [aligned_knowledge_label(item) for item in items if item]


def normalize_primary_knowledge(primary_knowledge):
    target = str(primary_knowledge or "")
    if target in PRIMARY_KNOWLEDGE_META:
        return target
    for item in KNOWLEDGE_TAXONOMY:
        if target and (target in item or item in target):
            return item
    return infer_primary_knowledge(target)


def infer_primary_knowledge(text):
    return classify_text_detailed(text)["primary_knowledge"]


def infer_secondary_knowledge(text, primary_knowledge):
    primary = normalize_primary_knowledge(primary_knowledge)
    candidates = []
    if primary != "立体几何·空间几何体·体积建模" and _has_geometry_modeling_context(text):
        candidates.append("立体几何·空间几何体·体积建模")
    if primary != "导数与微分·求导运算·基本函数求导" and any(token in text for token in ("f'", "求导", "导数")):
        candidates.append("导数与微分·求导运算·基本函数求导")
    if primary != "导数应用·单调性·判断单调区间" and "单调" in str(text):
        candidates.append("导数应用·单调性·判断单调区间")
    if primary not in ("导数应用·极值与最值·求极值", "导数应用·极值与最值·求最值") and any(
        token in str(text) for token in ("极值", "最大值", "最小值", "最值")
    ):
        candidates.append("导数应用·极值与最值·求最值")
    if primary != "函数综合·函数构造·构造函数证不等式" and any(token in str(text) for token in ("构造", "证明")):
        candidates.append("函数综合·函数构造·构造函数证不等式")
    if primary != "概率统计·数字特征·期望与方差" and any(token in str(text) for token in ("期望", "方差", "E(X)", "D(X)")):
        candidates.append("概率统计·数字特征·期望与方差")
    return [aligned_knowledge_label(item) for item in candidates[:3]]


def _has_geometry_modeling_context(text):
    return any(token in str(text) for token in ("正四棱锥", "棱锥", "空间几何体", "体积", "容积", "铁片", "容器", "方盒"))


def _is_geometry_optimization(text):
    text = str(text)
    if not _has_geometry_modeling_context(text):
        return False
    return any(token in text for token in ("最大值", "最小值", "最大", "最小", "最值", "V(x)", "V'", "单调递增", "单调递减"))
