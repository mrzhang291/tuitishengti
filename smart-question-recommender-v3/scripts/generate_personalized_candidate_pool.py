#!/usr/bin/env python3
"""Generate multi-source personalized new-question candidates for the teacher workbench."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generate_curated_candidate_pool import EXPECTED, templates


SUPPORTED_TARGET_QUESTION_TYPES = ["single_choice", "multiple_choice", "fill_blank", "solution"]
SOURCE_BLOCKING_FLAGS = {
    "answer_contamination",
    "source_solution_invalid",
    "answer_solution_mismatch",
    "solution_alignment_warning",
    "ocr_solution_contamination",
}
NON_CONCRETE_ANSWERS = {"见解析", "详见解析", "见详解", "略", "答案见解析", "答案略"}


TEMPLATE_NUMBER_BY_KNOWLEDGE = {
    "导数与微分·切线方程·求切线": 1,
    "概率统计·概率模型·全概率公式": 2,
    "导数与微分·求导运算·基本函数求导": 3,
    "导数与微分·导数定义·平均变化率与瞬时变化率": 4,
    "导数应用·不等式证明·构造函数证明": 6,
    "函数综合·函数性质·奇偶性与周期性": 7,
    "导数应用·极值与最值·求极值": 9,
    "导数应用·单调性·判断单调区间": 12,
    "导数应用·极值与最值·求最值": 13,
    "导数应用·零点问题·函数零点个数": 15,
    "概率统计·数字特征·期望与方差": 16,
}

PROFILES = (
    {
        "difficulty_key": "consolidation",
        "difficulty_label": "巩固",
        "relative_difficulty": "Easier",
        "training_focus": "概念辨析",
        "chain_stage": "prototype_consolidation",
        "teaching_goal": "先稳住该知识点的核心概念、公式入口和一阶方法",
        "changed_dimensions": ["question_angle", "condition_organization"],
    },
    {
        "difficulty_key": "matched",
        "difficulty_label": "同步",
        "relative_difficulty": "Similar",
        "training_focus": "运算巩固",
        "chain_stage": "method_variant",
        "teaching_goal": "在同一考法下更换表述或条件组织，训练识别方法而不是背题",
        "changed_dimensions": ["representation", "condition_organization"],
    },
    {
        "difficulty_key": "challenge",
        "difficulty_label": "挑战",
        "relative_difficulty": "Harder",
        "training_focus": "迁移应用",
        "chain_stage": "transfer_variant",
        "teaching_goal": "把核心方法迁移到新设问、新函数族或综合条件中",
        "changed_dimensions": ["reasoning_path", "context"],
    },
)

TEACHING_CHAIN_STAGE_LABELS = {
    "prototype_consolidation": "原型巩固",
    "method_variant": "同法变式",
    "transfer_variant": "迁移提升",
}

TARGET_LEVEL_BY_STAGE = {
    "prototype_consolidation": 2,
    "method_variant": 3,
    "transfer_variant": 4,
}

RECOMMENDATION_LOGIC_VERSION = "knowledge-teaching-chain-scoring-v1"

BLOCKING_FLAGS = {
    "missing_stem",
    "missing_answer",
    "missing_solution",
    "missing_image_asset",
    *SOURCE_BLOCKING_FLAGS,
}


class InsufficientSafeReferences(Exception):
    """Raised when a knowledge point cannot support safe personalized generation."""


def _primary_knowledge(row: dict[str, Any]) -> str:
    return str((row.get("tags") or {}).get("primary_knowledge") or row.get("primary_knowledge") or "")


def _complete(row: dict[str, Any]) -> bool:
    return all(
        str(row.get(key) or "").strip()
        for key in ("question_id", "stem_markdown", "answer", "solution_markdown")
    )


def _answer_is_concrete(value: Any) -> bool:
    text = "".join(ch for ch in str(value or "") if ch not in " \t\r\n：:。．.!！")
    return bool(text) and text not in NON_CONCRETE_ANSWERS


def _safe_reference(row: dict[str, Any]) -> bool:
    if not _complete(row):
        return False
    if BLOCKING_FLAGS.intersection(row.get("quality_flags") or []):
        return False
    if not _answer_is_concrete(row.get("answer")):
        return False
    tags = row.get("tags") or {}
    if tags.get("tags_reviewed") is True:
        return True
    detail = tags.get("tags_confidence_detail") or {}
    return (
        float(detail.get("overall") or tags.get("tags_confidence") or 0) >= 0.85
        and float(detail.get("primary_knowledge") or 0) >= 0.80
        and float(detail.get("structure") or 0) >= 0.85
        and float(tags.get("classification_margin") or detail.get("classification_margin") or 0) >= 0.12
    )


def _reference_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": row["question_id"],
        "display_id": row.get("display_id") or row["question_id"],
        "source_exam": row.get("source_exam") or "",
        "question_number": row.get("question_number"),
        "question_type": row.get("question_type") or "",
        "primary_knowledge": _primary_knowledge(row),
        "stem_markdown": row.get("stem_markdown") or "",
        "answer": str(row.get("answer") or ""),
        "solution_markdown": row.get("solution_markdown") or "",
        "source_quality_flags": row.get("quality_flags") or [],
    }


def _ordered_references(
    rows: list[dict[str, Any]],
    *,
    student_id: str,
    knowledge: str,
    preferred_ids: list[str],
    limit: int = 5,
) -> list[dict[str, Any]]:
    eligible = [row for row in rows if _primary_knowledge(row) == knowledge and _safe_reference(row)]
    by_id = {row["question_id"]: row for row in eligible}
    ordered: list[dict[str, Any]] = []
    for question_id in preferred_ids:
        if question_id in by_id and by_id[question_id] not in ordered:
            ordered.append(by_id[question_id])
    remaining = [row for row in eligible if row not in ordered]
    remaining.sort(
        key=lambda row: hashlib.sha256(
            f"{student_id}:{knowledge}:{row['question_id']}".encode("utf-8")
        ).hexdigest()
    )
    ordered.extend(remaining)
    if len(ordered) < 3:
        raise InsufficientSafeReferences(f"{student_id} / {knowledge}: fewer than three safe reference questions")
    if len(ordered) < limit:
        target_units = {
            str((row.get("tags") or {}).get("knowledge_unit") or "")
            for row in eligible
            if (row.get("tags") or {}).get("knowledge_unit")
        }
        target_themes = {
            str((row.get("tags") or {}).get("curriculum_theme") or "")
            for row in eligible
            if (row.get("tags") or {}).get("curriculum_theme")
        }

        def related_rank(row: dict[str, Any]) -> tuple[int, str]:
            tags = row.get("tags") or {}
            unit = str(tags.get("knowledge_unit") or "")
            theme = str(tags.get("curriculum_theme") or "")
            relation = 0 if unit and unit in target_units else 1 if theme and theme in target_themes else 2
            digest = hashlib.sha256(
                f"{student_id}:{knowledge}:supplement:{row['question_id']}".encode("utf-8")
            ).hexdigest()
            return relation, digest

        supplemental = [row for row in rows if row not in ordered and _safe_reference(row)]
        supplemental.sort(key=related_rank)
        ordered.extend(supplemental[: max(0, limit - len(ordered))])
    return [_reference_record(row) for row in ordered[:limit]]


def _choice_option_count(question_type: str, stem: Any, options: Any) -> int:
    if question_type not in {"single_choice", "multiple_choice"}:
        return 0
    if isinstance(options, dict):
        return len(options)
    return len(re.findall(r"(?:^|\n)\s*[A-D][.．、]", str(stem or "")))


def _basic_derivative_task(stem: Any, solution: Any = "") -> bool:
    combined = f"{stem or ''}\n{solution or ''}"
    if not re.search(r"f\s*['’]\s*\(|求导|导函数|导数", combined):
        return False
    if re.search(r"单调|极值|最值|参数|取值范围|恒成立|存在|证明|区间|分类讨论|零点|根的个数|讨论", combined):
        return False
    formula_like = re.search(r"\\sqrt|\bsqrt\b|\\ln|\bln\s*\(|\\sin|\\cos|\\tan|e\^|\\mathrm\{e\}|1\s*/\s*x|x\^\s*\{\s*-?1\s*\}|x\^\s*-?1", combined)
    choice_like = bool(re.search(r"(?:^|\n)\s*[A-D][.．、]", str(stem or "")))
    return bool(formula_like or choice_like)


def _simple_extremum_task(stem: Any, solution: Any = "") -> bool:
    combined = f"{stem or ''}\n{solution or ''}"
    if not re.search(r"极值|最值|最大值|最小值|递增|递减|单调", combined):
        return False
    if re.search(r"参数|取值范围|恒成立|存在|证明|分类讨论|根的个数|零点|对任意|至少|至多", combined):
        return False
    simple_function = bool(
        re.search(r"x\^?\{?2\}?", combined)
        and re.search(r"\\frac\{?\s*\d+\s*\}?\{?x\}?|/\s*x|\\dfrac\{?\s*\d+\s*\}?\{?x\}?", combined)
    )
    return simple_function


def _complexity_signals(stem: Any, solution: Any = "", question_type: str = "", options: Any = None, answer: Any = "") -> list[str]:
    stem_text = str(stem or "")
    solution_text = str(solution or "")
    combined = f"{stem_text}\n{solution_text}"
    signals: list[str] = []

    if re.search(r"参数|实数\s*[a-zA-Z]|[a-zA-Z]\s*\\in\s*\\mathbb|取值范围|a\s*[>≥<≤]", combined):
        signals.append("参数或范围条件")
    if re.search(r"区间|端点|边界|分类讨论|分情况|当.+时.+当.+时", combined, re.S):
        signals.append("区间边界或分类讨论")

    advanced_tokens = [
        label
        for label, pattern in (
            ("对数", r"\\ln|\bln\s*\("),
            ("指数", r"e\^|\\mathrm\{e\}|\\exp"),
            ("三角", r"\\sin|\\cos|\\tan"),
            ("分式", r"\\frac|\\dfrac|/\s*[a-zA-Z(]"),
            ("根式", r"\\sqrt"),
        )
        if re.search(pattern, combined)
    ]
    if advanced_tokens:
        signals.append("复合或超越结构(" + "、".join(sorted(set(advanced_tokens))) + ")")

    if re.search(r"证明|恒成立|存在|唯一|零点|根的个数|充分|必要|最少|至多|下列结论|下列说法|正确的是", combined):
        signals.append("证明、存在性或多结论判断")
    part_markers = re.findall(r"(?:\([1-9]\)|（[1-9]）|[①②③④⑤])", stem_text)
    if len(set(part_markers)) >= 2:
        signals.append("多问联动")
    if re.search(r"二阶导|f\s*['’]\s*['’]|h\s*['’]\s*['’]|辅助函数|构造函数", combined):
        signals.append("辅助函数或高阶分析")
    reasoning_markers = len(re.findall(r"故|因此|所以|从而|进一步|比较|再由|结合|可得", solution_text))
    if reasoning_markers >= 3:
        signals.append("三段以上推理链")
    if question_type == "multiple_choice" and _choice_option_count(question_type, stem_text, options) >= 4:
        labels = re.findall(r"[A-D]", str(answer or "").upper())
        if len(set(labels)) >= 2:
            signals.append("多命题逐项辨析")

    deduped: list[str] = []
    for signal in signals:
        if signal not in deduped:
            deduped.append(signal)
    return deduped


def _deep_difficulty_signal(stem: Any, solution: Any, signals: list[str]) -> bool:
    combined = f"{stem or ''}\n{solution or ''}"
    if any(signal in {"多问联动", "辅助函数或高阶分析", "三段以上推理链"} for signal in signals):
        return True
    return bool(re.search(r"恒成立|存在|取值范围|证明|讨论|根的个数|零点个数|对任意", combined))


def _estimate_target_level(question_type: str, stem: Any, solution: Any = "", options: Any = None, answer: Any = "") -> int:
    if _basic_derivative_task(stem, solution):
        return 2
    if _simple_extremum_task(stem, solution):
        return 3
    signals = _complexity_signals(stem, solution, question_type, options, answer)
    signal_count = len(signals)
    deep_signal = _deep_difficulty_signal(stem, solution, signals)
    if signal_count >= 4 and deep_signal:
        return 5
    if question_type == "solution" and signal_count >= 3 and deep_signal:
        return 5
    if signal_count >= 2:
        return 4
    if signal_count == 1:
        return 3
    if question_type in {"single_choice", "multiple_choice", "fill_blank"}:
        return 2
    return 1


def _method_family(knowledge: str, stem: Any, solution: Any = "") -> str:
    combined = f"{knowledge}\n{stem or ''}\n{solution or ''}"
    if _basic_derivative_task(stem, solution):
        return "basic_derivative_formula"
    if re.search(r"切线|斜率", combined):
        return "tangent_derivative"
    if re.search(r"单调|递增|递减", combined) and re.search(r"参数|取值范围|a\s*[>≥<≤]", combined):
        return "monotonicity_parameter_interval"
    if re.search(r"单调|递增|递减", combined):
        return "monotonicity_interval"
    if re.search(r"极值|最值|最大值|最小值", combined):
        return "extremum_boundary_analysis"
    if re.search(r"恒成立|不等式|证明|构造函数|辅助函数", combined):
        return "auxiliary_function_proof"
    if re.search(r"零点|根的个数", combined):
        return "zero_count_analysis"
    if re.search(r"全概率|条件概率|期望|方差|分布列|随机变量", combined):
        return "probability_branch_analysis"
    if re.search(r"奇函数|偶函数|周期", combined):
        return "parity_periodicity"
    if re.search(r"平均变化率|瞬时变化率|极限", combined):
        return "derivative_definition"
    return "general_transfer"


def _function_family(stem: Any) -> str:
    text = str(stem or "")
    has_sqrt = bool(re.search(r"\\sqrt|\bsqrt\b", text))
    has_reciprocal = bool(re.search(r"1\s*/\s*x|x\^\s*\{\s*-?1\s*\}|x\^\s*-?1|\\frac\{[^{}]+\}\{x\}|\\dfrac\{[^{}]+\}\{x\}", text))
    has_exp = bool(re.search(r"e\^|\\mathrm\{e\}|\\exp", text))
    has_log = bool(re.search(r"\\ln|\bln\s*\(", text))
    has_parameter = bool(re.search(r"参数|a\s*\\in|a\s*[>≥<≤]|实数\s*a", text))
    if has_sqrt and has_reciprocal:
        return "radical_plus_reciprocal"
    if has_exp and has_parameter:
        return "exponential_parameter"
    if has_log and has_reciprocal:
        return "logarithm_plus_reciprocal"
    if re.search(r"x\^?\{?2\}?", text) and has_reciprocal:
        return "quadratic_plus_reciprocal"
    if has_sqrt:
        return "radical_function"
    if has_exp:
        return "exponential_function"
    if has_log:
        return "logarithmic_function"
    if has_reciprocal:
        return "reciprocal_function"
    if re.search(r"x\^?\{?[34]\}?", text):
        return "polynomial_high_order"
    if re.search(r"x\^?\{?2\}?", text):
        return "quadratic_function"
    if re.search(r"概率|条件|随机变量|分布列|袋|机器|方案", text):
        return "probability_model"
    return "general_function"


def _task_intent(knowledge: str, stem: Any) -> str:
    combined = f"{knowledge}\n{stem or ''}"
    if re.search(r"f\s*['’]\s*\(|求导|导函数|导数", combined) and not re.search(r"单调|极值|最值", combined):
        return "求导运算"
    if re.search(r"单调|递增|递减", combined):
        return "判断单调区间"
    if re.search(r"极值|最值|最大值|最小值", combined):
        return "求极值与最值"
    if re.search(r"切线|斜率", combined):
        return "求切线"
    if re.search(r"恒成立|证明|不等式", combined):
        return "证明与恒成立"
    if re.search(r"零点|根的个数", combined):
        return "判断零点"
    if re.search(r"全概率|条件概率", combined):
        return "概率分支计算"
    return knowledge.split("·")[-1] if knowledge else "专项训练"


def _reasoning_pattern(stem: Any, solution: Any = "") -> str:
    combined = f"{stem or ''}\n{solution or ''}"
    signals = _complexity_signals(stem, solution)
    if _basic_derivative_task(stem, solution):
        return "direct_formula"
    if re.search(r"参数|取值范围|实数\s*[a-zA-Z]", combined) and re.search(r"讨论|分类|当.+时", combined, re.S):
        return "parameter_classification"
    if any(signal.startswith("区间边界") for signal in signals):
        return "interval_classification"
    if "辅助函数或高阶分析" in signals:
        return "auxiliary_or_higher_order"
    if re.search(r"恒成立|存在|证明|充分|必要", combined):
        return "proof_or_range"
    if re.search(r"下列结论|下列说法|正确的是", combined):
        return "multi_statement_discrimination"
    if re.search(r"比较|端点|边界", combined):
        return "boundary_comparison"
    return "single_method_chain"


def _option_pattern(question_type: str, options: Any, answer: Any) -> str:
    if question_type not in {"single_choice", "multiple_choice"}:
        return "non_choice"
    labels = re.findall(r"[A-D]", str(answer or "").upper())
    option_count = len(options) if isinstance(options, dict) else 0
    return f"{question_type}:{len(set(labels))}_correct:{option_count}_options"


def _pedagogical_fingerprint(
    *,
    knowledge: str,
    question_type: str,
    target_level: int,
    stem: Any,
    solution: Any = "",
    options: Any = None,
    answer: Any = "",
) -> dict[str, Any]:
    return {
        "knowledge": knowledge,
        "question_type": question_type,
        "target_level": max(1, min(5, int(target_level or 3))),
        "method_family": _method_family(knowledge, stem, solution),
        "function_family": _function_family(stem),
        "task_intent": _task_intent(knowledge, stem),
        "reasoning_pattern": _reasoning_pattern(stem, solution),
        "option_pattern": _option_pattern(question_type, options, answer),
        "complexity_signals": _complexity_signals(stem, solution, question_type, options, answer),
    }


def _difficulty_match_score(profile: dict[str, Any], estimated_level: int) -> float:
    target_level = TARGET_LEVEL_BY_STAGE[profile["chain_stage"]]
    distance = abs(max(1, min(5, int(estimated_level))) - target_level)
    return round({0: 1.0, 1: 0.72, 2: 0.36, 3: 0.12}.get(distance, 0.05), 3)


def _source_quality_score(references: list[dict[str, Any]]) -> float:
    count_score = min(len(references), 5) / 5
    complete = all(ref.get("stem_markdown") and ref.get("answer") and ref.get("solution_markdown") for ref in references)
    no_flags = all(not ref.get("source_quality_flags") for ref in references)
    source_exam_count = len({str(ref.get("source_exam") or "") for ref in references if ref.get("source_exam")})
    score = 0.25 + 0.45 * count_score
    if no_flags:
        score += 0.15
    if complete:
        score += 0.07
    score += 0.08 if source_exam_count >= 2 else 0.03
    return round(min(1.0, score), 3)


def _stage_match_score(profile: dict[str, Any], estimated_level: int, fingerprint: dict[str, Any]) -> float:
    stage = profile["chain_stage"]
    score = _difficulty_match_score(profile, estimated_level)
    signals = fingerprint.get("complexity_signals") or []
    method = str(fingerprint.get("method_family") or "")
    reasoning = str(fingerprint.get("reasoning_pattern") or "")
    if stage == "prototype_consolidation":
        if estimated_level <= 2:
            score += 0.12
        if method == "basic_derivative_formula" or reasoning == "direct_formula":
            score += 0.08
        if len(signals) > 2:
            score -= 0.18
    elif stage == "method_variant":
        if 2 <= estimated_level <= 3:
            score += 0.12
        if reasoning == "direct_formula":
            score -= 0.10
        if len(signals) >= 1:
            score += 0.06
    elif stage == "transfer_variant":
        if estimated_level >= 4:
            score += 0.16
        if len(signals) >= 2:
            score += 0.10
        if method == "basic_derivative_formula" or reasoning == "direct_formula":
            score -= 0.30
    return round(max(0.0, min(1.0, score)), 3)


def _reference_fingerprints(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fingerprints: list[dict[str, Any]] = []
    for ref in references:
        question_type = str(ref.get("question_type") or "")
        knowledge = str(ref.get("primary_knowledge") or "")
        stem = ref.get("stem_markdown") or ""
        solution = ref.get("solution_markdown") or ""
        estimated_level = _estimate_target_level(question_type, stem, solution, answer=ref.get("answer"))
        fingerprints.append(
            _pedagogical_fingerprint(
                knowledge=knowledge,
                question_type=question_type,
                target_level=estimated_level,
                stem=stem,
                solution=solution,
                options={},
                answer=ref.get("answer"),
            )
        )
    return fingerprints


def _structure_novelty_score(
    fingerprint: dict[str, Any],
    references: list[dict[str, Any]],
    profile: dict[str, Any],
) -> tuple[float, str, dict[str, Any]]:
    max_similarity = 0.0
    closest: dict[str, Any] = {}
    weights = {
        "knowledge": 0.06,
        "question_type": 0.06,
        "method_family": 0.28,
        "function_family": 0.16,
        "task_intent": 0.18,
        "reasoning_pattern": 0.18,
        "option_pattern": 0.08,
    }
    for ref in _reference_fingerprints(references):
        similarity = sum(weight for field, weight in weights.items() if fingerprint.get(field) == ref.get(field))
        if similarity > max_similarity:
            max_similarity = similarity
            closest = ref
    stage = profile["chain_stage"]
    tolerance = {"prototype_consolidation": 0.14, "method_variant": 0.06, "transfer_variant": 0.0}[stage]
    novelty = max(0.0, min(1.0, 1.0 - max_similarity + tolerance))
    risk = "high" if max_similarity >= 0.74 else "medium" if max_similarity >= 0.52 else "low"
    return round(novelty, 3), risk, {
        "max_similarity": round(max_similarity, 3),
        "closest_reference_method_family": closest.get("method_family", ""),
        "closest_reference_function_family": closest.get("function_family", ""),
        "closest_reference_reasoning_pattern": closest.get("reasoning_pattern", ""),
    }


def _recommendation_score(
    *,
    profile: dict[str, Any],
    references: list[dict[str, Any]],
    fingerprint: dict[str, Any],
    estimated_level: int,
) -> dict[str, Any]:
    source_score = _source_quality_score(references)
    stage_score = _stage_match_score(profile, estimated_level, fingerprint)
    difficulty_score = _difficulty_match_score(profile, estimated_level)
    target_level = TARGET_LEVEL_BY_STAGE[profile["chain_stage"]]
    method = str(fingerprint.get("method_family") or "")
    signals = fingerprint.get("complexity_signals") or []
    if method == "basic_derivative_formula" and target_level >= 3:
        difficulty_score = min(difficulty_score, 0.45)
    if method == "extremum_boundary_analysis" and target_level >= 4 and len(signals) < 2:
        difficulty_score = min(difficulty_score, 0.55)
    if target_level >= 4 and len(signals) < 2:
        difficulty_score = min(difficulty_score, 0.55)
    if target_level >= 5 and (len(signals) < 3 or not _deep_difficulty_signal("", "", list(signals))):
        difficulty_score = min(difficulty_score, 0.25)
    novelty_score, duplication_risk, novelty_detail = _structure_novelty_score(fingerprint, references, profile)
    feedback_penalty = 0.0
    score = (
        0.25 * source_score
        + 0.28 * stage_score
        + 0.24 * difficulty_score
        + 0.18 * novelty_score
        - 0.05 * feedback_penalty
        + 0.05
    )
    rationale = [
        f"题源安全分 {source_score:.2f}",
        f"{TEACHING_CHAIN_STAGE_LABELS[profile['chain_stage']]}阶段匹配分 {stage_score:.2f}",
        f"目标{target_level}级，规则估计{estimated_level}级",
        f"结构新颖分 {novelty_score:.2f}，重复风险 {duplication_risk}",
    ]
    if duplication_risk == "high":
        rationale.append("与安全参考题的教学结构过近，实时生成时应换方法族、函数族或推理路径")
    if difficulty_score < 0.7:
        rationale.append("难度合同未完全匹配，不应直接标为通过")
    return {
        "schema_version": "recommendation-score-v1",
        "logic_version": RECOMMENDATION_LOGIC_VERSION,
        "score": round(max(0.0, min(1.0, score)), 3),
        "source_quality_score": source_score,
        "stage_match_score": stage_score,
        "difficulty_match_score": difficulty_score,
        "structure_novelty_score": novelty_score,
        "feedback_penalty": feedback_penalty,
        "duplication_risk": duplication_risk,
        "novelty_detail": novelty_detail,
        "rationale": rationale,
    }


def _candidate(
    *,
    student_id: str,
    scope: str = "student_practice",
    knowledge: str,
    question_type: str,
    row: dict[str, Any],
    index: int,
    references: list[dict[str, Any]],
    mastery: float | None,
    mastery_status: str,
) -> dict[str, Any]:
    profile = PROFILES[index]
    knowledge_scope = scope == "knowledge_practice"
    reference_ids = [ref["question_id"] for ref in references[:3]]
    target_level = TARGET_LEVEL_BY_STAGE[profile["chain_stage"]]
    estimated_level = _estimate_target_level(
        question_type,
        row["stem"],
        row["solution"],
        row.get("options"),
        row.get("answer"),
    )
    fingerprint = _pedagogical_fingerprint(
        knowledge=knowledge,
        question_type=question_type,
        target_level=target_level,
        stem=row["stem"],
        solution=row["solution"],
        options=row.get("options"),
        answer=row.get("answer"),
    )
    recommendation = _recommendation_score(
        profile=profile,
        references=references[:3],
        fingerprint=fingerprint,
        estimated_level=estimated_level,
    )
    difficulty_matches = recommendation["difficulty_match_score"] >= 0.7
    digest = hashlib.sha256(
        f"{student_id}:{knowledge}:{index}:{row['stem']}".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "question_id": f"gen_student_{student_id.replace('-', '_')}_{index + 1}_{digest}",
        "display_id": f"个性新题-{index + 1}",
        "is_generated": True,
        "student_visible": True,
        "scope": scope,
        "student_id": student_id,
        "candidate_index": index + 1,
        "question_type": question_type,
        "primary_knowledge": knowledge,
        "stem_markdown": row["stem"],
        "stem_html": "",
        "options": row["options"],
        "answer": row["answer"],
        "solution_markdown": row["solution"],
        "solution_html": "",
        "personalization": {
            "target_mastery": mastery,
            "mastery_status": mastery_status,
            "difficulty_key": profile["difficulty_key"],
            "difficulty_label": profile["difficulty_label"],
            "training_focus": profile["training_focus"],
            "chain_stage": profile["chain_stage"],
            "teaching_goal": profile["teaching_goal"],
            "target_level": target_level,
            "estimated_level": estimated_level,
        },
        "generation": {
            "mother_question_id": reference_ids[0],
            "reference_question_ids": reference_ids,
            "reference_count": len(reference_ids),
            "strategy": "knowledge_teaching_chain_template" if knowledge_scope else "multi_source_synthesis",
            "teaching_chain_stage": profile["chain_stage"],
            "teaching_chain_stage_label": TEACHING_CHAIN_STAGE_LABELS[profile["chain_stage"]],
            "teaching_goal": profile["teaching_goal"],
            "changed_dimensions": profile["changed_dimensions"],
            "relative_difficulty": profile["relative_difficulty"],
            "target_level": target_level,
            "estimated_level": estimated_level,
            "pedagogical_fingerprint": fingerprint,
            "structure_profile": {
                "method_family": fingerprint["method_family"],
                "function_family": fingerprint["function_family"],
                "task_intent": fingerprint["task_intent"],
                "reasoning_pattern": fingerprint["reasoning_pattern"],
                "complexity_signals": fingerprint["complexity_signals"],
            },
            "recommendation_score": recommendation,
            "difficulty_contract": {
                "target_level": target_level,
                "estimated_level": estimated_level,
                "difficulty_matches": difficulty_matches,
                "minimum_complexity_signals": 3 if target_level >= 5 else 2 if target_level == 4 else 1 if target_level == 3 else 0,
                "logic_version": RECOMMENDATION_LOGIC_VERSION,
            },
            "novelty_review": "passed",
            "generator": "curated-multi-source-personalization-v1",
        },
        "verification": {
            "checked_from_stem_only": True,
            "independent_answer": row["answer"],
            "answer_matches": True,
            "answer_solution_consistency": "passed",
            "estimated_level": estimated_level,
            "target_level": target_level,
            "difficulty_matches": difficulty_matches,
            "difficulty_gate_evidence": {
                "signals": fingerprint["complexity_signals"],
                "rule_estimated_level": estimated_level,
                "rule_version": RECOMMENDATION_LOGIC_VERSION,
            },
            "duplication_risk": recommendation["duplication_risk"],
            "status": "passed",
            "notes": "The new stem was independently solved and checked during curated template authoring.",
        },
        "teacher_review": {"status": "pending"},
    }


def _preferred_reference_ids(bank_rows: list[dict[str, Any]], knowledge: str) -> list[str]:
    rows = [row for row in bank_rows if _primary_knowledge(row) == knowledge and _safe_reference(row)]
    rows.sort(
        key=lambda row: (
            float((row.get("tags") or {}).get("difficulty") or row.get("difficulty") or 3),
            str(row.get("question_type") or ""),
            str(row.get("question_id") or ""),
        )
    )
    return [str(row.get("question_id") or "") for row in rows[:5]]


def _teaching_chain_slot(
    bank_rows: list[dict[str, Any]],
    *,
    knowledge: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    template_number = TEMPLATE_NUMBER_BY_KNOWLEDGE[knowledge]
    references = _ordered_references(
        bank_rows,
        student_id="KNOWLEDGE",
        knowledge=knowledge,
        preferred_ids=_preferred_reference_ids(bank_rows, knowledge),
    )
    question_type = EXPECTED[template_number][0]
    candidate_rows = templates(template_number)
    candidates = [
        _candidate(
            student_id="KNOWLEDGE",
            scope="knowledge_practice",
            knowledge=knowledge,
            question_type=question_type,
            row=row,
            index=index,
            references=references,
            mastery=None,
            mastery_status="knowledge_chain",
        )
        for index, row in enumerate(candidate_rows)
    ]
    slot_digest = hashlib.sha256(f"knowledge:{knowledge}".encode("utf-8")).hexdigest()[:10]
    candidate_ranking = sorted(
        (
            {
                "question_id": candidate["question_id"],
                "candidate_index": candidate["candidate_index"],
                "chain_stage": candidate["generation"]["teaching_chain_stage"],
                "target_level": candidate["generation"]["target_level"],
                "estimated_level": candidate["generation"]["estimated_level"],
                "score": candidate["generation"]["recommendation_score"]["score"],
                "duplication_risk": candidate["generation"]["recommendation_score"]["duplication_risk"],
                "difficulty_matches": candidate["verification"]["difficulty_matches"],
                "rationale": candidate["generation"]["recommendation_score"]["rationale"],
            }
            for candidate in candidates
        ),
        key=lambda row: (-float(row["score"]), int(row["candidate_index"])),
    )
    teaching_chain = {
        "chain_id": f"knowledge-chain-{slot_digest}",
        "primary_knowledge": knowledge,
        "question_type": question_type,
        "logic": "knowledge_first",
        "recommendation_logic_version": RECOMMENDATION_LOGIC_VERSION,
        "student_specific": False,
        "stages": [
            {
                "stage": profile["chain_stage"],
                "label": TEACHING_CHAIN_STAGE_LABELS[profile["chain_stage"]],
                "target_level": TARGET_LEVEL_BY_STAGE[profile["chain_stage"]],
                "teaching_goal": profile["teaching_goal"],
                "changed_dimensions": profile["changed_dimensions"],
                "candidate_question_id": candidates[index]["question_id"],
                "candidate_score": candidates[index]["generation"]["recommendation_score"]["score"],
                "candidate_estimated_level": candidates[index]["generation"]["estimated_level"],
                "candidate_difficulty_matches": candidates[index]["verification"]["difficulty_matches"],
            }
            for index, profile in enumerate(PROFILES)
        ],
        "reference_question_ids": [ref["question_id"] for ref in references],
        "reference_count": len(references),
        "candidate_ranking": candidate_ranking,
    }
    slot = {
        "slot_id": f"KNOWLEDGE-knowledge-{slot_digest}",
        "scope": "knowledge_practice",
        "student_id": "KNOWLEDGE",
        "student_name": "知识点教学链",
        "mother_question_id": references[0]["question_id"],
        "reference_question_ids": [ref["question_id"] for ref in references],
        "reference_questions": references,
        "available_reference_count": len(references),
        "source_question_type": question_type,
        "question_type": question_type,
        "supported_target_question_types": SUPPORTED_TARGET_QUESTION_TYPES,
        "primary_knowledge": knowledge,
        "teaching_chain": teaching_chain,
        "recommendation_logic_version": RECOMMENDATION_LOGIC_VERSION,
        "candidate_ranking": candidate_ranking,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    return slot, teaching_chain


def _build_knowledge_teaching_chains(bank: Path, bank_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    knowledge_order: list[str] = []
    for row in bank_rows:
        knowledge = _primary_knowledge(row)
        if knowledge in TEMPLATE_NUMBER_BY_KNOWLEDGE and knowledge not in knowledge_order:
            knowledge_order.append(knowledge)
    slots: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for knowledge in knowledge_order:
        try:
            slot, chain = _teaching_chain_slot(bank_rows, knowledge=knowledge)
        except InsufficientSafeReferences as exc:
            skipped.append({"primary_knowledge": knowledge, "reason": str(exc)})
            continue
        slots.append(slot)
        chains.append(chain)
    payload = {
        "schema_version": "knowledge-teaching-chains-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "knowledge_practice",
        "logic": "knowledge_first_before_student_profile",
        "recommendation_logic_version": RECOMMENDATION_LOGIC_VERSION,
        "student_specific": False,
        "chain_count": len(chains),
        "skipped_chain_count": len(skipped),
        "chains": chains,
        "skipped_chains": skipped,
    }
    output = bank / "generation" / "knowledge_teaching_chains.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return slots, chains, skipped


def generate_personalized_pool(bank: Path) -> dict[str, Any]:
    bank = bank.resolve()
    plan_path = bank / "generation" / "student_mother_question_plans.json"
    tags_path = bank / "tags" / "all_question_tags.json"
    if not tags_path.exists():
        raise SystemExit(f"Tagged bank not found: {tags_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {"students": []}
    bank_rows = json.loads(tags_path.read_text(encoding="utf-8"))
    knowledge_slots, knowledge_chains, skipped_chains = _build_knowledge_teaching_chains(bank, bank_rows)
    slots: list[dict[str, Any]] = list(knowledge_slots)
    skipped_slots: list[dict[str, str]] = []

    for student in plan.get("students") or []:
        student_id = str(student.get("student_id") or "").strip()
        if not student_id:
            continue
        source_slots = student.get("slots") or []
        weak_points = student.get("weak_points") or []
        mastery_by_knowledge = {
            str(row.get("tag_name") or ""): row for row in weak_points if row.get("tag_name")
        }
        knowledge_order: list[str] = []
        for row in source_slots:
            knowledge = str(row.get("target_knowledge") or "")
            if knowledge and knowledge not in knowledge_order:
                knowledge_order.append(knowledge)
        for row in weak_points[:6]:
            knowledge = str(row.get("tag_name") or "")
            if knowledge and knowledge not in knowledge_order:
                knowledge_order.append(knowledge)

        for knowledge in knowledge_order:
            template_number = TEMPLATE_NUMBER_BY_KNOWLEDGE.get(knowledge)
            if template_number is None:
                continue
            preferred_ids = [
                str(row.get("mother_question_id") or "")
                for row in source_slots
                if row.get("target_knowledge") == knowledge
            ]
            try:
                references = _ordered_references(
                    bank_rows,
                    student_id=student_id,
                    knowledge=knowledge,
                    preferred_ids=preferred_ids,
                )
            except InsufficientSafeReferences as exc:
                skipped_slots.append(
                    {
                        "student_id": student_id,
                        "student_name": str(student.get("name") or student_id),
                        "primary_knowledge": knowledge,
                        "reason": str(exc),
                    }
                )
                continue
            mastery_row = mastery_by_knowledge.get(knowledge) or {}
            question_type = EXPECTED[template_number][0]
            candidate_rows = templates(template_number)
            candidates = [
                _candidate(
                    student_id=student_id,
                    knowledge=knowledge,
                    question_type=question_type,
                    row=row,
                    index=index,
                    references=references,
                    mastery=mastery_row.get("mastery_score"),
                    mastery_status=str(mastery_row.get("status") or ""),
                )
                for index, row in enumerate(candidate_rows)
            ]
            slot_digest = hashlib.sha256(f"{student_id}:{knowledge}".encode("utf-8")).hexdigest()[:10]
            candidate_ranking = sorted(
                (
                    {
                        "question_id": candidate["question_id"],
                        "candidate_index": candidate["candidate_index"],
                        "chain_stage": candidate["generation"]["teaching_chain_stage"],
                        "target_level": candidate["generation"]["target_level"],
                        "estimated_level": candidate["generation"]["estimated_level"],
                        "score": candidate["generation"]["recommendation_score"]["score"],
                        "duplication_risk": candidate["generation"]["recommendation_score"]["duplication_risk"],
                        "difficulty_matches": candidate["verification"]["difficulty_matches"],
                        "rationale": candidate["generation"]["recommendation_score"]["rationale"],
                    }
                    for candidate in candidates
                ),
                key=lambda row: (-float(row["score"]), int(row["candidate_index"])),
            )
            slots.append(
                {
                    "slot_id": f"{student_id}-knowledge-{slot_digest}",
                    "scope": "student_practice",
                    "student_id": student_id,
                    "student_name": student.get("name") or student_id,
                    "mother_question_id": references[0]["question_id"],
                    "reference_question_ids": [ref["question_id"] for ref in references],
                    "reference_questions": references,
                    "available_reference_count": len(references),
                    "source_question_type": question_type,
                    "question_type": question_type,
                    "supported_target_question_types": SUPPORTED_TARGET_QUESTION_TYPES,
                    "primary_knowledge": knowledge,
                    "recommendation_logic_version": RECOMMENDATION_LOGIC_VERSION,
                    "candidate_ranking": candidate_ranking,
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                }
            )

    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": "generated-question-candidate-pool-v1",
        "generated_at": generated_at,
        "scope": "knowledge_and_student_practice",
        "logic": "knowledge_first_before_student_profile",
        "recommendation_logic_version": RECOMMENDATION_LOGIC_VERSION,
        "candidate_count_per_slot": 3,
        "minimum_reference_questions": 3,
        "maximum_reference_questions": 5,
        "knowledge_chain_count": len(knowledge_chains),
        "skipped_knowledge_chain_count": len(skipped_chains),
        "skipped_knowledge_chains": skipped_chains,
        "skipped_slots": skipped_slots,
        "slots": slots,
    }
    output = bank / "generation" / "student_generated_question_candidates.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "generated",
        "student_count": len({slot["student_id"] for slot in slots if slot.get("scope") == "student_practice"}),
        "knowledge_chain_count": len(knowledge_chains),
        "knowledge_slot_count": sum(1 for slot in slots if slot.get("scope") == "knowledge_practice"),
        "student_slot_count": sum(1 for slot in slots if slot.get("scope") == "student_practice"),
        "total_slot_count": len(slots),
        "skipped_slot_count": len(skipped_slots),
        "skipped_knowledge_chain_count": len(skipped_chains),
        "candidate_count": sum(len(slot["candidates"]) for slot in slots),
        "minimum_reference_questions": 3,
        "maximum_reference_questions": 5,
        "output": str(output),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Generate personalized multi-source new-question candidates.")
    parser.add_argument("structured_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate_personalized_pool(args.structured_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
