#!/usr/bin/env python3
"""Generate personalized questions through the local Cherry Studio API Gateway."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from validate_generated_questions import validate_generated_questions


ALLOWED_DIFFICULTIES = {"auto", "consolidation", "matched", "challenge"}
ALLOWED_FOCUSES = {"auto", "概念辨析", "运算巩固", "迁移应用"}
ALLOWED_GENERATION_MODES = {"knowledge", "student"}
ALLOWED_QUESTION_TYPES = {"auto", "single_choice", "multiple_choice", "fill_blank", "solution"}
ALLOWED_REVIEW_STATUSES = {"approved", "pending", "needs_revision"}
QUALITY_FEEDBACK_TYPES = {
    "solution_error",
    "difficulty_too_low",
    "difficulty_too_high",
    "duplicate_structure",
    "unsuitable_training_value",
}
TARGET_QUESTION_TYPES = ("single_choice", "multiple_choice", "fill_blank", "solution")
INEQUALITY_AUTO_TARGET_QUESTION_TYPES = ("single_choice", "solution", "fill_blank")
DIFFICULTY_LABELS = {"auto": "智能匹配", "consolidation": "巩固", "matched": "同步", "challenge": "挑战"}
DEFAULT_TARGET_LEVELS = {"auto": 3, "consolidation": 2, "matched": 3, "challenge": 4}
SOURCE_BLOCKING_FLAGS = {
    "missing_stem",
    "missing_answer",
    "missing_solution",
    "missing_image_asset",
    "answer_contamination",
    "source_solution_invalid",
    "answer_solution_mismatch",
    "solution_alignment_warning",
    "ocr_solution_contamination",
}
NON_CONCRETE_ANSWERS = {"见解析", "详见解析", "见详解", "略", "答案见解析", "答案略"}


def _request_target_level(request: dict[str, Any]) -> int:
    """Return the exact target level while accepting legacy internal test/request rows."""
    difficulty = str(request.get("difficulty") or "auto")
    return max(1, min(5, int(request.get("target_level") or DEFAULT_TARGET_LEVELS.get(difficulty, 3))))


def _strict_target_level(request: dict[str, Any]) -> int | None:
    """Use exact 1-5 hard gates only when the caller explicitly selected a level."""
    return _request_target_level(request) if request.get("target_level_explicit") else None


def _target_level_contract(target_level: int, question_type: str = "") -> dict[str, Any]:
    """Return the exact 1-5 difficulty contract used by prompts and hard gates."""
    target_level = max(1, min(5, int(target_level or 3)))
    rows = {
        1: {
            "label": "1级",
            "required": "直接公式、单步计算或概念识别。",
            "forbidden": "不得出现参数讨论、多层推理、复杂复合函数或分类讨论。",
            "minimum_complexity_signals": 0,
        },
        2: {
            "label": "2级",
            "required": "一个知识点加简单变形，通常 1-2 步。",
            "forbidden": "不得把直接套公式包装成 3级以上训练题。",
            "minimum_complexity_signals": 0,
        },
        3: {
            "label": "3级",
            "required": "2-3 步衔接，至少包含定义域、边界、等价变换或一个非机械推理点。",
            "forbidden": "禁止纯求导、纯代入或简单顶点最值。",
            "minimum_complexity_signals": 1,
        },
        4: {
            "label": "4级",
            "required": "参数、区间、分类、多结论判断或复合函数至少形成两个复杂度信号，并有三个关键判断。",
            "forbidden": "禁止一眼公式题，或多选但四项同一套路。",
            "minimum_complexity_signals": 2,
        },
        5: {
            "label": "5级",
            "required": "恒成立、存在性、范围证明、多问联动、高阶或辅助函数分析中至少三类复杂度信号，关键推理链不少于三段。",
            "forbidden": "禁止一次求导、一次代入、简单二次函数或简单分式最值。",
            "minimum_complexity_signals": 3,
        },
    }
    result = dict(rows[target_level])
    result["target_level"] = target_level
    result["question_type"] = question_type
    return result


def _target_level_reasoning_steps(target_level: int) -> str:
    return {
        1: "1 个直接步骤",
        2: "1-2 个直接步骤",
        3: "2-3 个相互衔接的关键步骤",
        4: "至少 3 个关键判断，包含参数、区间、分类或多结论之一到之二",
        5: "至少 3 段关键推理，并包含三类高复杂度信号",
    }.get(max(1, min(5, int(target_level or 3))), "2-3 个相互衔接的关键步骤")


def _answer_is_concrete(value: Any) -> bool:
    text = re.sub(r"[\s：:。．.!！]+", "", str(value or ""))
    return bool(text) and text not in NON_CONCRETE_ANSWERS
QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "fill_blank": "填空题",
    "solution": "解答题",
}
STRUCTURAL_DIMENSIONS = {
    "question_angle",
    "condition_organization",
    "representation",
    "context",
    "reasoning_path",
}
HISTORY_LOCK = threading.Lock()
BATCH_DRAFT_LOCK = threading.Lock()
BATCH_DRAFT_STEMS: dict[str, list[tuple[float, int, str, str]]] = {}
BATCH_DRAFT_TTL_SECONDS = 30 * 60
CANCELLED_BATCH_LOCK = threading.Lock()
CANCELLED_BATCHES: dict[str, float] = {}
CANCELLED_BATCH_TTL_SECONDS = 60 * 60
VARIATION_PROFILES = (
    "参数求值：改变条件组织，使用新的参数关系或边界条件",
    "性质判断：改变提问角度，侧重充分必要性、真假判断或反例辨析",
    "区间迁移：改变表示与推理路径，在新区间或新变量关系中求解",
    "综合构造：引入辅助函数、图像关系或等价变换，但保持目标难度",
    "情境应用：使用简洁的新背景承载同一知识点，避免原题叙述结构",
)
GENERATION_TASK_SPEC_VERSION = "generation-task-spec-v2"
SAFE_BLUEPRINT_VERIFICATION_POLICY_VERSION = "safe-blueprint-math-only-v4"
MIN_DIAGNOSTIC_EVIDENCE = 3
MAX_DIAGNOSTIC_EVIDENCE = 5
MAX_SLOT_ATTEMPTS = 5
MAX_STAGE_TIMEOUT_SECONDS = 70


def _difficulty_contract(
    difficulty: str,
    question_type: str = "",
    target_level: int | None = None,
) -> dict[str, Any]:
    """Turn the teacher's difficulty choice into an observable generation contract."""
    type_contracts = {
        "single_choice": "四个选项必须对应不同的真实误区；同步或挑战题不能靠一次代入直接排除。",
        "multiple_choice": "四个判断必须彼此独立，至少两个正确项；逐项判断需要不同依据。",
        "fill_blank": "答案必须唯一；同步或挑战题至少包含两段相互依赖的推理，不能一步配方求值。",
        "solution": "必须给出完整推导；同步题至少两步，挑战题至少三段关键推理或分类讨论。",
    }
    profiles = {
        "auto": {
            "label": "智能匹配",
            "target_score": "2–4/5",
            "required": "依据学情画像与单道结构母题确定认知负荷，并根据题型保持适中的推理长度。",
            "forbidden": "不得因为使用熟悉公式而把明显基础题包装成高难题。",
        },
        "consolidation": {
            "label": "巩固",
            "target_score": "1–2/5",
            "required": "聚焦一个核心概念或一种基本方法，条件直接，计算量可控。",
            "forbidden": "避免多参数、长分类讨论或三层以上综合推理。",
        },
        "matched": {
            "label": "同步",
            "target_score": "2–3/5",
            "required": "至少有一个非机械推理点，通常需要 2–3 个相互衔接的步骤。",
            "forbidden": "禁止只套一个公式、只代入一次或只完成一道基础运算。",
        },
        "challenge": {
            "label": "挑战",
            "target_score": "4–5/5",
            "required": (
                "至少落实三类复杂度信号：参数或变量条件、区间端点/分类讨论、超越或复合结构、"
                "恒成立/存在性/范围证明、多问联动或二阶分析；关键结论至少经过三段推理。"
            ),
            "forbidden": (
                "禁止一眼配方、一次求导、一次代入即可完成；尤其禁止把实数域上简单二次函数"
                "的顶点或最值题标成挑战。"
            ),
        },
    }
    result = dict(profiles.get(difficulty) or profiles["auto"])
    if target_level is not None:
        exact = _target_level_contract(target_level, question_type)
        result.update(
            {
                "label": exact["label"],
                "target_score": f"{exact['target_level']}/5",
                "required": exact["required"],
                "forbidden": exact["forbidden"],
                "minimum_complexity_signals": exact["minimum_complexity_signals"],
                "exact_level": exact["target_level"],
            }
        )
    result["question_type_contract"] = type_contracts.get(question_type, "题型结构必须与目标题型一致。")
    return result


def _basic_derivative_task(stem: Any, solution: Any = "") -> bool:
    combined = f"{stem or ''}\n{solution or ''}"
    if not re.search(r"f\s*['′]\s*\(\s*x\s*\)|求导|导函数|导数", combined):
        return False
    if re.search(r"单调|极值|最值|参数|取值范围|恒成立|存在|证明|区间|分类讨论|零点", combined):
        return False
    formula_like = re.search(r"\\sqrt|\bsqrt\b|1\s*/\s*x|x\^\s*\{\s*-?1\s*\}|x\^\s*-?1", combined)
    choice_like = bool(re.search(r"(?:^|\n)\s*[A-D][.．、]", str(stem or "")))
    return bool(formula_like or choice_like)


def _difficulty_gate_evidence(
    difficulty: str,
    question_type: str,
    stem: Any,
    solution: Any = "",
    *,
    target_level: int | None = None,
) -> dict[str, Any]:
    """Collect evidence for the selected target difficulty without emitting a second score."""
    stem_text = str(stem or "")
    solution_text = str(solution or "")
    combined = f"{stem_text}\n{solution_text}"
    compact = re.sub(r"\s+", "", combined)
    signals: list[str] = []

    if re.search(r"参数|实数[a-zA-Z]|[a-zA-Z]\s*\\in\s*\\mathbb|取值范围", combined):
        signals.append("参数或范围条件")
    if re.search(r"区间|端点|边界|分类讨论|分情况|当.+?时.+?当.+?时", combined, re.S):
        signals.append("区间边界或分类讨论")
    advanced_tokens = {
        token
        for token, pattern in (
            ("对数", r"\\ln|\bln\s*\("),
            ("指数", r"e\^|\\mathrm\{e\}|\\exp"),
            ("三角", r"\\sin|\\cos|\\tan"),
            ("分式", r"\\frac|/\s*[a-zA-Z(]"),
            ("根式", r"\\sqrt"),
        )
        if re.search(pattern, combined)
    }
    if advanced_tokens:
        signals.append("复合或超越结构（" + "、".join(sorted(advanced_tokens)) + "）")
    if re.search(r"证明|恒成立|存在|唯一|零点|根的个数|充分|必要|最少|至多", combined):
        signals.append("证明、存在性或多结论判断")
    part_markers = re.findall(r"(?:\([1-9]\)|（[1-9]）|[①②③④])", stem_text)
    if len(set(part_markers)) >= 2:
        signals.append("多问联动")
    if re.search(r"二阶导|f\s*['′]\s*['′]|h\s*['′]\s*['′]|辅助函数|构造函数", combined):
        signals.append("辅助函数或高阶分析")
    reasoning_markers = len(re.findall(r"故|因此|从而|进而|比较|再由|结合", solution_text))
    if reasoning_markers >= 3:
        signals.append("三段以上推理链")
    if question_type == "multiple_choice" and len(re.findall(r"(?:^|\n)\s*[A-D][.．、]", stem_text)) >= 4:
        signals.append("多命题逐项辨析")

    simple_quadratic = bool(
        re.search(r"(?:f\s*\(\s*x\s*\)\s*=)?[^\n]{0,20}x\^?\{?2\}?[^\n]{0,35}", compact)
        and not re.search(r"x\^?\{?[3-9]", compact)
        and re.search(r"最[大小]值|极值", stem_text)
        and not advanced_tokens
        and not re.search(r"参数|区间|\[[^\]]+\]|\([^\)]*,[^\)]*\)|证明|恒成立|存在|取值范围", stem_text)
    )
    return {
        "target": difficulty,
        "target_label": DIFFICULTY_LABELS.get(difficulty, difficulty),
        "signals": signals,
        "simple_one_step_pattern": simple_quadratic,
        **(
            {
                "target_level": target_level,
                "exact_level_contract": _target_level_contract(target_level, question_type),
                "basic_derivative_task": _basic_derivative_task(stem_text, solution_text),
            }
            if target_level is not None
            else {}
        ),
    }


def _difficulty_gate_error(
    difficulty: str,
    question_type: str,
    stem: Any,
    solution: Any = "",
    *,
    target_level: int | None = None,
) -> str:
    if difficulty == "auto" and target_level is None:
        return ""
    evidence_row = _difficulty_gate_evidence(
        difficulty,
        question_type,
        stem,
        solution,
        target_level=target_level,
    )
    signal_count = len(evidence_row["signals"])
    if target_level is not None:
        level = max(1, min(5, int(target_level or 3)))
        evidence = "、".join(evidence_row["signals"]) or "未检测到综合推理信号"
        if evidence_row.get("basic_derivative_task") and level >= 3:
            return f"目标为 {level}级，但当前是基础函数求导题；基础求导最高只能作为 2级。"
        if level == 5 and (evidence_row["simple_one_step_pattern"] or signal_count < 3):
            return (
                f"目标为 5级，但当前题目复杂度不足（{evidence}）。"
                "5级必须包含恒成立、存在性、范围证明、多问联动、高阶或辅助函数分析中的至少三类信号。"
            )
        if level == 4 and (evidence_row["simple_one_step_pattern"] or signal_count < 2):
            return (
                f"目标为 4级，但当前题目仍缺少参数、区间、分类或多结论判断等足够信号（{evidence}）。"
                "请更换结构，而不是只增加计算量。"
            )
        if level == 3 and (evidence_row["simple_one_step_pattern"] or signal_count < 1):
            return "目标为 3级，但当前仍是一步基础题；请增加一个非机械推理点和相互衔接的步骤。"
        if level <= 2 and signal_count > 2:
            return f"目标为 {level}级，但当前题目超出低阶训练范围；请减少参数、分类或多层综合。"
        return ""
    if difficulty == "challenge" and (evidence_row["simple_one_step_pattern"] or signal_count < 3):
        evidence = "、".join(evidence_row["signals"]) or "未检测到综合推理信号"
        return (
            f"目标为挑战，但当前题目未达到该目标难度（{evidence}）。"
            "请更换数学结构，并至少加入参数/范围、边界分类、复合函数、证明或多问联动中的三类。"
        )
    if difficulty == "matched" and signal_count < 1:
        return "目标为同步，但当前仍是一步基础题；请增加一个非机械推理点和相互衔接的步骤。"
    if difficulty == "consolidation" and signal_count > 2:
        return "当前题目超出目标难度“巩固”；请减少参数、分类或多层综合。"
    return ""


def _method_family(knowledge: str, stem: Any, solution: Any = "") -> str:
    combined = f"{knowledge}\n{stem or ''}\n{solution or ''}"
    if _basic_derivative_task(stem, solution):
        return "basic_derivative_formula"
    if re.search(r"切线|斜率", combined):
        return "tangent_derivative"
    if re.search(r"单调|递增|递减", combined):
        return "monotonicity_interval"
    if re.search(r"极值|最值|最大值|最小值", combined):
        return "extremum_boundary_analysis"
    if re.search(r"恒成立|不等式|证明|构造函数|辅助函数", combined):
        return "auxiliary_function_proof"
    if re.search(r"零点|根的个数", combined):
        return "zero_count_analysis"
    if re.search(r"全概率|条件概率|期望|方差|分布列", combined):
        return "probability_branch_analysis"
    return "general_transfer"


def _function_family(stem: Any) -> str:
    text = str(stem or "")
    has_sqrt = bool(re.search(r"\\sqrt|\bsqrt\b", text))
    has_reciprocal = bool(re.search(r"1\s*/\s*x|x\^\s*\{\s*-?1\s*\}|x\^\s*-?1|\\frac\{[^{}]+\}\{x\}", text))
    if has_sqrt and has_reciprocal:
        return "radical_plus_reciprocal"
    if re.search(r"e\^|\\exp|\\mathrm\{e\}", text) and re.search(r"参数|a\s*\\in|a\s*[∈=]", text):
        return "exponential_parameter"
    if re.search(r"x\^?\{?2\}?", text) and has_reciprocal:
        return "quadratic_plus_reciprocal"
    if re.search(r"\\ln|\bln\s*\(", text) and has_reciprocal:
        return "logarithm_plus_reciprocal"
    if re.search(r"\\sqrt|\bsqrt\b", text):
        return "radical_function"
    if re.search(r"e\^|\\exp|\\mathrm\{e\}", text):
        return "exponential_function"
    if re.search(r"\\ln|\bln\s*\(", text):
        return "logarithmic_function"
    if has_reciprocal:
        return "reciprocal_function"
    if re.search(r"x\^?\{?[23]\}?", text):
        return "polynomial_function"
    return "general_function"


def _task_intent(knowledge: str, stem: Any) -> str:
    combined = f"{knowledge}\n{stem or ''}"
    if re.search(r"f\s*['′]\s*\(\s*x\s*\)|求导|导函数|导数", combined) and not re.search(r"单调|极值|最值", combined):
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
    return (knowledge.split("·")[-1] if knowledge else "专项训练")


def _reasoning_pattern(stem: Any, solution: Any = "") -> str:
    combined = f"{stem or ''}\n{solution or ''}"
    evidence = _difficulty_gate_evidence("matched", "", stem, solution)
    if _basic_derivative_task(stem, solution):
        return "direct_formula"
    if "辅助函数或高阶分析" in evidence["signals"]:
        return "auxiliary_or_higher_order"
    if "区间边界或分类讨论" in evidence["signals"]:
        return "interval_classification"
    if "证明、存在性或多结论判断" in evidence["signals"]:
        return "proof_or_multi_conclusion"
    if re.search(r"比较|端点|边界", combined):
        return "boundary_comparison"
    return "single_method_chain"


def _option_pattern(question_type: str, options: Any, answer: Any) -> str:
    if question_type not in {"single_choice", "multiple_choice"}:
        return "non_choice"
    labels = re.findall(r"[A-D]", str(answer or "").upper())
    count = len(set(labels))
    option_count = len(options) if isinstance(options, dict) else 0
    return f"{question_type}:{count}_correct:{option_count}_options"


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
    evidence = _difficulty_gate_evidence("matched", question_type, stem, solution, target_level=target_level)
    return {
        "knowledge": knowledge,
        "question_type": question_type,
        "target_level": max(1, min(5, int(target_level or 3))),
        "method_family": _method_family(knowledge, stem, solution),
        "function_family": _function_family(stem),
        "task_intent": _task_intent(knowledge, stem),
        "reasoning_pattern": _reasoning_pattern(stem, solution),
        "option_pattern": _option_pattern(question_type, options, answer),
        "complexity_signals": evidence["signals"],
    }


def _pedagogical_fingerprint_key(fingerprint: dict[str, Any]) -> str:
    fields = (
        "knowledge",
        "question_type",
        "target_level",
        "method_family",
        "task_intent",
        "reasoning_pattern",
        "option_pattern",
    )
    return "|".join(str(fingerprint.get(field) or "") for field in fields)

SAFETY_FAMILY_COUNTS = {
    "monotonicity": 20,
    "inequality": 35,
    "inequality_challenge_choice": 6,
    "inequality_solution": 12,
    "inequality_fill": 12,
    "extremum": 20,
    "extremum_single": 10,
    "extremum_fill": 10,
    "extremum_solution": 10,
    "derivative": 10,
    "total_probability": 10,
    "tangent": 10,
    "parity_periodicity": 10,
    "derivative_definition": 10,
    "optimum": 10,
    "zeros": 10,
    "expectation_variance": 10,
}

DYNAMIC_STRUCTURE_OBJECTS = (
    "对数与有理式组合，但极值点必须能精确求出",
    "指数函数与一次式乘积，导数可直接因式分解",
    "正变量上的幂函数与倒数项组合",
    "含 ln(1+x) 的辅助函数",
    "t ln t 型函数与线性项组合",
    "两个不同指数率的指数和",
    "可精确判号的多项式与指数乘积",
    "带参数但等号点预先可精确确定的函数",
    "通过变量代换得到的单峰或单谷函数",
    "二阶导恒正且驻点可精确求出的凸函数",
)

DYNAMIC_CONDITION_TRANSFORMS = (
    "平移变量后设置等号点",
    "正比例缩放后设置等号点",
    "倒数代换 t=c/x",
    "指数代换 t=e^x",
    "对数代换 t=ln x 并明确正定义域",
    "把恒成立条件改写为辅助函数最值",
    "把参数范围问题改写为唯一等号条件",
)

DYNAMIC_PROOF_ROUTES = (
    "一阶导数由负到正证明唯一最小值",
    "一阶导数由正到负证明唯一最大值",
    "二阶导数证明严格凸性后定位驻点",
    "比较两个函数的单调性并定位唯一交点",
    "先代换再用导数证明非负",
    "构造差函数并同时核对边界与等号条件",
)


def _user_environment_value(name: str) -> str:
    """Read a user-scoped Windows environment value without exposing it to the browser."""
    if name in os.environ:
        return str(os.environ.get(name) or "").strip()
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return str(value or "").strip()
    except (FileNotFoundError, OSError):
        return ""


class LiveGenerationError(RuntimeError):
    status_code = 422


class GenerationCancelledError(LiveGenerationError):
    status_code = 409


class GatewayConfigurationError(LiveGenerationError):
    status_code = 503


class GatewayRequestError(LiveGenerationError):
    status_code = 502


@dataclass(frozen=True)
class GatewayConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        base_url = (_user_environment_value("CHERRY_STUDIO_BASE_URL") or "http://127.0.0.1:24333").rstrip("/")
        api_key = _user_environment_value("CHERRY_STUDIO_API_KEY")
        model = _user_environment_value("CHERRY_STUDIO_MODEL")
        try:
            timeout_seconds = max(20, min(300, int(_user_environment_value("CHERRY_STUDIO_TIMEOUT_SECONDS") or "60")))
        except ValueError as exc:
            raise GatewayConfigurationError("CHERRY_STUDIO_TIMEOUT_SECONDS 必须是整数") from exc
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise GatewayConfigurationError("Cherry Studio 网关地址必须是本机 127.0.0.1、localhost 或 ::1")
        if not api_key:
            raise GatewayConfigurationError("尚未设置 CHERRY_STUDIO_API_KEY")
        if not model or ":" not in model:
            raise GatewayConfigurationError("尚未设置 CHERRY_STUDIO_MODEL，格式应为 providerId:modelId")
        return cls(base_url=base_url, api_key=api_key, model=model, timeout_seconds=timeout_seconds)


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise LiveGenerationError(f"缺少生成所需文件：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise GatewayRequestError(f"Cherry Studio 网关返回 HTTP {exc.code}：{detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise GatewayRequestError(f"无法连接 Cherry Studio 网关：{exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GatewayRequestError("Cherry Studio 网关返回了非 JSON 内容") from exc
    if not isinstance(parsed, dict):
        raise GatewayRequestError("Cherry Studio 网关返回格式不正确")
    return parsed


def gateway_status() -> dict[str, Any]:
    try:
        config = GatewayConfig.from_env()
    except GatewayConfigurationError as exc:
        return {
            "configured": False,
            "reachable": False,
            "authenticated": False,
            "model": _user_environment_value("CHERRY_STUDIO_MODEL"),
            "message": str(exc),
        }
    try:
        _http_json("GET", f"{config.base_url}/health", timeout=5)
        models = _http_json(
            "GET",
            f"{config.base_url}/v1/models?limit=500",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=10,
        )
        model_ids = {
            str(row.get("id") or "")
            for row in (models.get("data") or models.get("items") or [])
            if isinstance(row, dict)
        }
        available = not model_ids or config.model in model_ids
        return {
            "configured": True,
            "reachable": True,
            "authenticated": True,
            "model": config.model,
            "model_available": available,
            "message": "Cherry Studio 实时生题已连接" if available else "网关已连接，但所选模型不在模型列表中",
        }
    except GatewayRequestError as exc:
        return {
            "configured": True,
            "reachable": False,
            "authenticated": False,
            "model": config.model,
            "message": str(exc),
        }


def _content_text(message_content: Any) -> str:
    if isinstance(message_content, str):
        return message_content
    if isinstance(message_content, list):
        pieces = []
        for item in message_content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        return "\n".join(pieces)
    return ""


def _repair_model_json(text: str) -> str:
    latex_commands = (
        r"frac|dfrac|tfrac|sqrt|ln|log|sin|cos|tan|cot|sec|csc|left|right|"
        r"mathbb|mathrm|mathbf|operatorname|cdot|times|div|pm|mp|leq?|geq?|neq|"
        r"infty|sum|prod|int|lim|to|in|notin|cup|cap|alpha|beta|gamma|theta|pi|"
        r"begin|end|text|overline|underline|vec"
    )
    repaired = re.sub(rf"(?<!\\)\\(?=(?:{latex_commands})\b)", r"\\\\", text)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    candidates = [text]
    if start >= 0 and end > start and text[start : end + 1] != text:
        candidates.append(text[start : end + 1])
    value = None
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        for current in (candidate, _repair_model_json(candidate)):
            try:
                value = json.loads(current)
                break
            except json.JSONDecodeError as exc:
                last_error = exc
        if value is not None:
            break
    if value is None:
        if start < 0 or end <= start:
            raise LiveGenerationError("模型没有返回可解析的 JSON 对象")
        try:
            value = json.loads(_repair_model_json(text[start : end + 1]))
        except json.JSONDecodeError as exc:
            raise LiveGenerationError("模型返回的 JSON 格式不正确") from (last_error or exc)
    if not isinstance(value, dict):
        raise LiveGenerationError("模型必须返回一个 JSON 对象")
    return value


def _gateway_chat_json(
    config: GatewayConfig,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int = 2400,
    deadline: float | None = None,
    batch_id: str = "",
) -> dict[str, Any]:
    token_budget = max(800, min(3000, int(max_tokens)))
    last_error: Exception | None = None
    current_messages = messages
    raw_fragments: list[str] = []
    stage_deadline = deadline or (time.monotonic() + min(60, config.timeout_seconds))
    for retry_index in range(3):
        _ensure_batch_active(batch_id)
        remaining_seconds = stage_deadline - time.monotonic()
        if remaining_seconds < 5:
            raise GatewayRequestError("Cherry Studio 本阶段已到总时限，未继续启动格式修复请求")
        response = _http_json(
            "POST",
            f"{config.base_url}/v1/chat/completions",
            payload={
                "model": config.model,
                "messages": current_messages,
                "temperature": temperature if retry_index == 0 else min(temperature, 0.15),
                "reasoning_effort": "low",
                "stream": False,
                "max_tokens": token_budget if retry_index == 0 else 3000,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=max(5, min(config.timeout_seconds, int(remaining_seconds))),
        )
        choices = response.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise GatewayRequestError("Cherry Studio 网关响应中没有 choices")
        choice = choices[0]
        content = _content_text((choice.get("message") or {}).get("content"))
        if not content:
            last_error = GatewayRequestError("Cherry Studio 网关响应中没有文本内容")
            current_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "上一次没有正文。请只返回一个完整 JSON 对象，不要输出思考、代码围栏或额外文字；"
                        "solution_markdown 不超过 350 个中文字符。"
                    ),
                },
            ]
            continue
        raw_fragments.append(content)
        try:
            return _parse_json_content(content)
        except LiveGenerationError as exc:
            finish_reason = str(choice.get("finish_reason") or "")
            reason = "输出达到长度上限而被截断" if finish_reason in {"length", "max_tokens"} else str(exc)
            last_error = GatewayRequestError(f"Cherry Studio 返回的 JSON 无法解析：{reason}")
            current_messages = [
                {
                    "role": "system",
                    "content": (
                        "你是 JSON 截断修复器。根据已有响应提取题干、选项和答案，补成一个完整 JSON 对象。"
                        "保持题干与答案；将 solution_markdown 压缩为最终正确推导，不超过 350 个中文字符。"
                        "禁止输出试算、自我修正、代码围栏或 JSON 之外的文字。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "修复并闭合被截断或格式错误的 JSON 响应",
                            "raw_response": "\n\n--- 修复轮次分隔 ---\n\n".join(raw_fragments)[-24000:],
                            "required_fields": [
                                "question_type",
                                "stem_markdown",
                                "options",
                                "answer",
                                "solution_markdown",
                                "changed_dimensions",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
    raise last_error or GatewayRequestError("Cherry Studio 返回的 JSON 无法解析")


def _personal_skill_rules() -> str:
    skill_path = Path(__file__).resolve().parents[1] / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    sections = []
    heading_groups = (
        ("智能新题流程", "个性新题流程"),
        ("诊断证据、任务单与结构锚点安全门",),
        ("智能新题生成门", "个性新题生成门"),
    )
    for aliases in heading_groups:
        for heading in aliases:
            match = re.search(rf"^## {re.escape(heading)}\s*$([\s\S]*?)(?=^## |\Z)", text, flags=re.MULTILINE)
            if match:
                sections.append(f"## {heading}\n{match.group(1).strip()}")
                break
    if len(sections) != 3:
        raise LiveGenerationError("生题 Skill 规则不完整：缺少智能新题流程或安全门章节")
    return "\n\n".join(sections)


def _generation_history_path(bank: Path) -> Path:
    return bank / "generation" / "live_personalized_generation_history.jsonl"


def _generation_rejection_path(bank: Path) -> Path:
    return bank / "generation" / "live_personalized_generation_rejections.jsonl"


def _review_state_path(bank: Path) -> Path:
    return bank / "review" / "personalized_question_reviews.jsonl"


def _teacher_quality_feedback_path(bank: Path) -> Path:
    return bank / "generation" / "teacher_quality_feedback.jsonl"


def _quality_feedback_rows(bank: Path) -> list[dict[str, Any]]:
    path = _teacher_quality_feedback_path(bank)
    if not path.exists() or not path.stat().st_size:
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _recent_quality_feedback(bank: Path, request: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _quality_feedback_rows(bank)
        if str(row.get("knowledge") or "") == request["knowledge"]
        and str(row.get("question_type") or "") in {"", request["question_type"], "auto"}
    ]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows[:limit]


def _feedback_prompt_lines(feedback_rows: list[dict[str, Any]]) -> list[str]:
    labels = {
        "solution_error": "解析错误",
        "difficulty_too_low": "难度偏低",
        "difficulty_too_high": "难度偏高",
        "duplicate_structure": "结构重复",
        "unsuitable_training_value": "训练价值不足",
    }
    lines: list[str] = []
    for row in feedback_rows:
        fingerprint = row.get("pedagogical_fingerprint") if isinstance(row.get("pedagogical_fingerprint"), dict) else {}
        key = _pedagogical_fingerprint_key(fingerprint) if fingerprint else ""
        lines.append(
            f"{labels.get(str(row.get('feedback_type') or ''), row.get('feedback_type'))}: "
            f"{str(row.get('notes') or '').strip()[:120]}；禁用结构={key or '同类负样本'}"
        )
    return [line for line in lines if line.strip()]


def _all_history_rows(bank: Path) -> list[dict[str, Any]]:
    path = _generation_history_path(bank)
    if not path.exists() or not path.stat().st_size:
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _history_rows(bank: Path) -> list[dict[str, Any]]:
    """Only delivered batches participate in reuse, diversity and similarity gates."""
    delivered: list[dict[str, Any]] = []
    for row in _all_history_rows(bank):
        status = str(row.get("delivery_status") or "").strip()
        if status == "committed":
            delivered.append(row)
            continue
        # History written before delivery_status existed contains many generated
        # drafts and developer smoke runs.  Treating every legacy row as delivered
        # permanently exhausts exact families even though no teacher accepted the
        # question.  Preserve only genuinely approved legacy questions.
        teacher_status = str(
            ((row.get("question") or {}).get("teacher_review") or {}).get("status") or ""
        ).strip()
        if not status and teacher_status == "approved":
            delivered.append(row)
    return delivered


def _rejection_rows(bank: Path) -> list[dict[str, Any]]:
    path = _generation_rejection_path(bank)
    if not path.exists() or not path.stat().st_size:
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _append_rejection(bank: Path, request: dict[str, Any], draft: dict[str, Any], reason: str) -> None:
    stem = str(draft.get("stem_markdown") or draft.get("stem") or draft.get("question") or "").strip()
    if not stem:
        return
    path = _generation_rejection_path(bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "live-personalized-generation-rejection-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "student_id": request["student_id"],
        "knowledge": request["knowledge"],
        "batch_id": str(request.get("batch_id") or ""),
        "slot_index": int(request.get("slot_index") or 0),
        "attempt": int(request.get("attempt") or 1),
        "diversity_round": int(request.get("diversity_round") or 1),
        "question_type": str(draft.get("question_type") or ""),
        "stem_markdown": stem[:1600],
        "answer": str(draft.get("answer") or "")[:400],
        "structure": _number_agnostic_structure(stem),
        "math_structure": _math_structure(stem),
        "safety_blueprint_family": str(
            draft.get("_safety_blueprint_family") or draft.get("family_id") or ""
        ),
        "verification_policy_version": str(draft.get("_verification_policy_version") or ""),
        "reason": str(reason or "生成或校验失败")[:600],
    }
    with HISTORY_LOCK, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _normalise_text(value: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", _strip_latex_presentation(value).lower())


def _strip_latex_presentation(value: Any) -> str:
    text = str(value or "")
    text = text.replace(r"\dfrac", r"\frac").replace(r"\tfrac", r"\frac")
    text = re.sub(r"\\(?:left|right|displaystyle|textstyle|scriptstyle|scriptscriptstyle)\b", "", text)
    text = re.sub(r"\\(?:quad|qquad)\b", "", text)
    text = re.sub(r"\\[,!;:]", "", text)
    return text


def _number_agnostic_structure(value: Any) -> str:
    text = _strip_latex_presentation(value).lower().replace("−", "-")
    text = re.sub(r"\d+(?:\.\d+)?", "#", text)
    return re.sub(r"\s+", "", text)


def _math_structure(value: Any) -> str:
    segments = re.findall(r"\$[^$]+\$", str(value or ""))
    return _number_agnostic_structure("|".join(segments)) if segments else ""


def _normalise_answer(value: Any) -> str:
    text = _strip_latex_presentation(_clean_answer_content(value)).upper().replace("−", "-")
    chained = re.fullmatch(
        r"\s*(.+?)\s*(\\LEQ?|≤|<)\s*[A-Z]\s*(\\LEQ?|≤|<)\s*(.+?)\s*",
        text,
    )
    if chained:
        left = "[" if chained.group(2) in {r"\LE", r"\LEQ", "≤"} else "("
        right = "]" if chained.group(3) in {r"\LE", r"\LEQ", "≤"} else ")"
        text = f"{left}{chained.group(1)},{chained.group(4)}{right}"
    # Non-choice verifiers frequently restate a pure requested answer as
    # ``a=1``, ``a\in[0,1]`` or ``a 的最小值为 1/2``.  Those are presentation
    # variants, not mathematical disagreements.  Choice answers bypass this
    # function and E(X)=... style multi-value answers do not match the prefix.
    text = re.sub(
        r"^\s*(?:故\s*)?(?:(?:参数\s*)?[A-Z](?:\s*的\s*(?:取值范围|最大值|最小值))?"
        r"\s*(?:=|∈|\\IN\b|为|是)|(?:取值范围|最大值|最小值)\s*(?:=|为|是))\s*",
        "",
        text,
    )
    text = text.replace("{", "").replace("}", "").replace("，", ",")
    return re.sub(r"[\s`$。．.,]", "", text)


def _parameter_bound_endpoints(value: Any) -> set[str]:
    """Return finite endpoints when a verifier restates an optimum as a range."""
    text = _strip_latex_presentation(_clean_answer_content(value)).upper().replace("−", "-")
    text = re.sub(
        r"^\s*(?:参数\s*)?[A-Z]\s*的\s*取值范围\s*(?:为|是|[:：])\s*",
        "",
        text,
    )
    endpoints: set[str] = set()
    simple = re.fullmatch(
        r"\s*(?:参数\s*)?[A-Z]\s*(?:\\LEQ?|\\GEQ?|≤|≥|<|>)\s*(.+?)\s*",
        text,
    )
    if simple:
        endpoints.add(_normalise_answer(simple.group(1)))
    chained = re.fullmatch(
        r"\s*(.+?)\s*(?:\\LEQ?|≤|<)\s*[A-Z]\s*(?:\\LEQ?|≤|<)\s*(.+?)\s*",
        text,
    )
    if chained:
        endpoints.update({_normalise_answer(chained.group(1)), _normalise_answer(chained.group(2))})
    membership = re.fullmatch(
        r"\s*(?:参数\s*)?[A-Z]\s*(?:\\IN\b|∈)\s*[\[(]\s*(.+?)\s*[,，]\s*(.+?)\s*[\])]\s*",
        text,
    )
    if membership:
        for endpoint in membership.groups():
            normalised = _normalise_answer(endpoint)
            if "INFTY" not in normalised and "∞" not in normalised:
                endpoints.add(normalised)
    return {endpoint for endpoint in endpoints if endpoint}


def _rational_answer_value(value: Any) -> Fraction | None:
    """Parse common pure rational styles without evaluating arbitrary text."""
    raw = _strip_latex_presentation(_clean_answer_content(value)).upper()
    raw = re.sub(r"\s+", "", raw)
    raw_decimal = re.fullmatch(r"-?\d+\.\d+", raw)
    if raw_decimal:
        return Fraction(raw)
    normalised = _normalise_answer(value)
    direct = re.fullmatch(r"(-?\d+)(?:/(\d+))?", normalised)
    if direct:
        return Fraction(int(direct.group(1)), int(direct.group(2) or 1))
    decimal = re.fullmatch(r"-?\d+\.\d+", normalised)
    if decimal:
        return Fraction(normalised)
    latex_fraction = re.fullmatch(
        r"\\FRAC(?:\{(-?\d+)\}|(-?\d))(?:\{(\d+)\}|(\d))",
        raw,
    )
    if latex_fraction:
        numerator = latex_fraction.group(1) or latex_fraction.group(2)
        denominator = latex_fraction.group(3) or latex_fraction.group(4)
        return Fraction(int(numerator), int(denominator))
    return None


def _extract_rational_answer_values(value: Any) -> list[Fraction]:
    """Extract ordered, exact numeric answers without evaluating model text."""
    text = str(value or "").replace("％", "%")
    text = re.sub(r"[（(]\s*\d+\s*[)）]", " ", text)
    pattern = re.compile(
        r"\\frac\{(-?\d+)\}\{(\d+)\}|(-?\d+)\s*/\s*(\d+)|(-?\d+\.\d+|-?\d+)"
    )
    values: list[Fraction] = []
    for match in pattern.finditer(text):
        if match.group(1) is not None:
            values.append(Fraction(int(match.group(1)), int(match.group(2))))
        elif match.group(3) is not None:
            values.append(Fraction(int(match.group(3)), int(match.group(4))))
        else:
            values.append(Fraction(match.group(5)))
    return values


def _fraction_latex(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else rf"\frac{{{value.numerator}}}{{{value.denominator}}}"


def _fixed_probability_branch_contract(stem: Any) -> dict[str, Any] | None:
    """Parse a fixed-prior, fixed-conditional branch model generated by the LLM."""
    stem_text = str(stem or "").replace("％", "%")
    if not re.search(r"占(?:总产量|总数|总体|全部)|占比|供应比例|来源比例", stem_text):
        return None
    def percent_list(value: Any) -> list[Fraction]:
        return [
            Fraction(token) / 100
            for token in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*%", str(value or ""))
        ]

    grouped_priors = re.search(
        r"(?:占比|比例)\s*分别为\s*((?:\d+(?:\.\d+)?\s*%\s*[、,，]?\s*)+)",
        stem_text,
    )
    grouped_conditionals = re.search(
        r"(?:合格率|不合格率|次品率|成功率|命中率)\s*分别为\s*"
        r"((?:\d+(?:\.\d+)?\s*%\s*[、,，]?\s*)+)",
        stem_text,
    )
    if grouped_priors and grouped_conditionals:
        priors = percent_list(grouped_priors.group(1))
        conditionals = percent_list(grouped_conditionals.group(1))
    else:
        percentages = percent_list(stem_text)
        if len(percentages) < 4 or len(percentages) % 2:
            return None
        priors = percentages[0::2]
        conditionals = percentages[1::2]
    if len(priors) < 2 or len(priors) != len(conditionals):
        return None
    if sum(priors, Fraction(0)) != 1 or any(not 0 <= value <= 1 for value in conditionals):
        return None
    setup = re.split(r"[（(]\s*1\s*[)）]", stem_text, maxsplit=1)[0]
    latin_labels = re.findall(r"(?:型号\s*)?([A-D])(?=\s*(?:占|类))", setup, flags=re.I)
    chinese_labels = re.findall(
        r"([甲乙丙丁])(?:车间|生产线|线|工厂|厂|盒|通道|类)?[^%，。；\n]{0,20}(?:占|比例)",
        setup,
    )
    grouped_chinese = re.search(
        r"(?:生产线|型号|车间|通道|来源|类别)?\s*"
        r"([甲乙丙丁](?:\s*[、,，]\s*[甲乙丙丁])+)",
        setup,
    )
    grouped_latin = re.search(
        r"(?:生产线|型号|车间|通道|来源|类别)?\s*"
        r"([A-D](?:\s*[、,，]\s*[A-D])+)",
        setup,
        flags=re.I,
    )
    if grouped_chinese:
        chinese_labels = re.findall(r"[甲乙丙丁]", grouped_chinese.group(1))
    if grouped_latin:
        latin_labels = [label.upper() for label in re.findall(r"[A-D]", grouped_latin.group(1), flags=re.I)]
    labels = [label.upper() for label in latin_labels] or chinese_labels
    if len(labels) != len(priors) or len(set(labels)) != len(labels):
        return None
    is_two_part = bool(re.search(r"[（(]\s*1\s*[)）].*[（(]\s*2\s*[)）]", stem_text, re.S))
    if not is_two_part:
        return None
    query_text = re.split(r"[（(]\s*2\s*[)）]", stem_text, maxsplit=1)[-1]
    target_match = re.search(
        r"来自\s*(?:生产线|型号|车间|通道|来源|类别)?\s*([A-D])",
        query_text,
        flags=re.I,
    )
    target = target_match.group(1).upper() if target_match else ""
    if not target:
        target_match = re.search(
            r"来自\s*(?:生产线|型号|车间|通道|来源|类别)?\s*([甲乙丙丁])",
            query_text,
        )
        target = target_match.group(1) if target_match else ""
    if target not in labels:
        return None
    total = sum((prior * conditional for prior, conditional in zip(priors, conditionals)), Fraction(0))
    if not 0 < total <= 1:
        return None
    target_index = labels.index(target)
    joint = priors[target_index] * conditionals[target_index]
    return {
        "priors": priors,
        "conditionals": conditionals,
        "labels": labels,
        "target": target,
        "target_index": target_index,
        "total": total,
        "joint": joint,
        "posterior": joint / total,
    }


def _repair_probability_branch_draft(knowledge: str, draft: dict[str, Any]) -> bool:
    """Seal arithmetic for a model-designed branch problem without fixing its context or data."""
    if "全概率公式" not in str(knowledge or ""):
        return False
    contract = _fixed_probability_branch_contract(draft.get("stem_markdown"))
    if not contract:
        return False
    total = contract["total"]
    posterior = contract["posterior"]
    products = [
        rf"{_fraction_latex(prior)}\times{_fraction_latex(conditional)}"
        for prior, conditional in zip(contract["priors"], contract["conditionals"])
    ]
    draft["answer"] = f"{_fraction_latex(total)}；{_fraction_latex(posterior)}"
    draft["solution_markdown"] = (
        "记题设中的观测事件为 $D$，各来源构成完备事件组。"
        rf"由全概率公式，$P(D)={'+'.join(products)}={_fraction_latex(total)}$。"
        rf"第（2）问所求条件概率为 $\frac{{{_fraction_latex(contract['joint'])}}}"
        rf"{{{_fraction_latex(total)}}}={_fraction_latex(posterior)}$。"
    )
    draft["_deterministic_probability_check"] = {
        "method": "fraction_branch_recalculation_v1",
        "total": str(total),
        "posterior": str(posterior),
    }
    return True


def _probability_branch_arithmetic_error(
    knowledge: str,
    stem: Any,
    answer: Any,
    solution: Any = "",
) -> str:
    """Deterministically check common full-probability + posterior two-part tasks."""
    if "全概率公式" not in str(knowledge or ""):
        return ""
    contract = _fixed_probability_branch_contract(stem)
    if not contract:
        return ""
    actual = _extract_rational_answer_values(answer)
    if len(actual) < 2:
        return "全概率两问解答缺少两个可精确核对的数值答案"
    if actual[0] != contract["total"]:
        return f"全概率第（1）问算术不一致：题面精确结果应为 {contract['total']}"
    if actual[1] != contract["posterior"]:
        return f"全概率第（2）问算术不一致：题面精确结果应为 {contract['posterior']}"
    if "≈" in str(solution or "") or re.search(r"约为|近似", str(solution or "")):
        return "概率解答必须保留精确分数，禁止用近似小数替代最终答案"
    return ""


def _canonical_symbolic_answer(value: Any) -> str:
    """Canonicalise safe display variants such as Unicode pi and LaTeX frac."""
    text = _strip_latex_presentation(_clean_answer_content(value)).upper().replace("−", "-")
    text = text.replace("π", "PI").replace("Π", "PI").replace(r"\PI", "PI")
    text = text.replace("²", "^2").replace("³", "^3")
    text = text.replace("√", "SQRT").replace(r"\SQRT", "SQRT")
    fraction_pattern = re.compile(r"\\FRAC\{([^{}]+)\}\{([^{}]+)\}")
    while fraction_pattern.search(text):
        text = fraction_pattern.sub(r"(\1)/(\2)", text)
    text = re.sub(r"\\FRAC\s*([^\s{}()])\s*([^\s{}()])", r"(\1)/(\2)", text)
    text = text.replace(r"\CDOT", "").replace("·", "").replace("*", "")
    text = text.replace("{", "(").replace("}", ")")
    text = re.sub(r"[\s`$]", "", text)
    # Parentheses here only encode presentation/grouping in the already sealed
    # exact-answer field.  Removing them makes 4(PI-2)/PI^3 and a LaTeX frac
    # representation converge without evaluating user-controlled expressions.
    return re.sub(r"[()]", "", text)


def _answers_match(first: Any, second: Any, knowledge: str = "") -> bool:
    left, right = _normalise_answer(first), _normalise_answer(second)
    if left == right:
        return True
    left_endpoints = _parameter_bound_endpoints(first)
    right_endpoints = _parameter_bound_endpoints(second)
    if left_endpoints and not right_endpoints and right in left_endpoints:
        return True
    if right_endpoints and not left_endpoints and left in right_endpoints:
        return True
    left_rational = _rational_answer_value(first)
    right_rational = _rational_answer_value(second)
    if left_rational is not None and right_rational is not None and left_rational == right_rational:
        return True
    left_symbolic = _canonical_symbolic_answer(first)
    right_symbolic = _canonical_symbolic_answer(second)
    if left_symbolic and left_symbolic == right_symbolic:
        return True
    if "单调" in knowledge:
        # Textbooks differ on whether a derivative-zero endpoint is attached
        # to a monotonic interval.  The interval core is the same conclusion.
        strip_brackets = lambda value: re.sub(r"[\[\](){}]", "", value.replace(r"\LEFT", "").replace(r"\RIGHT", ""))
        return strip_brackets(left) == strip_brackets(right)
    return False


def _clean_answer_content(value: Any) -> str:
    text = str(value or "").strip().strip("`")
    text = re.sub(
        r"^\s*(?:\*\*|__)?\s*(?:标准)?答案\s*[:：]\s*(?:\*\*|__)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^\s*(?:答|解)\s*[:：]\s*", "", text)
    text = text.replace("**", "").replace("__", "").strip()
    if len(text) >= 2 and text.startswith("$") and text.endswith("$"):
        text = text[1:-1].strip()
    if text.startswith(r"\(") and text.endswith(r"\)"):
        text = text[2:-2].strip()
    if text.startswith(r"\[") and text.endswith(r"\]"):
        text = text[2:-2].strip()
    return text


def _batch_avoid_stems(batch_id: str) -> list[str]:
    if not batch_id:
        return []
    batch_id = _batch_root_id(batch_id)
    now = time.monotonic()
    with BATCH_DRAFT_LOCK:
        for key in list(BATCH_DRAFT_STEMS):
            rows = [
                (created, slot, stem, structure_key)
                for created, slot, stem, structure_key in BATCH_DRAFT_STEMS[key]
                if now - created < BATCH_DRAFT_TTL_SECONDS
            ]
            if rows:
                BATCH_DRAFT_STEMS[key] = rows
            else:
                del BATCH_DRAFT_STEMS[key]
        return [stem for _, _, stem, _ in BATCH_DRAFT_STEMS.get(batch_id, [])]


def _batch_reserved_structure_keys(batch_id: str, except_slot: int = -1) -> set[str]:
    """Return exact families already held by other successful in-flight slots."""
    if not batch_id:
        return set()
    batch_id = _batch_root_id(batch_id)
    now = time.monotonic()
    with BATCH_DRAFT_LOCK:
        rows = [
            (created, slot, stem, structure_key)
            for created, slot, stem, structure_key in BATCH_DRAFT_STEMS.get(batch_id, [])
            if now - created < BATCH_DRAFT_TTL_SECONDS
        ]
        if rows:
            BATCH_DRAFT_STEMS[batch_id] = rows
        else:
            BATCH_DRAFT_STEMS.pop(batch_id, None)
        return {
            structure_key
            for _, slot, _, structure_key in rows
            if structure_key and slot != except_slot
        }


def _clean_batch_id(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "", str(value or "").strip())[:80]


def _batch_root_id(batch_id: str) -> str:
    """Keep initial and recovery requests in one in-flight diversity group."""
    clean = _clean_batch_id(batch_id)
    return re.sub(r"-recovery-\d+$", "", clean)


def _prune_cancelled_batches(now: float) -> None:
    for batch_id, cancelled_at in list(CANCELLED_BATCHES.items()):
        if now - cancelled_at >= CANCELLED_BATCH_TTL_SECONDS:
            CANCELLED_BATCHES.pop(batch_id, None)


def _batch_cancelled(batch_id: str) -> bool:
    if not batch_id:
        return False
    now = time.monotonic()
    with CANCELLED_BATCH_LOCK:
        _prune_cancelled_batches(now)
        return any(
            batch_id == cancelled_id or batch_id.startswith(cancelled_id + "-recovery-")
            for cancelled_id in CANCELLED_BATCHES
        )


def _ensure_batch_active(batch_id: str) -> None:
    if _batch_cancelled(batch_id):
        raise GenerationCancelledError("本轮生成已由用户停止")


def cancel_personalized_batch(payload: dict[str, Any], bank: Path | None = None) -> dict[str, Any]:
    requested_batch_id = _clean_batch_id(payload.get("batch_id") if isinstance(payload, dict) else "")
    if not requested_batch_id:
        raise LiveGenerationError("缺少需要停止的 batch_id")
    raw_preserve_ids = payload.get("preserve_question_ids") or [] if isinstance(payload, dict) else []
    if not isinstance(raw_preserve_ids, list):
        raise LiveGenerationError("停止批次时的保留题目列表格式不正确")
    preserve_question_ids = {
        str(value or "").strip()
        for value in raw_preserve_ids[:5]
        if str(value or "").strip()
    }
    batch_id = _batch_root_id(requested_batch_id)
    now = time.monotonic()
    with CANCELLED_BATCH_LOCK:
        _prune_cancelled_batches(now)
        CANCELLED_BATCHES[batch_id] = now
    with BATCH_DRAFT_LOCK:
        for active_id in list(BATCH_DRAFT_STEMS):
            if active_id == batch_id or active_id == _batch_root_id(batch_id):
                BATCH_DRAFT_STEMS.pop(active_id, None)
    discarded = (
        _discard_pending_batch_history(bank, batch_id, preserve_question_ids)
        if bank is not None
        else 0
    )
    return {
        "status": "cancelled",
        "batch_id": batch_id,
        "discarded_pending": discarded,
        "preserved_pending": len(preserve_question_ids),
    }


def _register_batch_draft(batch_id: str, slot_index: int, stem: str, structure_key: str = "") -> None:
    if not batch_id:
        return
    batch_id = _batch_root_id(batch_id)
    normalised = _normalise_text(stem)
    if not normalised:
        raise LiveGenerationError("新题题干为空")
    now = time.monotonic()
    with BATCH_DRAFT_LOCK:
        rows = BATCH_DRAFT_STEMS.setdefault(batch_id, [])
        # A new attempt replaces this slot's previous reservation.  Remove it
        # before comparison so a rejected retry cannot leave a stale blocker.
        rows[:] = [row for row in rows if row[1] != slot_index]
        for _, previous_slot, previous, previous_key in rows:
            previous_normalised = _normalise_text(previous)
            ratio = SequenceMatcher(None, normalised, previous_normalised).ratio() if previous_normalised else 0.0
            same_structure = bool(structure_key and previous_key and structure_key == previous_key)
            model_fingerprint_keys = str(structure_key).startswith("ped:") or str(previous_key).startswith("ped:")
            threshold = (
                0.98
                if structure_key and previous_key and not same_structure and not model_fingerprint_keys
                else 0.82
            )
            if normalised == previous_normalised or same_structure or ratio >= threshold:
                raise LiveGenerationError(f"与本批次已生成题过于相似（{ratio:.3f}），必须更换题型结构或推理路径")
        rows.append((now, slot_index, stem, structure_key))
        del rows[:-20]


def _release_batch_draft(batch_id: str, slot_index: int) -> None:
    """Release a provisional stem after independent verification fails."""
    if not batch_id:
        return
    batch_id = _batch_root_id(batch_id)
    with BATCH_DRAFT_LOCK:
        rows = BATCH_DRAFT_STEMS.get(batch_id)
        if rows is None:
            return
        rows[:] = [row for row in rows if row[1] != slot_index]
        if not rows:
            BATCH_DRAFT_STEMS.pop(batch_id, None)


def _reference_quality_error(row: dict[str, Any]) -> str:
    flags = set(str(value) for value in (row.get("source_quality_flags") or row.get("quality_flags") or []))
    blocking = flags.intersection(SOURCE_BLOCKING_FLAGS)
    if blocking:
        return "源题存在质量警告：" + "、".join(sorted(blocking))
    if not _answer_is_concrete(row.get("answer")):
        return "源题答案不是可校验的具体答案"
    if not str(row.get("solution_markdown") or "").strip():
        return "源题缺少解析"
    return ""


def _slot_and_references(
    bank: Path,
    student_id: str,
    knowledge: str,
    reference_count: int,
    *,
    mode: str = "student",
    question_type: str = "auto",
    slot_index: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pool = _read_json(bank / "generation" / "student_generated_question_candidates.json")
    slots = [row for row in pool.get("slots") or [] if isinstance(row, dict)]
    if mode == "knowledge":
        knowledge_slots = [
            row
            for row in slots
            if str(row.get("primary_knowledge") or "") == knowledge
            and str(row.get("scope") or "") == "knowledge_practice"
        ]
        candidates = knowledge_slots or [
            row for row in slots if str(row.get("primary_knowledge") or "") == knowledge
        ]
        if question_type != "auto":
            typed = [row for row in candidates if str(row.get("question_type") or "") == question_type]
            if typed:
                candidates = typed
        candidates.sort(
            key=lambda row: (
                0 if str(row.get("scope") or "") == "knowledge_practice" else 1,
                str(row.get("question_type") or ""),
                str(row.get("student_id") or ""),
                str(row.get("slot_id") or ""),
            )
        )
        slot = candidates[slot_index % len(candidates)] if candidates else None
    else:
        candidates = [
            row
            for row in slots
            if str(row.get("student_id") or "") == student_id
            and str(row.get("primary_knowledge") or "") == knowledge
        ]
        if question_type != "auto":
            typed = [row for row in candidates if str(row.get("question_type") or "") == question_type]
            if typed:
                candidates = typed
        candidates.sort(key=lambda row: (str(row.get("question_type") or ""), str(row.get("slot_id") or "")))
        slot = candidates[slot_index % len(candidates)] if candidates else None
    if not slot:
        if mode == "knowledge":
            raise LiveGenerationError("没有找到该知识点对应的安全生题槽位")
        raise LiveGenerationError("没有找到该学生与知识点对应的安全生题槽位")
    raw_references = [row for row in slot.get("reference_questions") or [] if isinstance(row, dict)]
    blocked_references = {
        str(row.get("question_id") or "unknown"): error
        for row in raw_references
        if (error := _reference_quality_error(row))
    }
    references = [row for row in raw_references if str(row.get("question_id") or "unknown") not in blocked_references]
    if len(references) < reference_count:
        detail = "；".join(f"{key}:{value}" for key, value in list(blocked_references.items())[:3])
        suffix = f"。已排除可疑源题：{detail}" if detail else ""
        raise LiveGenerationError(f"安全诊断证据只有 {len(references)} 道，无法满足所选 {reference_count} 道{suffix}")
    # Rotate by the slot itself, not by reference_count.  When both were five,
    # the old multiplication always produced offset zero and every batch slot
    # silently reused the same first question as its structural anchor.
    offset = slot_index % len(references) if references else 0
    rotated = references[offset:] + references[:offset]
    selected = rotated[:reference_count]
    if len({str(row.get("question_id") or "") for row in selected}) != reference_count:
        raise LiveGenerationError("诊断证据存在重复，已阻止生成")
    # A reference slot tells us where the safe evidence comes from; it must not
    # dictate the new question's form.  Explicit teacher choice always wins,
    # while auto mode deliberately rotates forms across a 3–5 question batch.
    source_question_type = str(slot.get("question_type") or "")
    auto_types = (
        INEQUALITY_AUTO_TARGET_QUESTION_TYPES
        if "不等式" in knowledge or "构造函数" in knowledge
        else TARGET_QUESTION_TYPES
    )
    target_question_type = (
        question_type if question_type != "auto" else auto_types[slot_index % len(auto_types)]
    )
    target_slot = dict(slot)
    target_slot["source_question_type"] = source_question_type
    target_slot["question_type"] = target_question_type
    target_slot["supported_target_question_types"] = list(TARGET_QUESTION_TYPES)
    return target_slot, selected


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LiveGenerationError("生成请求格式不正确")
    mode = str(payload.get("mode") or "student").strip()
    student_id = str(payload.get("student_id") or "").strip()
    knowledge = str(payload.get("knowledge") or "").strip()
    question_type = str(payload.get("question_type") or "auto").strip()
    difficulty = str(payload.get("difficulty") or "auto").strip()
    focus = str(payload.get("focus") or "auto").strip()
    try:
        reference_count = int(payload.get("reference_count") or 3)
    except (TypeError, ValueError) as exc:
        raise LiveGenerationError("诊断证据数必须是 3、4 或 5") from exc
    if mode not in ALLOWED_GENERATION_MODES:
        raise LiveGenerationError("生成方式不正确")
    if not knowledge:
        raise LiveGenerationError("缺少知识点")
    if mode == "student" and not student_id:
        raise LiveGenerationError("按学情生成时必须选择学生")
    if question_type not in ALLOWED_QUESTION_TYPES:
        raise LiveGenerationError("题型参数不正确")
    if difficulty not in ALLOWED_DIFFICULTIES:
        raise LiveGenerationError("难度参数不正确")
    raw_target_level = payload.get("target_level")
    target_level_explicit = raw_target_level not in (None, "")
    if raw_target_level in (None, ""):
        target_level = DEFAULT_TARGET_LEVELS[difficulty]
    else:
        try:
            target_level = int(raw_target_level)
        except (TypeError, ValueError) as exc:
            raise LiveGenerationError("目标等级必须是 1、2、3、4 或 5") from exc
        if target_level not in {1, 2, 3, 4, 5}:
            raise LiveGenerationError("目标等级必须是 1、2、3、4 或 5")
    if focus not in ALLOWED_FOCUSES:
        raise LiveGenerationError("训练侧重参数不正确")
    if not MIN_DIAGNOSTIC_EVIDENCE <= reference_count <= MAX_DIAGNOSTIC_EVIDENCE:
        raise LiveGenerationError("诊断证据数必须是 3、4 或 5")
    batch_id = _clean_batch_id(payload.get("batch_id"))
    try:
        slot_index = max(0, min(20, int(payload.get("slot_index") or 0)))
    except (TypeError, ValueError):
        slot_index = 0
    try:
        diversity_round = max(1, min(1000, int(payload.get("diversity_round") or 1)))
    except (TypeError, ValueError):
        diversity_round = 1
    try:
        batch_size = max(1, min(5, int(payload.get("batch_size") or 1)))
    except (TypeError, ValueError):
        batch_size = 1
    return {
        "mode": mode,
        "student_id": student_id if mode == "student" else "KNOWLEDGE",
        "student_name": (
            str(payload.get("student_name") or student_id).strip()
            if mode == "student"
            else "按知识点生成"
        ),
        "knowledge": knowledge,
        "question_type": question_type,
        "difficulty": difficulty,
        "target_level": target_level,
        "target_level_explicit": target_level_explicit,
        "focus": focus,
        "reference_count": reference_count,
        "slot_index": slot_index,
        "diversity_round": diversity_round,
        "batch_size": batch_size,
        "batch_id": batch_id,
        "defer_history_commit": bool(payload.get("defer_history_commit")),
    }


def _recent_avoid_stems(bank: Path, student_id: str, knowledge: str) -> list[str]:
    accepted_rows = [
        row
        for row in _history_rows(bank)
        if row.get("student_id") == student_id and row.get("knowledge") == knowledge
    ]
    rejected_rows = [
        row
        for row in _rejection_rows(bank)
        if row.get("student_id") == student_id and row.get("knowledge") == knowledge
    ]
    stems = [
        *[
            str((row.get("question") or {}).get("stem_markdown") or "")[:600]
            for row in accepted_rows[-12:]
            if row.get("question")
        ],
        *[str(row.get("stem_markdown") or "")[:600] for row in rejected_rows[-20:]],
    ]
    return list(dict.fromkeys(stem for stem in stems if stem))


def _rejected_structure_error(
    bank: Path,
    request: dict[str, Any],
    question_type: str,
    stem: str,
) -> str:
    structure = _number_agnostic_structure(stem)
    math_structure = _math_structure(stem)
    normalised = _normalise_text(stem)
    for row in _rejection_rows(bank):
        if str(row.get("student_id") or "") != request["student_id"]:
            continue
        if str(row.get("knowledge") or "") != request["knowledge"]:
            continue
        if str(row.get("question_type") or "") != question_type:
            continue
        if structure and structure == str(row.get("structure") or ""):
            return "该题结构此前已经生成失败，禁止再次返回或只换数字"
        previous_math = str(row.get("math_structure") or "")
        if math_structure and previous_math and math_structure == previous_math:
            return "该题公式结构此前已经生成失败，禁止再次返回或只换数字"
        previous_stem = _normalise_text(row.get("stem_markdown"))
        if normalised and previous_stem and SequenceMatcher(None, normalised, previous_stem).ratio() >= 0.86:
            return "该题与此前失败题过于相似，必须更换数学对象和推理路径"
    return ""


def _compact_reference(row: dict[str, Any]) -> dict[str, Any]:
    """Compact the single full-text anchor sent to the drafting model."""
    return {
        "question_id": str(row.get("question_id") or ""),
        "question_type": str(row.get("question_type") or ""),
        "primary_knowledge": str(row.get("primary_knowledge") or ""),
        "stem_markdown": str(row.get("stem_markdown") or "")[:1200],
        "answer": str(row.get("answer") or "")[:120],
        "solution_excerpt": str(row.get("solution_markdown") or "")[:350],
    }


def _diagnostic_evidence_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Describe learning evidence without leaking another complete question into drafting."""
    text = f"{row.get('stem_markdown') or ''}\n{row.get('solution_markdown') or ''}"
    signal_patterns = (
        ("构造辅助函数", r"辅助函数|构造函数|令\s*[fgh]\s*\("),
        ("导数判号与单调性", r"导数|[fgh]\s*['′]|单调递[增减]"),
        ("参数或分类讨论", r"参数|取值范围|分类讨论|分情况|当.+?时"),
        ("最值或极值定位", r"最[大小]值|极值|驻点"),
        ("定义域与边界核对", r"定义域|端点|边界|x\s*[><]=?\s*0"),
        ("零点与方程分析", r"零点|根的个数|方程"),
        ("奇偶性、周期或对称", r"奇函数|偶函数|周期|对称"),
        ("概率分支与期望", r"全概率|条件概率|期望|方差|分布列"),
        ("切线与变化率", r"切线|斜率|变化率"),
    )
    signals = [label for label, pattern in signal_patterns if re.search(pattern, text, re.S)]
    return {
        "question_id": str(row.get("question_id") or ""),
        "question_type": str(row.get("question_type") or ""),
        "primary_knowledge": str(row.get("primary_knowledge") or ""),
        "reasoning_signals": signals[:4] or ["同知识点作答证据"],
        "usage": "diagnosis_only_no_stem_no_answer",
    }


def _student_mastery_snapshot(bank: Path, request: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] != "student":
        return {
            "source": "teacher_selected_knowledge",
            "student_specific": False,
        }
    plan_path = bank / "generation" / "student_mother_question_plans.json"
    if not plan_path.exists():
        return {
            "source": "student_request_without_mastery_file",
            "student_specific": True,
        }
    plan = _read_json(plan_path)
    student = next(
        (
            row
            for row in plan.get("students") or []
            if isinstance(row, dict) and str(row.get("student_id") or "") == request["student_id"]
        ),
        {},
    )
    weak_point = next(
        (
            row
            for row in student.get("weak_points") or []
            if isinstance(row, dict) and str(row.get("tag_name") or "") == request["knowledge"]
        ),
        {},
    )
    snapshot = {
        "source": "student_mastery_profile",
        "student_specific": True,
        "mastery_score": weak_point.get("mastery_score"),
        "mastery_status": str(weak_point.get("status") or ""),
        "attempts": int(weak_point.get("attempts") or 0),
        "wrongs": int(weak_point.get("wrongs") or 0),
        "partials": int(weak_point.get("partials") or 0),
        "recent_wrongs": int(weak_point.get("recent_wrongs") or 0),
        "evidence_confidence": weak_point.get("evidence_confidence"),
    }
    return {key: value for key, value in snapshot.items() if value not in (None, "")}


def _teaching_chain_snapshot(slot: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] != "knowledge":
        return {}
    chain = slot.get("teaching_chain") if isinstance(slot.get("teaching_chain"), dict) else {}
    stages = chain.get("stages") if isinstance(chain.get("stages"), list) else []
    return {
        "source": "knowledge_teaching_chain",
        "student_specific": False,
        "teaching_chain_id": str(chain.get("chain_id") or slot.get("slot_id") or ""),
        "teaching_chain_logic": chain.get("logic") or "knowledge_first",
        "teaching_chain_stages": stages[:5],
        "current_chain_stage": (stages[int(request.get("slot_index") or 0) % len(stages)] if stages else {}),
    }


def _independent_task_role(knowledge: str, question_type: str) -> dict[str, Any]:
    """Give each batch slot a real training job before any question is drafted."""
    if "不等式" in knowledge or "构造函数" in knowledge:
        roles = {
            "single_choice": {
                "training_goal": "参数边界与恒成立条件辨析",
                "required_features": ["参数范围", "精确极值或等号条件", "三个真实误区干扰项"],
                "forbidden_features": ["一步代入排除", "直接复述结论"],
            },
            "solution": {
                "training_goal": "构造辅助函数并完成严格证明",
                "required_features": ["定义域", "导数符号链", "等号条件", "至少三段关键推理"],
                "forbidden_features": ["A/B/C/D 选项", "数值试根", "定义域不闭合"],
            },
            "fill_blank": {
                "training_goal": "求临界参数、最优常数或唯一边界值",
                "required_features": ["唯一答案", "精确临界值", "至少两段相互依赖推理"],
                "forbidden_features": ["一次配方直接得数", "答案区间不唯一"],
            },
            "multiple_choice": {
                "training_goal": "逐项判断不等式命题的成立条件",
                "required_features": ["至少两个正确项", "逐项独立依据", "定义域核对"],
                "forbidden_features": ["四项同义改写", "只有一个正确项"],
            },
        }
    else:
        roles = {
            "single_choice": {
                "training_goal": "辨析一个核心结论与常见误区",
                "required_features": ["唯一正确项", "三个真实误区干扰项"],
                "forbidden_features": ["仅靠字面或一次代入排除"],
            },
            "multiple_choice": {
                "training_goal": "从不同依据逐项判断多个结论",
                "required_features": ["至少两个正确项", "逐项独立校验"],
                "forbidden_features": ["选项互为简单否定"],
            },
            "fill_blank": {
                "training_goal": "经过完整推理得到唯一精确结果",
                "required_features": ["唯一答案", "边界或定义域核对"],
                "forbidden_features": ["无约束的一步计算"],
            },
            "solution": {
                "training_goal": "组织完整、多步且可复核的解题过程",
                "required_features": ["条件完备", "关键步骤清晰", "最终结论唯一"],
                "forbidden_features": ["选择题选项", "省略关键论证"],
            },
        }
    return roles.get(question_type, roles["solution"])


def _build_generation_task_spec(
    bank: Path,
    request: dict[str, Any],
    slot: dict[str, Any],
    references: list[dict[str, Any]],
    blueprint: dict[str, Any] | None = None,
    quality_feedback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Freeze what to train before drafting; only one source question may define structure."""
    if not references:
        raise LiveGenerationError("缺少可用的学情证据，无法建立出题任务单")
    anchor = references[0]
    question_type = str(slot.get("question_type") or "")
    attempt = max(1, int(request.get("attempt") or 1))
    variation_index = int(request.get("slot_index") or 0) + (attempt - 1) * 2
    target_level = _request_target_level(request)
    level_contract = _target_level_contract(target_level, question_type)
    reasoning_steps = _target_level_reasoning_steps(target_level)
    evidence = [_diagnostic_evidence_summary(row) for row in references]
    task_role = _independent_task_role(request["knowledge"], question_type)
    teaching_chain_profile = _teaching_chain_snapshot(slot, request)
    current_chain_stage = (
        teaching_chain_profile.get("current_chain_stage")
        if isinstance(teaching_chain_profile.get("current_chain_stage"), dict)
        else {}
    )
    blueprint_family = str((blueprint or {}).get("family_id") or "")
    feedback_memory = [
        {
            "feedback_type": str(row.get("feedback_type") or ""),
            "question_id": str(row.get("question_id") or ""),
            "target_level": row.get("target_level"),
            "pedagogical_fingerprint": row.get("pedagogical_fingerprint") or {},
            "notes": str(row.get("notes") or "")[:160],
        }
        for row in (quality_feedback or [])
        if isinstance(row, dict)
    ][:6]
    structure_anchor = (
        {
            "kind": "skill_blueprint",
            "id": blueprint_family,
            "role": "exact_verified_structure_anchor",
            "selection_basis": "Skill 选择未使用且可精确求解的结构族；原题只保留诊断角色",
        }
        if blueprint_family
        else {
            "kind": "mother_question",
            "id": str(anchor.get("question_id") or ""),
            "role": "single_fulltext_structure_anchor",
            "selection_basis": "本题位独立轮换选择；只提供结构参照，不提供拼题素材",
        }
    )
    task_spec = {
        "schema_version": GENERATION_TASK_SPEC_VERSION,
        "strategy": "single_anchor_task_spec",
        "target": {
            "knowledge": request["knowledge"],
            "question_type": question_type,
            "question_type_label": QUESTION_TYPE_LABELS.get(question_type, question_type),
            "difficulty": request["difficulty"],
            "difficulty_level": target_level,
            "target_level": target_level,
            "difficulty_label": f"{target_level}级",
            "level_contract": level_contract,
            "minimum_complexity_signals": level_contract["minimum_complexity_signals"],
            "training_focus": "智能匹配" if request["focus"] == "auto" else request["focus"],
            "knowledge_chain_stage": current_chain_stage.get("stage"),
            "knowledge_chain_stage_label": current_chain_stage.get("label"),
            "knowledge_chain_teaching_goal": current_chain_stage.get("teaching_goal"),
            **task_role,
            "reasoning_steps": reasoning_steps,
        },
        "diagnostic_profile": {
            **_student_mastery_snapshot(bank, request),
            **teaching_chain_profile,
            "evidence_count": len(references),
            "evidence_question_ids": [str(row.get("question_id") or "") for row in references],
            "evidence_summaries": evidence,
        },
        "teacher_feedback_memory": feedback_memory,
        "structure_anchor": structure_anchor,
        "diagnostic_anchor_question_id": str(anchor.get("question_id") or ""),
        "batch_role": {
            "slot_index": int(request.get("slot_index") or 0),
            "slot_number": int(request.get("slot_index") or 0) + 1,
            "batch_size": max(1, int(request.get("batch_size") or 1)),
            "variation_profile": VARIATION_PROFILES[variation_index % len(VARIATION_PROFILES)],
            "independent_generation": True,
        },
        "generation_contract": {
            "required_changed_dimensions": 2,
            "must_change_one_structural_dimension": True,
            "fulltext_source_question_count": 0 if blueprint_family else 1,
            "use_only_one_fulltext_anchor": not blueprint_family,
            "on_failure": "switch_blueprint_or_recipe_instead_of_repairing_the_same_structure",
            "forbidden": [
                "merge_multiple_source_stems",
                "copy_or_paraphrase_anchor",
                "numbers_only_change",
                "reuse_failed_structure",
            ],
        },
    }
    fingerprint_payload = json.dumps(task_spec, ensure_ascii=False, sort_keys=True)
    task_spec["task_spec_id"] = "task-" + hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()[:16]
    return task_spec


def _knowledge_safety_constraints(knowledge: str, question_type: str = "") -> list[str]:
    """Return small, high-value mathematical guardrails for fragile topics."""
    constraints = [
        "输出前在内部从题面重新求解一次；无法严格得到唯一答案时重新设计，不要输出",
        "结论必须能用高中数学的精确推导证明，禁止依赖小数近似或只凭变化趋势猜符号",
    ]
    if "单调" in knowledge:
        constraints.extend(
            [
                "明确区分自变量、参数和函数整体性质：不得求‘使函数在某区间单调的自变量 x’",
                "只可询问函数的单调区间、指定区间上的单调性，或保证单调的参数取值范围",
                "优先选用导数零点可精确求出且符号可严格判断的函数；解析必须核对定义域、导数零点和区间端点",
            ]
        )
    if "不等式" in knowledge or "构造函数" in knowledge:
        constraints.extend(
            [
                "只设计导数零点、最值点和等号条件都能精确求出的辅助函数，禁止依赖数值求根",
                "禁止把最大值点不同的两个函数直接相加后猜测整体最值点",
                "先完整符号推导得到唯一答案，再据此编写题干和干扰项；无法在 350 个中文字符内严格证明就重新设计",
            ]
        )
        if question_type == "solution":
            constraints.extend(
                [
                    "解答题不得出现 A/B/C/D 选项或‘下列结论’措辞；直接给出需要证明的不等式或参数任务",
                    "禁止设计 f(a-x)、f(c-x)、f(φ(x)) 与另一复合函数比较的恒成立题；这类结构容易造成定义域映射不闭合",
                    "优先使用可靠证明路线：构造差函数求唯一最小值、切线不等式、或单调函数比较；等号点必须能精确写出",
                    "先在内部完整写出不超过 350 字的严格证明并核对等号条件，再生成题面；证明过长就降低结构嵌套而不是猜答案",
                ]
            )
    if "极值" in knowledge or "最值" in knowledge:
        constraints.extend(
            [
                "严格区分极值点、极值和最值；不得把 f'(x)=0 单独当作取得极值的充分条件",
                "必须用导数左右变号或严格凸凹性核对极值类型，并逐项验算选择题选项",
                "带参数时先确定参数取值与定义域，再写选项；不得让题设条件本身直接等价于某个选项",
            ]
        )
    if "概率" in knowledge or "期望" in knowledge or "方差" in knowledge:
        constraints.extend(
            [
                "所有概率必须位于 [0,1]，互斥与独立条件必须在题面明确给出",
                "先列出完整样本分支并核对概率和为 1，再计算全概率、期望或方差",
            ]
        )
        if question_type == "solution":
            constraints.append(
                "同步或挑战解答题不得只有一次加权求和；必须设置两个相互依赖的小问，并在解析中明确使用前一问结论"
            )
    return constraints


def _monotonicity_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    """Give fragile monotonicity fill-blanks an exact, independently solvable scaffold."""
    if "单调" not in request["knowledge"] or question_type != "fill_blank":
        return None
    seed = int(request.get("slot_index") or 0) + max(1, int(request.get("attempt") or 1)) - 1
    batch_seed = int(hashlib.sha256(str(request.get("batch_id") or "").encode("utf-8")).hexdigest()[:8], 16)
    variant = seed + batch_seed
    family_override = request.get("_family_override")
    family = (
        int(family_override) % SAFETY_FAMILY_COUNTS["monotonicity"]
        if family_override is not None
        else (
            int(request.get("slot_index") or 0)
            + (max(1, int(request.get("diversity_round") or 1)) - 1)
            * max(1, int(request.get("batch_size") or 1))
        )
        % SAFETY_FAMILY_COUNTS["monotonicity"]
    )
    if family == 0:
        left = -6 + variant % 9
        right = left + 2 + (variant // 9) % 4
        quadratic = -3 * (left + right)
        linear = 6 * left * right
        q_sign, l_sign = ("+" if quadratic >= 0 else "-"), ("+" if linear >= 0 else "-")
        formula = f"f(x)=2x^3 {q_sign} {abs(quadratic)}x^2 {l_sign} {abs(linear)}x"
        domain = r"\mathbb{R}"
        answer = f"[{left}, {right}]"
        left_factor = f"(x- {left})" if left >= 0 else f"(x+ {abs(left)})"
        right_factor = f"(x- {right})" if right >= 0 else f"(x+ {abs(right)})"
        solution = (
            f"求导得 $f'(x)=6{left_factor}{right_factor}$。因为 {left}<{right}，"
            f"当 $x\\in({left},{right})$ 时 $f'(x)<0$，其余区间 $f'(x)>0$，故单调递减区间为 ${answer}$。"
        )
    elif family == 1:
        root = 2 + variant % 8
        constant = root * root
        formula = rf"f(x)=x+\frac{{{constant}}}{{x}}"
        domain, answer = r"(0,+\infty)", f"(0, {root}]"
        solution = (
            rf"$f'(x)=1-\frac{{{constant}}}{{x^2}}=\frac{{(x-{root})(x+{root})}}{{x^2}}$。"
            f"在定义域内，当 $0<x<{root}$ 时导数为负，故单调递减区间为 ${answer}$。"
        )
    elif family == 2:
        constant = 2 + variant % 9
        formula = rf"f(x)=\ln x+\frac{{{constant}}}{{x}}"
        domain, answer = r"(0,+\infty)", f"(0, {constant}]"
        solution = (
            rf"$f'(x)=\frac1x-\frac{{{constant}}}{{x^2}}=\frac{{x-{constant}}}{{x^2}}$。"
            f"当 $0<x<{constant}$ 时导数为负，故单调递减区间为 ${answer}$。"
        )
    elif family == 3:
        constant = 2 + variant % 8
        formula = rf"f(x)=e^x-{constant}x"
        domain, answer = r"\mathbb{R}", rf"(-\infty, \ln {constant}]"
        solution = (
            rf"$f'(x)=e^x-{constant}$，由 $e^x<{constant}$ 得 $x<\ln {constant}$。"
            rf"因此 $f'(x)<0$ 的区间为 ${answer}$，即函数的单调递减区间。"
        )
    elif family == 4:
        constant = 2 + variant % 9
        formula = rf"f(x)=x-{constant}\ln x"
        domain, answer = r"(0,+\infty)", f"(0, {constant}]"
        solution = (
            rf"$f'(x)=1-\frac{{{constant}}}x=\frac{{x-{constant}}}x$。"
            f"在定义域内，当 $0<x<{constant}$ 时导数为负，故单调递减区间为 ${answer}$。"
        )
    elif family == 5:
        root = 2 + variant % 5
        constant = 2 * root**3
        formula = rf"f(x)=x^2+\frac{{{constant}}}x"
        domain, answer = r"(0,+\infty)", f"(0, {root}]"
        solution = (
            rf"$f'(x)=2x-\frac{{{constant}}}{{x^2}}="
            rf"\frac{{2(x^3-{root**3})}}{{x^2}}$。在定义域内，当 $0<x<{root}$ 时导数为负，"
            f"故单调递减区间为 ${answer}$。"
        )
    elif family == 6:
        root = 2 + variant % 6
        constant = 2 * root**2
        formula = rf"f(x)=x^2-{constant}\ln x"
        domain, answer = r"(0,+\infty)", f"(0, {root}]"
        solution = (
            rf"$f'(x)=2x-\frac{{{constant}}}x=\frac{{2(x^2-{root**2})}}x$。"
            f"在定义域内，当 $0<x<{root}$ 时导数为负，故单调递减区间为 ${answer}$。"
        )
    elif family == 7:
        shift = 1 + variant % 6
        turning = shift + 1
        formula = rf"f(x)=(x-{shift})e^{{-x}}"
        domain, answer = r"\mathbb{R}", rf"[{turning},+infty)"
        solution = (
            rf"$f'(x)=e^{{-x}}(1-x+{shift})=e^{{-x}}({turning}-x)$。"
            rf"因为 $e^{{-x}}>0$，当 $x>{turning}$ 时导数为负，故单调递减区间为 ${answer}$。"
        )
    elif family == 8:
        formula = r"f(x)=x^2e^{-x}"
        domain, answer = r"\mathbb{R}", r"(-\infty,0]\cup[2,+\infty)"
        solution = (
            r"$f'(x)=xe^{-x}(2-x)$。因 $e^{-x}>0$，当 $x<0$ 或 $x>2$ 时导数为负，"
            rf"故函数的单调递减区间为 ${answer}$。"
        )
    elif family == 9:
        constant = 2 + variant % 8
        formula = rf"f(x)=\ln x-\frac x{{{constant}}}"
        domain, answer = r"(0,+\infty)", rf"[{constant},+infty)"
        solution = (
            rf"$f'(x)=\frac1x-\frac1{{{constant}}}=\frac{{{constant}-x}}{{{constant}x}}$。"
            rf"在定义域内，当 $x>{constant}$ 时导数为负，故单调递减区间为 ${answer}$。"
        )
    elif family == 10:
        formula, domain, answer = r"f(x)=x^3-3x^2", r"\mathbb{R}", r"[0,2]"
        solution = r"$f'(x)=3x(x-2)$，当 $0<x<2$ 时导数为负，故单调递减区间为 $[0,2]$。"
    elif family == 11:
        formula, domain = r"f(x)=x^4-4x^2+1", r"\mathbb{R}"
        answer = r"(-\infty,-\sqrt2]\cup[0,\sqrt2]"
        solution = r"$f'(x)=4x(x^2-2)$，按三个零点 $-\sqrt2,0,\sqrt2$ 判号，导数为负的区间即答案。"
    elif family == 12:
        formula, domain, answer = r"f(x)=e^{-x}+x", r"\mathbb{R}", r"(-\infty,0]"
        solution = r"$f'(x)=1-e^{-x}$，当 $x<0$ 时 $e^{-x}>1$，故函数在 $(-\infty,0]$ 上递减。"
    elif family == 13:
        formula, domain, answer = r"f(x)=x-e^x", r"\mathbb{R}", r"[0,+\infty)"
        solution = r"$f'(x)=1-e^x$，当 $x>0$ 时导数为负，故单调递减区间为 $[0,+\infty)$。"
    elif family == 14:
        formula, domain, answer = r"f(x)=\frac{x^3}{3}-4x", r"\mathbb{R}", r"[-2,2]"
        solution = r"$f'(x)=x^2-4=(x-2)(x+2)$，在 $(-2,2)$ 上为负，故递减区间为 $[-2,2]$。"
    elif family == 15:
        formula, domain, answer = r"f(x)=\ln(1+x)-\frac x2", r"(-1,+\infty)", r"[1,+\infty)"
        solution = r"$f'(x)=\frac1{1+x}-\frac12=\frac{1-x}{2(1+x)}$，在定义域内 $x>1$ 时为负。"
    elif family == 16:
        formula, domain, answer = r"f(x)=x^2e^x", r"\mathbb{R}", r"[-2,0]"
        solution = r"$f'(x)=xe^x(x+2)$，因 $e^x>0$，导数在 $(-2,0)$ 上为负，故答案为 $[-2,0]$。"
    elif family == 17:
        formula, domain, answer = r"f(x)=(x-2)e^x", r"\mathbb{R}", r"(-\infty,1]"
        solution = r"$f'(x)=e^x(x-1)$，因 $e^x>0$，当 $x<1$ 时导数为负，故答案为 $(-\infty,1]$。"
    elif family == 18:
        formula, domain, answer = r"f(x)=x-1+\frac1{x-1}", r"(1,+\infty)", r"(1,2]"
        solution = r"$f'(x)=1-\frac1{(x-1)^2}$，在定义域内 $1<x<2$ 时为负，故答案为 $(1,2]$。"
    else:
        formula, domain, answer = r"f(x)=x^2-4\ln(x+1)", r"(-1,+\infty)", r"(-1,1]"
        solution = r"$f'(x)=2x-\frac4{x+1}=\frac{2(x+2)(x-1)}{x+1}$，在定义域内 $-1<x<1$ 时为负。"
    return {
        "question_type": "fill_blank",
        "stem_markdown": f"已知函数 ${formula}$ 的定义域为 ${domain}$，则函数 $f(x)$ 的单调递减区间为 ________。",
        "options": {},
        "answer": answer,
        "solution_markdown": solution,
        "changed_dimensions": ["representation", "reasoning_path"],
        "family_id": f"monotonicity-{family}",
        "contract": "题干、系数、定义域和答案必须原样保留；只允许润色解析措辞",
    }


def _inequality_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    """Create exact single-choice scaffolds for derivative-based inequality proofs."""
    if "不等式证明" not in request["knowledge"] or question_type != "single_choice":
        return None
    seed = int(request.get("slot_index") or 0) + max(1, int(request.get("attempt") or 1)) - 1
    batch_seed = int(hashlib.sha256(str(request.get("batch_id") or "").encode("utf-8")).hexdigest()[:8], 16)
    variant = seed + batch_seed
    family_override = request.get("_family_override")
    family = (
        int(family_override) % SAFETY_FAMILY_COUNTS["inequality"]
        if family_override is not None
        else (
            int(request.get("slot_index") or 0)
            + (max(1, int(request.get("diversity_round") or 1)) - 1)
            * max(1, int(request.get("batch_size") or 1))
        )
        % SAFETY_FAMILY_COUNTS["inequality"]
    )
    constant = 2 + variant % 8
    if family == 0:
        inequality = rf"\ln\frac{{x}}{{{constant}}}\leq\frac{{x}}{{{constant}}}-1\quad(x>0)"
        helper = rf"h(x)=\frac{{x}}{{{constant}}}-1-\ln\frac{{x}}{{{constant}}}"
        correct = rf"$h(x)$ 在 $(0,{constant})$ 上递减、在 $({constant},+\infty)$ 上递增，且最小值为 $0$"
        solution = (
            rf"$h'(x)=\frac1{{{constant}}}-\frac1x=\frac{{x-{constant}}}{{{constant}x}}$。"
            rf"所以 $h$ 在 $(0,{constant})$ 上递减、在 $({constant},+\infty)$ 上递增，"
            rf"$h({constant})=0$，从而原不等式成立，故选 A。"
        )
    elif family == 1:
        inequality = rf"\frac{{x}}{{{constant}}}-\frac{{{constant}}}x\geq2\ln\frac{{x}}{{{constant}}}\quad(x\geq {constant})"
        helper = r"h(t)=t-\frac1t-2\ln t\quad(t\geq1)"
        correct = r"$h'(t)=\frac{(t-1)^2}{t^2}\geq0$，且 $h(1)=0$"
        solution = (
            r"令 $t=\frac{x}{" + str(constant) + r"}\geq1$，则 "
            r"$h'(t)=1+\frac1{t^2}-\frac2t=\frac{(t-1)^2}{t^2}\geq0$。"
            r"故 $h(t)\geq h(1)=0$，原不等式成立，选 A。"
        )
    elif family == 2:
        shift = variant % 7
        inequality = rf"e^{{x-{shift}}}\geq1+x-{shift}\quad(x\in\mathbb{{R}})"
        helper = rf"h(x)=e^{{x-{shift}}}-(x-{shift})-1"
        correct = rf"$h(x)$ 在 $x={shift}$ 处取得最小值 $0$"
        solution = (
            rf"$h'(x)=e^{{x-{shift}}}-1$，当 $x<{shift}$ 时为负，当 $x>{shift}$ 时为正。"
            rf"故 $h({shift})=0$ 为最小值，原不等式成立，选 A。"
        )
    elif family == 3:
        inequality = rf"\ln\frac{{x}}{{{constant}}}+\frac{{{constant}}}x\geq1\quad(x>0)"
        helper = rf"h(x)=\ln\frac{{x}}{{{constant}}}+\frac{{{constant}}}x-1"
        correct = rf"$h'(x)=\frac{{x-{constant}}}{{x^2}}$，且 $h({constant})=0$"
        solution = (
            rf"$h'(x)=\frac1x-\frac{{{constant}}}{{x^2}}=\frac{{x-{constant}}}{{x^2}}$。"
            rf"故 $h$ 在 $x={constant}$ 处取得最小值 $h({constant})=0$，原不等式成立，选 A。"
        )
    elif family == 4:
        inequality = rf"\frac{{x}}{{{constant}}}+\frac{{{constant}}}x\geq2\quad(x>0)"
        helper = rf"h(x)=\frac{{x}}{{{constant}}}+\frac{{{constant}}}x-2"
        correct = rf"$h'(x)=\frac{{(x-{constant})(x+{constant})}}{{{constant}x^2}}$，且最小值为 $0$"
        solution = (
            rf"$h'(x)=\frac1{{{constant}}}-\frac{{{constant}}}{{x^2}}="
            rf"\frac{{(x-{constant})(x+{constant})}}{{{constant}x^2}}$。"
            rf"故 $h$ 在 $x={constant}$ 处取得最小值 $0$，原不等式成立，选 A。"
        )
    elif family == 5:
        inequality = rf"\left(\frac x{{{constant}}}\right)^2-1\geq2\ln\frac x{{{constant}}}\quad(x>0)"
        helper = r"h(t)=t^2-1-2\ln t\quad(t>0)"
        correct = r"$h'(t)=\frac{2(t-1)(t+1)}t$，且 $h(1)=0$"
        solution = (
            r"令 $t=\frac{x}{" + str(constant) + r"}>0$，则 "
            r"$h'(t)=2t-\frac2t=\frac{2(t-1)(t+1)}t$。"
            r"故 $h$ 在 $t=1$ 处取得最小值 $0$，原不等式成立，选 A。"
        )
    elif family == 6:
        inequality = rf"\frac x{{{constant}}}\ln\frac x{{{constant}}}-\frac x{{{constant}}}+1\geq0\quad(x>0)"
        helper = r"h(t)=t\ln t-t+1\quad(t>0)"
        correct = r"$h'(t)=\ln t$，且 $h(1)=0$ 为最小值"
        solution = (
            r"令 $t=\frac{x}{" + str(constant) + r"}>0$，则 $h'(t)=\ln t$。"
            r"当 $0<t<1$ 时导数为负，当 $t>1$ 时导数为正，故 $h(1)=0$ 为最小值，选 A。"
        )
    elif family == 7:
        shift = variant % 7
        inequality = rf"e^{{x-{shift}}}+e^{{{shift}-x}}\geq2\quad(x\in\mathbb{{R}})"
        helper = rf"h(x)=e^{{x-{shift}}}+e^{{{shift}-x}}-2"
        correct = rf"$h'(x)=e^{{x-{shift}}}-e^{{{shift}-x}}$，且 $h({shift})=0$ 为最小值"
        solution = (
            rf"$h'(x)=e^{{x-{shift}}}-e^{{{shift}-x}}$。当 $x<{shift}$ 时导数为负，"
            rf"当 $x>{shift}$ 时导数为正，故 $h({shift})=0$ 为最小值，原不等式成立，选 A。"
        )
    elif family == 8:
        inequality = r"\ln(1+x)\geq\frac{x}{1+x}\quad(x>-1)"
        helper = r"h(x)=\ln(1+x)-\frac{x}{1+x}"
        correct = r"$h'(x)=\frac{x}{(1+x)^2}$，且 $h(0)=0$ 为最小值"
        solution = (
            r"$h'(x)=\frac1{1+x}-\frac1{(1+x)^2}=\frac{x}{(1+x)^2}$。"
            r"故 $h$ 在 $(-1,0)$ 上递减、在 $(0,+\infty)$ 上递增，$h(0)=0$，选 A。"
        )
    elif family == 9:
        shift = variant % 7
        inequality = rf"x-{shift}+e^{{{shift}-x}}\geq1\quad(x\in\mathbb{{R}})"
        helper = rf"h(x)=x-{shift}+e^{{{shift}-x}}-1"
        correct = rf"$h'(x)=1-e^{{{shift}-x}}$，且 $h({shift})=0$ 为最小值"
        solution = (
            rf"$h'(x)=1-e^{{{shift}-x}}$。当 $x<{shift}$ 时导数为负，当 $x>{shift}$ 时导数为正，"
            rf"故 $h({shift})=0$ 为最小值，原不等式成立，选 A。"
        )
    elif family == 10:
        inequality = r"\ln(1+x)\leq x\quad(x>-1)"
        helper = r"h(x)=x-\ln(1+x)"
        correct = r"$h'(x)=\frac{x}{1+x}$，且 $h(0)=0$ 为最小值"
        solution = (
            r"$h'(x)=1-\frac1{1+x}=\frac{x}{1+x}$。"
            r"当 $-1<x<0$ 时导数为负，当 $x>0$ 时导数为正，故 $h(0)=0$ 为最小值，选 A。"
        )
    elif family == 11:
        shift = variant % 6
        inequality = rf"e^{{x-{shift}}}(1-x+{shift})\leq1\quad(x\geq {shift})"
        helper = rf"h(x)=1-e^{{x-{shift}}}(1-x+{shift})"
        correct = rf"$h'(x)=(x-{shift})e^{{x-{shift}}}\geq0$，且 $h({shift})=0$"
        solution = (
            rf"$h'(x)=(x-{shift})e^{{x-{shift}}}$。在 $x\geq {shift}$ 上导数非负，"
            rf"故 $h(x)\geq h({shift})=0$，原不等式成立，选 A。"
        )
    elif family == 12:
        inequality = rf"\left(\frac x{{{constant}}}\right)^2+\frac{{2{constant}}}x\geq3\quad(x>0)"
        helper = r"h(t)=t^2+\frac2t-3\quad(t>0)"
        correct = r"$h'(t)=\frac{2(t^3-1)}{t^2}$，且 $h(1)=0$ 为最小值"
        solution = (
            r"令 $t=\frac{x}{" + str(constant) + r"}>0$，则 "
            r"$h'(t)=2t-\frac2{t^2}=\frac{2(t^3-1)}{t^2}$。"
            r"故 $h$ 在 $t=1$ 处取得最小值 $0$，原不等式成立，选 A。"
        )
    elif family == 13:
        shift = variant % 6
        inequality = rf"2e^{{x-{shift}}}+e^{{-2(x-{shift})}}\geq3\quad(x\in\mathbb{{R}})"
        helper = rf"h(x)=2e^{{x-{shift}}}+e^{{-2(x-{shift})}}-3"
        correct = rf"$h'(x)=2e^{{x-{shift}}}-2e^{{-2(x-{shift})}}$，且 $h({shift})=0$ 为最小值"
        solution = (
            rf"$h'(x)=2e^{{x-{shift}}}-2e^{{-2(x-{shift})}}$。当 $x<{shift}$ 时导数为负，"
            rf"当 $x>{shift}$ 时导数为正，故 $h({shift})=0$ 为最小值，选 A。"
        )
    elif family == 14:
        inequality = r"e^x+\frac{x^2}{2}\geq1+x\quad(x\in\mathbb{R})"
        helper = r"h(x)=e^x+\frac{x^2}{2}-1-x"
        correct = r"$h''(x)=e^x+1>0$，且 $h'(0)=h(0)=0$"
        solution = (
            r"$h'(x)=e^x+x-1$，$h''(x)=e^x+1>0$，故 $h'$ 严格递增。"
            r"又 $h'(0)=0$，所以 $h$ 在 $x=0$ 处取得最小值 $h(0)=0$，选 A。"
        )
    elif family == 15:
        inequality = r"e^x\geq1+x+\frac{x^2}{2}\quad(x\geq0)"
        helper = r"h(x)=e^x-1-x-\frac{x^2}{2}"
        correct = r"$h''(x)=e^x-1\geq0$，且 $h'(0)=h(0)=0$"
        solution = (
            r"$h'(x)=e^x-1-x$，$h''(x)=e^x-1\geq0$，故 $h'(x)\geq h'(0)=0$。"
            r"所以 $h(x)\geq h(0)=0$，原不等式成立，选 A。"
        )
    elif family == 16:
        inequality = r"e^x\geq1+x+\frac{x^2}{2}+\frac{x^3}{6}\quad(x\geq0)"
        helper = r"h(x)=e^x-1-x-\frac{x^2}{2}-\frac{x^3}{6}"
        correct = r"$h'(x)\geq0$ 且 $h(0)=0$"
        solution = (
            r"令 $g(x)=e^x-1-x-\frac{x^2}{2}$，则 $g'(x)=e^x-1-x\geq0$ 且 $g(0)=0$，故 $g(x)\geq0$。"
            r"而 $h'(x)=g(x)$、$h(0)=0$，所以 $h(x)\geq0$，选 A。"
        )
    elif family == 17:
        inequality = r"\ln x\leq\frac{x^2-1}{2}\quad(x>0)"
        helper = r"h(x)=\frac{x^2-1}{2}-\ln x"
        correct = r"$h'(x)=\frac{x^2-1}{x}$，且 $h(1)=0$ 为最小值"
        solution = (
            r"$h'(x)=x-1/x=(x^2-1)/x$。故 $h$ 在 $(0,1)$ 上递减、在 $(1,+\infty)$ 上递增，"
            r"$h(1)=0$ 为最小值，原不等式成立，选 A。"
        )
    elif family == 18:
        inequality = r"\ln x\leq\frac{(x-1)(x^2+x+1)}{3}\quad(x>0)"
        helper = r"h(x)=\frac{(x-1)(x^2+x+1)}{3}-\ln x"
        correct = r"$h'(x)=\frac{x^3-1}{x}$，且 $h(1)=0$ 为最小值"
        solution = (
            r"$h'(x)=x^2-1/x=(x^3-1)/x$。故 $h$ 在 $(0,1)$ 上递减、在 $(1,+\infty)$ 上递增，"
            r"$h(1)=0$ 为最小值，原不等式成立，选 A。"
        )
    elif family == 19:
        inequality = r"x-1\geq2(\sqrt{x}-1)\quad(x>0)"
        helper = r"h(x)=x-2\sqrt{x}+1"
        correct = r"$h(x)=(\sqrt{x}-1)^2\geq0$，且 $h(1)=0$"
        solution = r"$h(x)=x-2\sqrt{x}+1=(\sqrt{x}-1)^2\geq0$，等号在 $x=1$ 时成立，故选 A。"
    elif family == 20:
        inequality = r"x+\frac1x\geq2\quad(x>0)"
        helper = r"h(x)=x+\frac1x-2"
        correct = r"$h'(x)=\frac{(x-1)(x+1)}{x^2}$，且 $h(1)=0$ 为最小值"
        solution = (
            r"$h'(x)=1-1/x^2=(x-1)(x+1)/x^2$。故 $h$ 在 $x=1$ 处取得最小值 $0$，"
            r"原不等式成立，选 A。"
        )
    elif family == 21:
        inequality = r"x^2+\frac1{x^2}\geq2\quad(x>0)"
        helper = r"h(x)=x^2+\frac1{x^2}-2"
        correct = r"$h(x)=(x-1/x)^2\geq0$，且 $h(1)=0$"
        solution = r"$h(x)=x^2+1/x^2-2=(x-1/x)^2\geq0$，等号在 $x=1$ 时成立，故选 A。"
    elif family == 22:
        inequality = r"x^3+\frac1{x^3}\geq2\quad(x>0)"
        helper = r"h(x)=x^3+\frac1{x^3}-2"
        correct = r"$h(x)=(x^{3/2}-x^{-3/2})^2\geq0$，且 $h(1)=0$"
        solution = r"$h(x)=x^3+x^{-3}-2=(x^{3/2}-x^{-3/2})^2\geq0$，等号在 $x=1$ 时成立，故选 A。"
    elif family == 23:
        inequality = r"e^x+e^{-x}\geq2+x^2\quad(x\in\mathbb{R})"
        helper = r"h(x)=e^x+e^{-x}-2-x^2"
        correct = r"$h''(x)=e^x+e^{-x}-2\geq0$，且 $h'(0)=h(0)=0$"
        solution = (
            r"$h''(x)=e^x+e^{-x}-2\geq0$，故 $h'$ 递增。又 $h'(0)=0$，"
            r"所以 $h$ 在 $x=0$ 处取最小值 $h(0)=0$，原不等式成立，选 A。"
        )
    elif family == 24:
        inequality = r"\ln(1+x)\leq x-\frac{x^2}{2(1+x)}\quad(x\geq0)"
        helper = r"h(x)=x-\frac{x^2}{2(1+x)}-\ln(1+x)"
        correct = r"$h'(x)=\frac{x^2}{2(1+x)^2}\geq0$，且 $h(0)=0$"
        solution = (
            r"求导得 $h'(x)=x^2/[2(1+x)^2]\geq0$。故 $h$ 在 $[0,+\infty)$ 上递增，"
            r"$h(x)\geq h(0)=0$，原不等式成立，选 A。"
        )
    elif family == 25:
        inequality = r"\ln(1+x)\geq\frac{2x}{2+x}\quad(x\geq0)"
        helper = r"h(x)=\ln(1+x)-\frac{2x}{2+x}"
        correct = r"$h'(x)=\frac{x^2}{(1+x)(2+x)^2}\geq0$，且 $h(0)=0$"
        solution = (
            r"$h'(x)=1/(1+x)-4/(2+x)^2=x^2/[(1+x)(2+x)^2]\geq0$。"
            r"故 $h(x)\geq h(0)=0$，原不等式成立，选 A。"
        )
    elif family == 26:
        inequality = r"(1+x)\ln(1+x)\geq x\quad(x>-1)"
        helper = r"h(x)=(1+x)\ln(1+x)-x"
        correct = r"$h'(x)=\ln(1+x)$，且 $h(0)=0$ 为最小值"
        solution = (
            r"$h'(x)=\ln(1+x)$。当 $-1<x<0$ 时导数为负，当 $x>0$ 时导数为正，"
            r"故 $h(0)=0$ 为最小值，原不等式成立，选 A。"
        )
    elif family == 27:
        inequality = r"x-\ln x\geq1\quad(x>0)"
        helper = r"h(x)=x-\ln x-1"
        correct = r"$h'(x)=\frac{x-1}{x}$，且 $h(1)=0$ 为最小值"
        solution = (
            r"$h'(x)=1-1/x=(x-1)/x$。故 $h$ 在 $(0,1)$ 上递减、在 $(1,+\infty)$ 上递增，"
            r"$h(1)=0$ 为最小值，选 A。"
        )
    elif family == 28:
        inequality = r"x^2-1\geq2\ln x\quad(x>0)"
        helper = r"h(x)=x^2-1-2\ln x"
        correct = r"$h'(x)=\frac{2(x-1)(x+1)}x$，且 $h(1)=0$ 为最小值"
        solution = (
            r"$h'(x)=2x-2/x=2(x-1)(x+1)/x$。故 $h$ 在 $x=1$ 处取得最小值 $0$，"
            r"原不等式成立，选 A。"
        )
    elif family == 29:
        inequality = r"x^4-1\geq4\ln x\quad(x>0)"
        helper = r"h(x)=x^4-1-4\ln x"
        correct = r"$h'(x)=\frac{4(x^4-1)}x$，且 $h(1)=0$ 为最小值"
        solution = (
            r"$h'(x)=4x^3-4/x=4(x^4-1)/x$。故 $h$ 在 $(0,1)$ 上递减、"
            r"在 $(1,+\infty)$ 上递增，$h(1)=0$ 为最小值，选 A。"
        )
    elif family == 30:
        inequality = r"\sin x\leq x\quad(x\geq0)"
        helper = r"h(x)=x-\sin x"
        correct = r"$h'(x)=1-\cos x\geq0$，且 $h(0)=0$"
        solution = r"$h'(x)=1-\cos x\geq0$，故 $h$ 在 $[0,+\infty)$ 上递增，$h(x)\geq h(0)=0$，选 A。"
    elif family == 31:
        inequality = r"1-\cos x\leq\frac{x^2}{2}\quad(x\in\mathbb{R})"
        helper = r"h(x)=\frac{x^2}{2}+\cos x-1"
        correct = r"$h(x)$ 为偶函数，且 $x\geq0$ 时 $h'(x)=x-\sin x\geq0$"
        solution = r"$h$ 为偶函数。对 $x\geq0$，令 $g(x)=x-\sin x$，则 $g'(x)=1-\cos x\geq0$，故 $h'(x)=g(x)\geq0$。于是 $h(x)\geq h(0)=0$，选 A。"
    elif family == 32:
        inequality = r"2x+\frac1{x^2}\geq3\quad(x>0)"
        helper = r"h(x)=2x+\frac1{x^2}-3"
        correct = r"$h'(x)=\frac{2(x^3-1)}{x^3}$，且 $h(1)=0$ 为最小值"
        solution = r"$h'(x)=2-2/x^3=2(x^3-1)/x^3$，故 $h$ 在 $(0,1)$ 上递减、在 $(1,+\infty)$ 上递增，最小值为 $0$，选 A。"
    elif family == 33:
        inequality = r"e^{2x}+2e^{-x}\geq3\quad(x\in\mathbb{R})"
        helper = r"h(x)=e^{2x}+2e^{-x}-3"
        correct = r"$h'(x)=2e^{-x}(e^{3x}-1)$，且 $h(0)=0$ 为最小值"
        solution = r"$h'(x)=2e^{2x}-2e^{-x}=2e^{-x}(e^{3x}-1)$，在 $x=0$ 左负右正，故 $h(0)=0$ 为最小值，选 A。"
    else:
        inequality = r"x^4+3\geq4x\quad(x\in\mathbb{R})"
        helper = r"h(x)=x^4-4x+3"
        correct = r"$h(x)=(x-1)^2(x^2+2x+3)\geq0$"
        solution = r"$h(x)=x^4-4x+3=(x-1)^2(x^2+2x+3)$，且 $x^2+2x+3=(x+1)^2+2>0$，故不等式成立，选 A。"
    options = {
        "A": correct,
        "B": "$h(x)$ 在定义域内恒为严格递增函数",
        "C": "$h(x)$ 的最大值为 $0$",
        "D": f"不等式 ${inequality}$ 的方向应当反向",
    }
    option_text = "\n\n".join(f"{key}. {value}" for key, value in options.items())
    return {
        "question_type": "single_choice",
        "stem_markdown": f"为用构造函数法证明不等式 ${inequality}$，构造 ${helper}$。下列结论正确的是（ ）\n\n{option_text}",
        "options": options,
        "answer": "A",
        "solution_markdown": solution,
        "changed_dimensions": ["condition_organization", "reasoning_path"],
        "family_id": f"inequality-{family}",
        "contract": "题干、参数、选项和答案必须原样保留",
    }


def _extremum_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    """Create exact multi-choice scaffolds for extrema and minimum/maximum questions."""
    if not ("极值" in request["knowledge"] or "最值" in request["knowledge"]) or question_type != "multiple_choice":
        return None
    seed = int(request.get("slot_index") or 0) + max(1, int(request.get("attempt") or 1)) - 1
    batch_seed = int(hashlib.sha256(str(request.get("batch_id") or "").encode("utf-8")).hexdigest()[:8], 16)
    variant = seed + batch_seed
    family_override = request.get("_family_override")
    family = (
        int(family_override) % SAFETY_FAMILY_COUNTS["extremum"]
        if family_override is not None
        else (
            int(request.get("slot_index") or 0)
            + (max(1, int(request.get("diversity_round") or 1)) - 1)
            * max(1, int(request.get("batch_size") or 1))
        )
        % SAFETY_FAMILY_COUNTS["extremum"]
    )
    r = 2 + variant % 5

    if family == 0:
        formula, domain = rf"f(x)=x^3-{3*r*r}x", r"\mathbb{R}"
        options = {
            "A": rf"$x=-{r}$ 是极大值点",
            "B": rf"$x={r}$ 是极小值点",
            "C": rf"$f(x)$ 在 $(-{r},{r})$ 上单调递增",
            "D": "函数的极大值与极小值之和为 $0$",
        }
        answer = "ABD"
        solution = (
            rf"$f'(x)=3(x-{r})(x+{r})$，故在 $x=-{r}$ 处由正变负、在 $x={r}$ 处由负变正。"
            rf"极大值为 ${2*r**3}$，极小值为 $-{2*r**3}$，二者之和为 $0$，故选 ABD。"
        )
    elif family == 1:
        formula, domain = rf"f(x)=(x^2-{r*r})^2", r"\mathbb{R}"
        options = {
            "A": rf"$x=\pm {r}$ 均为极小值点",
            "B": "$x=0$ 是极大值点",
            "C": rf"$f(x)$ 在 $(-{r},0)$ 上单调递减",
            "D": "$f(x)$ 在定义域上没有最大值",
        }
        answer = "ABD"
        solution = (
            rf"$f'(x)=4x(x-{r})(x+{r})$。导数在 $-{r},0,{r}$ 两侧依次变号为 $- + - +$，"
            rf"故 $\pm {r}$ 为极小值点、$0$ 为极大值点，且函数向两端趋于 $+\infty$，无最大值。故选 ABD。"
        )
    elif family == 2:
        formula, domain = rf"f(x)=x+\frac{{{r*r}}}x", r"(0,+\infty)"
        options = {
            "A": rf"$x={r}$ 是唯一极小值点",
            "B": rf"函数的最小值为 ${2*r}$",
            "C": rf"$f(x)$ 在 $(0,{r})$ 上单调递增",
            "D": "$f(x)$ 在定义域上没有最大值",
        }
        answer = "ABD"
        solution = (
            rf"$f'(x)=1-\frac{{{r*r}}}{{x^2}}=\frac{{(x-{r})(x+{r})}}{{x^2}}$。"
            rf"故函数先减后增，在 $x={r}$ 处取最小值 ${2*r}$；"
            rf"当 $x\to0^+$ 或 $x\to+\infty$ 时 $f(x)\to+\infty$，所以无最大值。故选 ABD。"
        )
    elif family == 3:
        formula, domain = r"f(x)=xe^{-x}", r"(0,+\infty)"
        options = {
            "A": "$f(x)$ 在 $(0,1)$ 上单调递增",
            "B": r"$f(x)$ 在 $(1,+\infty)$ 上单调递减",
            "C": r"函数的最大值为 $\frac1e$",
            "D": "函数的最小值为 $0$",
        }
        answer = "ABC"
        solution = (
            r"$f'(x)=e^{-x}(1-x)$，故函数在 $(0,1)$ 上递增、在 $(1,+\infty)$ 上递减，"
            r"最大值为 $f(1)=1/e$；当 $x\to0^+$ 或 $x\to+\infty$ 时只趋于 $0$ 而不取到，故无最小值。故选 ABC。"
        )
    elif family == 4:
        formula, domain = rf"f(x)=\ln x-\frac x{{{r}}}", r"(0,+\infty)"
        options = {
            "A": rf"$f(x)$ 在 $(0,{r})$ 上单调递增",
            "B": rf"$f(x)$ 在 $({r},+\infty)$ 上单调递减",
            "C": rf"$x={r}$ 是唯一极大值点",
            "D": "函数的最小值为 $0$",
        }
        answer = "ABC"
        solution = (
            rf"$f'(x)=\frac1x-\frac1{{{r}}}=\frac{{{r}-x}}{{{r}x}}$。"
            rf"故函数在 $x={r}$ 左增右减，$x={r}$ 为唯一极大值点；定义域两端函数均趋于 $-\infty$，无最小值。故选 ABC。"
        )
    elif family == 5:
        formula, domain = rf"f(x)=x^2+\frac{{{2*r**3}}}x", r"(0,+\infty)"
        options = {
            "A": rf"$f(x)$ 在 $(0,{r})$ 上单调递减",
            "B": rf"$f(x)$ 在 $({r},+\infty)$ 上单调递增",
            "C": rf"函数的最小值为 ${3*r*r}$",
            "D": "函数在定义域上存在最大值",
        }
        answer = "ABC"
        solution = (
            rf"$f'(x)=2x-\frac{{{2*r**3}}}{{x^2}}=\frac{{2(x^3-{r**3})}}{{x^2}}$。"
            rf"故函数先减后增，在 $x={r}$ 处取最小值 ${3*r*r}$，且向定义域两端均趋于 $+\infty$。故选 ABC。"
        )
    elif family == 6:
        formula, domain = rf"f(x)=(x-{r})^2", r"\mathbb{R}"
        options = {
            "A": rf"$f(x)$ 在 $(-\infty,{r})$ 上单调递减",
            "B": rf"$f(x)$ 在 $({r},+\infty)$ 上单调递增",
            "C": "函数的最小值为 $0$",
            "D": "函数的最大值为 $0$",
        }
        answer = "ABC"
        solution = (
            rf"$f'(x)=2(x-{r})$。当 $x<{r}$ 时导数为负，当 $x>{r}$ 时导数为正，"
            rf"故函数先减后增，在 $x={r}$ 处取得最小值 $0$；向两端均趋于 $+\infty$，无最大值。故选 ABC。"
        )
    elif family == 7:
        formula, domain = r"f(x)=x^2e^{-x}", r"(0,+\infty)"
        options = {
            "A": "$x=2$ 是唯一极大值点",
            "B": "$f(x)$ 在 $(0,2)$ 上单调递增",
            "C": r"函数的最大值为 $\frac4{e^2}$",
            "D": "函数的最小值为 $0$",
        }
        answer = "ABC"
        solution = (
            r"$f'(x)=e^{-x}x(2-x)$。在定义域内，$0<x<2$ 时导数为正，$x>2$ 时导数为负，"
            r"故 $x=2$ 是唯一极大值点，最大值为 $4/e^2$；$x\to0^+$ 或 $+\infty$ 时只趋于 $0$，无最小值。故选 ABC。"
        )
    elif family == 8:
        formula, domain = r"f(x)=x-\ln x", r"(0,+\infty)"
        options = {
            "A": "$f(x)$ 在 $(0,1)$ 上单调递减",
            "B": r"$f(x)$ 在 $(1,+\infty)$ 上单调递增",
            "C": "函数的最小值为 $1$",
            "D": "$x=1$ 是极大值点",
        }
        answer = "ABC"
        solution = (
            r"$f'(x)=1-1/x=(x-1)/x$。故函数在 $(0,1)$ 上递减、在 $(1,+\infty)$ 上递增，"
            r"并在 $x=1$ 处取得最小值 $1$。故选 ABC。"
        )
    elif family == 9:
        k = 2 + variant % 6
        formula, domain = rf"f(x)=e^x-{k}x", r"\mathbb{R}"
        options = {
            "A": rf"$f(x)$ 在 $(-\infty,\ln {k})$ 上单调递减",
            "B": rf"$f(x)$ 在 $(\ln {k},+\infty)$ 上单调递增",
            "C": rf"函数的最小值为 ${k}-{k}\ln {k}$",
            "D": "函数在定义域上存在最大值",
        }
        answer = "ABC"
        solution = (
            rf"$f'(x)=e^x-{k}$，故在 $x=\ln {k}$ 左侧为负、右侧为正。"
            rf"函数在此处取得最小值 ${k}-{k}\ln {k}$，且无最大值。故选 ABC。"
        )
    elif family == 10:
        formula, domain = rf"f(x)=x^4-{2*r*r}x^2", r"\mathbb{R}"
        options = {
            "A": rf"$x=\pm {r}$ 均为极小值点",
            "B": "$x=0$ 是极大值点",
            "C": rf"函数的最小值为 $-{r**4}$",
            "D": "函数的最大值为 $0$",
        }
        answer = "ABC"
        solution = (
            rf"$f'(x)=4x(x-{r})(x+{r})$，导数符号依次为 $- + - +$。"
            rf"故 $\pm {r}$ 为极小值点且最小值为 $-{r**4}$，$0$ 为极大值点；函数无最大值。故选 ABC。"
        )
    elif family == 11:
        formula, domain = rf"f(x)=x^2-{2*r*r}\ln x", r"(0,+\infty)"
        options = {
            "A": rf"$f(x)$ 在 $(0,{r})$ 上单调递减",
            "B": rf"$f(x)$ 在 $({r},+\infty)$ 上单调递增",
            "C": rf"$x={r}$ 是唯一极小值点",
            "D": "函数在定义域上存在最大值",
        }
        answer = "ABC"
        solution = (
            rf"$f'(x)=2x-\frac{{{2*r*r}}}x=\frac{{2(x-{r})(x+{r})}}x$。"
            rf"故函数在 $x={r}$ 左减右增，$x={r}$ 为唯一极小值点；定义域两端函数均趋于 $+\infty$，无最大值。故选 ABC。"
        )
    elif family == 12:
        formula, domain = r"f(x)=\frac{\ln x}{x}", r"(0,+\infty)"
        options = {
            "A": "$f(x)$ 在 $(0,e)$ 上单调递增",
            "B": r"$f(x)$ 在 $(e,+\infty)$ 上单调递减",
            "C": r"函数的最大值为 $\frac1e$",
            "D": "函数的最小值为 $0$",
        }
        answer = "ABC"
        solution = (
            r"$f'(x)=(1-\ln x)/x^2$，故函数在 $(0,e)$ 上递增、在 $(e,+\infty)$ 上递减，"
            r"最大值为 $f(e)=1/e$；当 $x\to0^+$ 时函数趋于 $-\infty$，无最小值。故选 ABC。"
        )
    elif family == 13:
        power = 2 + variant % 3
        formula, domain = rf"f(x)=x^{power}e^{{-x}}", r"(0,+\infty)"
        options = {
            "A": rf"$f(x)$ 在 $(0,{power})$ 上单调递增",
            "B": rf"$f(x)$ 在 $({power},+\infty)$ 上单调递减",
            "C": rf"函数的最大值为 $\frac{{{power**power}}}{{e^{power}}}$",
            "D": "函数的最小值为 $0$",
        }
        answer = "ABC"
        solution = (
            rf"$f'(x)=x^{{{power-1}}}e^{{-x}}({power}-x)$，故函数先增后减，"
            rf"在 $x={power}$ 处取得最大值 ${power**power}/e^{power}$。两端只趋于 $0$ 而不取到，故无最小值。故选 ABC。"
        )
    elif family == 14:
        formula, domain = r"f(x)=x^2e^x", r"\mathbb{R}"
        options = {
            "A": "$x=-2$ 是极大值点",
            "B": "$x=0$ 是极小值点",
            "C": "函数的最小值为 $0$",
            "D": r"函数的最大值为 $\frac4{e^2}$",
        }
        answer = "ABC"
        solution = (
            r"$f'(x)=e^x x(x+2)$，故导数在 $-2$ 处由正变负、在 $0$ 处由负变正。"
            r"又 $f(x)\geq0$，最小值为 $0$；当 $x\to+\infty$ 时函数趋于 $+\infty$，无最大值。故选 ABC。"
        )
    elif family == 15:
        formula, domain = r"f(x)=(x-1)^2+2", r"\mathbb{R}"
        options = {"A": "$x=1$ 是唯一极小值点", "B": "函数的最小值为 $2$", "C": r"函数在 $(1,+\infty)$ 上单调递减", "D": "函数存在最大值"}
        answer = "AB"
        solution = r"$f'(x)=2(x-1)$，函数在 $x=1$ 左减右增，最小值为 $2$，且无最大值。故选 AB。"
    elif family == 16:
        formula, domain = r"f(x)=-(x+2)^2+3", r"\mathbb{R}"
        options = {"A": "$x=-2$ 是唯一极大值点", "B": "函数的最大值为 $3$", "C": r"函数在 $(-\infty,-2)$ 上单调递增", "D": "函数存在最小值"}
        answer = "ABC"
        solution = r"$f'(x)=-2(x+2)$，函数在 $x=-2$ 左增右减，最大值为 $3$，且向两端趋于 $-\infty$。故选 ABC。"
    elif family == 17:
        formula, domain = r"f(x)=x^2+1", r"[-1,2]"
        options = {"A": "$x=0$ 是区间内唯一极小值点", "B": "函数的最小值为 $1$", "C": "函数的最大值为 $5$", "D": "$x=-1$ 是最大值点"}
        answer = "ABC"
        solution = r"$f'(x)=2x$，函数在 $[-1,0]$ 递减、$[0,2]$ 递增；比较端点得最小值 $1$、最大值 $5$。故选 ABC。"
    elif family == 18:
        formula, domain = r"f(x)=x+\frac1x", r"(0,+\infty)"
        options = {"A": "函数在 $(0,1)$ 上单调递减", "B": r"函数在 $(1,+\infty)$ 上单调递增", "C": "函数的最小值为 $2$", "D": "函数存在最大值"}
        answer = "ABC"
        solution = r"$f'(x)=1-\frac1{x^2}$，函数在 $x=1$ 左减右增，最小值为 $2$，且无最大值。故选 ABC。"
    else:
        formula, domain = r"f(x)=\ln x-x", r"(0,+\infty)"
        options = {"A": "函数在 $(0,1)$ 上单调递增", "B": r"函数在 $(1,+\infty)$ 上单调递减", "C": "函数的最大值为 $-1$", "D": "函数存在最小值"}
        answer = "ABC"
        solution = r"$f'(x)=\frac1x-1$，函数在 $x=1$ 左增右减，最大值为 $-1$；定义域两端均趋于 $-\infty$，无最小值。故选 ABC。"

    option_text = "\n\n".join(f"{key}. {value}" for key, value in options.items())
    stem_patterns = (
        f"已知函数 ${formula}$ 的定义域为 ${domain}$，则下列结论正确的是（ ）",
        f"研究定义在 ${domain}$ 上的函数 ${formula}$ 的单调性与极值，下列说法正确的是（ ）",
        f"对函数 ${formula}$（$x\\in {domain}$），下列关于其变化趋势与最值的判断正确的是（ ）",
    )
    dimension_pairs = (
        ["condition_organization", "reasoning_path"],
        ["question_angle", "representation"],
        ["representation", "reasoning_path"],
        ["context", "condition_organization"],
        ["question_angle", "reasoning_path"],
    )
    slot_index = int(request.get("slot_index") or 0)
    return {
        "question_type": "multiple_choice",
        "stem_markdown": f"{stem_patterns[slot_index % len(stem_patterns)]}\n\n{option_text}",
        "options": options,
        "answer": answer,
        "solution_markdown": solution,
        "changed_dimensions": dimension_pairs[slot_index % len(dimension_pairs)],
        "family_id": f"extremum-{family}",
        "contract": "题干、函数、定义域、选项和答案必须原样保留",
    }


def _exact_family_index(request: dict[str, Any], prefix: str) -> int:
    family_override = request.get("_family_override")
    if family_override is not None:
        return int(family_override) % SAFETY_FAMILY_COUNTS[prefix]
    seed = (
        int(request.get("slot_index") or 0)
        + max(1, int(request.get("attempt") or 1))
        - 1
    )
    batch_seed = int(
        hashlib.sha256(str(request.get("batch_id") or "").encode("utf-8")).hexdigest()[:8],
        16,
    )
    return (seed + batch_seed) % SAFETY_FAMILY_COUNTS[prefix]


def _exact_choice_blueprint(
    request: dict[str, Any],
    prefix: str,
    rows: list[tuple[str, str, list[str], str]],
) -> dict[str, Any]:
    family = _exact_family_index(request, prefix)
    stem, correct, distractors, reasoning = rows[family]
    correct_position = family % 4
    values = list(distractors[:3])
    values.insert(correct_position, correct)
    options = {label: values[index] for index, label in enumerate("ABCD")}
    answer = "ABCD"[correct_position]
    option_text = "\n".join(f"{label}. {value}" for label, value in options.items())
    dimension_pairs = (
        ["question_angle", "reasoning_path"],
        ["representation", "condition_organization"],
        ["context", "reasoning_path"],
        ["question_angle", "representation"],
        ["context", "condition_organization"],
    )
    return {
        "question_type": "single_choice",
        "stem_markdown": f"{stem}\n\n{option_text}",
        "options": options,
        "answer": answer,
        "solution_markdown": f"{reasoning}故选 {answer}。",
        "changed_dimensions": dimension_pairs[family % len(dimension_pairs)],
        "family_id": f"{prefix}-{family}",
        "contract": "题干、选项、答案及精确推导必须原样保留",
    }


def _derivative_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if "基本函数求导" not in request["knowledge"] or question_type != "single_choice":
        return None
    rows = [
        ("函数 $f(x)=x^4-3x^2+2x-5$，则 $f'(x)=$（　）。", "$4x^3-6x+2$", ["$4x^3-3x+2$", "$x^3-6x+2$", "$4x^3-6x-5$"], "逐项求导，常数项导数为零，得到 $f'(x)=4x^3-6x+2$。"),
        ("设 $x>0$，函数 $f(x)=e^{2x}+\\ln x$，则 $f'(x)=$（　）。", "$2e^{2x}+\\frac1x$", ["$e^{2x}+\\frac1x$", "$2e^x+\\frac1x$", "$2e^{2x}+\\ln x$"], "由复合函数与对数函数求导公式，$f'(x)=2e^{2x}+\\frac1x$。"),
        ("函数 $f(x)=xe^x$，则 $f'(x)=$（　）。", "$(x+1)e^x$", ["$xe^x$", "$(x-1)e^x$", "$e^x$"], "使用乘积求导法则，$f'(x)=e^x+xe^x=(x+1)e^x$。"),
        ("函数 $f(x)=\\ln(1+x^2)$，则 $f'(x)=$（　）。", "$\\frac{2x}{1+x^2}$", ["$\\frac1{1+x^2}$", "$\\frac{x}{1+x^2}$", "$2x\\ln(1+x^2)$"], "令内函数为 $1+x^2$，按链式法则得 $f'(x)=\\frac{2x}{1+x^2}$。"),
        ("设 $x>0$，函数 $f(x)=\\sqrt{x}+\\frac1x$，则 $f'(x)=$（　）。", "$\\frac1{2\\sqrt{x}}-\\frac1{x^2}$", ["$\\frac1{\\sqrt{x}}+\\frac1{x^2}$", "$\\frac1{2\\sqrt{x}}+\\frac1{x^2}$", "$2\\sqrt{x}-\\frac1x$"], "把根式与倒数写成幂函数，逐项求导得 $f'(x)=\\frac1{2\\sqrt{x}}-\\frac1{x^2}$。"),
        ("函数 $f(x)=\\sin 2x+\\cos x$，则 $f'(x)=$（　）。", "$2\\cos 2x-\\sin x$", ["$\\cos 2x-\\sin x$", "$2\\cos x-\\sin x$", "$2\\sin 2x+\\cos x$"], "由链式法则，$(\\sin2x)'=2\\cos2x$，且 $(\\cos x)'=-\\sin x$。"),
        ("设 $x\\ne-1$，函数 $f(x)=\\frac{x^2+1}{x+1}$，则 $f'(x)=$（　）。", "$\\frac{x^2+2x-1}{(x+1)^2}$", ["$\\frac{2x}{x+1}$", "$\\frac{x^2+2x+1}{(x+1)^2}$", "$\\frac{x^2-1}{(x+1)^2}$"], "使用商的求导法则，分子化简为 $2x(x+1)-(x^2+1)=x^2+2x-1$。"),
        ("设 $x\\ne0$，函数 $f(x)=\\frac{e^x}{x}$，则 $f'(x)=$（　）。", "$\\frac{e^x(x-1)}{x^2}$", ["$\\frac{e^x}{x^2}$", "$\\frac{e^x(x+1)}{x^2}$", "$\\frac{e^x-1}{x}$"], "按商法则，$f'(x)=\\frac{xe^x-e^x}{x^2}=\\frac{e^x(x-1)}{x^2}$。"),
        ("函数 $f(x)=(2x-1)^4$，则 $f'(x)=$（　）。", "$8(2x-1)^3$", ["$4(2x-1)^3$", "$8(2x-1)^4$", "$4(2x-1)^5$"], "外层幂函数导数乘以内层导数 $2$，故 $f'(x)=8(2x-1)^3$。"),
        ("设 $x>0$，函数 $f(x)=x^2\\ln x$，则 $f'(x)=$（　）。", "$2x\\ln x+x$", ["$2x\\ln x$", "$x\\ln x+x$", "$2x+x^{-1}$"], "由乘积法则，$f'(x)=2x\\ln x+x^2\\cdot\\frac1x=2x\\ln x+x$。"),
    ]
    return _exact_choice_blueprint(request, "derivative", rows)


def _total_probability_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if "全概率公式" not in request["knowledge"] or question_type != "single_choice":
        return None
    rows = [
        ("某零件由甲、乙两厂供应，供应比例分别为 $\\frac25,\\frac35$，次品率分别为 $\\frac1{10},\\frac1{20}$。任取一件为次品的概率是（　）。", "$\\frac7{100}$", ["$\\frac1{20}$", "$\\frac3{40}$", "$\\frac1{10}$"], "按供应来源分支，$P=\\frac25\\cdot\\frac1{10}+\\frac35\\cdot\\frac1{20}=\\frac7{100}$。"),
        ("先以概率 $\\frac13$ 选择盒甲、以概率 $\\frac23$ 选择盒乙；两盒取到红球的概率分别为 $\\frac12,\\frac14$。最终取到红球的概率为（　）。", "$\\frac13$", ["$\\frac14$", "$\\frac5{12}$", "$\\frac12$"], "由全概率公式，$P=\\frac13\\cdot\\frac12+\\frac23\\cdot\\frac14=\\frac13$。"),
        ("某人以概率 $\\frac34$ 乘公交、以概率 $\\frac14$ 乘地铁，迟到概率分别为 $\\frac15,\\frac1{10}$。他迟到的概率为（　）。", "$\\frac7{40}$", ["$\\frac18$", "$\\frac15$", "$\\frac3{20}$"], "按交通方式分组，$P=\\frac34\\cdot\\frac15+\\frac14\\cdot\\frac1{10}=\\frac7{40}$。"),
        ("某地降雨概率为 $\\frac3{10}$。降雨时航班延误概率为 $\\frac45$，不降雨时为 $\\frac1{10}$。航班延误概率为（　）。", "$\\frac{31}{100}$", ["$\\frac{27}{100}$", "$\\frac3{10}$", "$\\frac{17}{50}$"], "分降雨与不降雨两种情形，$P=\\frac3{10}\\cdot\\frac45+\\frac7{10}\\cdot\\frac1{10}=\\frac{31}{100}$。"),
        ("产品来自三条生产线的比例为 $\\frac12,\\frac13,\\frac16$，合格率依次为 $\\frac45,\\frac34,\\frac12$。任取一件合格的概率为（　）。", "$\\frac{11}{15}$", ["$\\frac7{10}$", "$\\frac34$", "$\\frac56$"], "三条互斥来源构成完备分组，$P=\\frac12\\cdot\\frac45+\\frac13\\cdot\\frac34+\\frac16\\cdot\\frac12=\\frac{11}{15}$。"),
        ("学生步行、骑车、乘车到校的概率为 $\\frac14,\\frac14,\\frac12$，对应迟到概率为 $0,\\frac1{10},\\frac15$。随机一天迟到的概率为（　）。", "$\\frac18$", ["$\\frac3{20}$", "$\\frac1{10}$", "$\\frac7{40}$"], "按三种到校方式求和，$P=\\frac14\\cdot0+\\frac14\\cdot\\frac1{10}+\\frac12\\cdot\\frac15=\\frac18$。"),
        ("邮件来自服务器甲、乙的概率为 $\\frac23,\\frac13$，被系统正确识别的概率分别为 $\\frac9{10},\\frac35$。识别正确的概率为（　）。", "$\\frac45$", ["$\\frac34$", "$\\frac7{10}$", "$\\frac56$"], "按服务器来源分组，$P=\\frac23\\cdot\\frac9{10}+\\frac13\\cdot\\frac35=\\frac45$。"),
        ("种子来自供货商甲、乙的比例为 $\\frac35,\\frac25$，发芽率分别为 $\\frac45,\\frac34$。随机一粒种子发芽的概率为（　）。", "$\\frac{39}{50}$", ["$\\frac{19}{25}$", "$\\frac45$", "$\\frac{31}{40}$"], "由全概率公式，$P=\\frac35\\cdot\\frac45+\\frac25\\cdot\\frac34=\\frac{39}{50}$。"),
        ("顾客使用银行卡、移动支付、现金的概率为 $\\frac12,\\frac25,\\frac1{10}$，发生退款的概率分别为 $\\frac1{20},\\frac1{40},0$。发生退款的概率为（　）。", "$\\frac7{200}$", ["$\\frac1{25}$", "$\\frac3{100}$", "$\\frac1{20}$"], "按支付方式分组，$P=\\frac12\\cdot\\frac1{20}+\\frac25\\cdot\\frac1{40}+\\frac1{10}\\cdot0=\\frac7{200}$。"),
        ("信号经通道 I、II 传输的概率为 $\\frac3{10},\\frac7{10}$，成功率分别为 $\\frac23,\\frac47$。传输成功的概率为（　）。", "$\\frac35$", ["$\\frac12$", "$\\frac{17}{30}$", "$\\frac23$"], "按通道分组，$P=\\frac3{10}\\cdot\\frac23+\\frac7{10}\\cdot\\frac47=\\frac35$。"),
    ]
    return _exact_choice_blueprint(request, "total_probability", rows)


def _tangent_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if "求切线" not in request["knowledge"] or question_type != "single_choice":
        return None
    rows = [
        ("曲线 $y=x^2+1$ 在点 $(1,2)$ 处的切线方程为（　）。", "$y=2x$", ["$y=x+1$", "$y=2x+1$", "$y=2x-1$"], "导数 $y'=2x$，在 $x=1$ 处斜率为 $2$，故 $y-2=2(x-1)$。"),
        ("曲线 $y=e^x$ 在横坐标为 $0$ 的点处的切线方程为（　）。", "$y=x+1$", ["$y=x$", "$y=e x$", "$y=2x+1$"], "$y'=e^x$，在 $x=0$ 处点为 $(0,1)$、斜率为 $1$，故切线为 $y=x+1$。"),
        ("曲线 $y=\\ln x$ 在点 $(1,0)$ 处的切线方程为（　）。", "$y=x-1$", ["$y=x+1$", "$y=-x+1$", "$y=\\ln x-1$"], "$y'=\\frac1x$，在 $x=1$ 处斜率为 $1$，故 $y=x-1$。"),
        ("曲线 $y=\\sqrt{x}$ 在点 $(4,2)$ 处的切线方程为（　）。", "$y=\\frac14x+1$", ["$y=\\frac12x$", "$y=\\frac14x+2$", "$y=4x-14$"], "$y'=\\frac1{2\\sqrt{x}}$，在 $x=4$ 处斜率为 $\\frac14$，代入点斜式得结论。"),
        ("曲线 $y=\\frac1x$ 在点 $(1,1)$ 处的切线方程为（　）。", "$y=-x+2$", ["$y=x$", "$y=-x+1$", "$y=x+2$"], "$y'=-\\frac1{x^2}$，在 $x=1$ 处斜率为 $-1$，故 $y-1=-(x-1)$。"),
        ("曲线 $y=\\sin x$ 在原点处的切线方程为（　）。", "$y=x$", ["$y=-x$", "$y=1$", "$y=x+1$"], "$y'=\\cos x$，$y'(0)=1$ 且曲线过原点，所以切线为 $y=x$。"),
        ("曲线 $y=xe^x$ 在横坐标为 $0$ 的点处的切线方程为（　）。", "$y=x$", ["$y=e^x$", "$y=x+1$", "$y=-x$"], "$y'=(x+1)e^x$，在 $x=0$ 处点为 $(0,0)$、斜率为 $1$。"),
        ("曲线 $y=x^3-3x$ 在点 $(1,-2)$ 处的切线方程为（　）。", "$y=-2$", ["$y=3x-5$", "$y=-3x+1$", "$y=x-3$"], "$y'=3x^2-3$，$y'(1)=0$，因此切线为水平直线 $y=-2$。"),
        ("曲线 $y=e^{2x}$ 在横坐标为 $0$ 的点处的切线方程为（　）。", "$y=2x+1$", ["$y=x+1$", "$y=2x$", "$y=e^2x+1$"], "$y'=2e^{2x}$，在 $x=0$ 处斜率为 $2$，且切点为 $(0,1)$。"),
        ("曲线 $y=\\ln(1+x)$ 在原点处的切线方程为（　）。", "$y=x$", ["$y=x+1$", "$y=-x$", "$y=\\frac12x$"], "$y'=\\frac1{1+x}$，在 $x=0$ 处斜率为 $1$，故切线为 $y=x$。"),
    ]
    return _exact_choice_blueprint(request, "tangent", rows)


def _parity_periodicity_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if "奇偶性与周期性" not in request["knowledge"] or question_type != "single_choice":
        return None
    rows = [
        ("函数 $f(x)=x^3+x$ 的奇偶性为（　）。", "奇函数", ["偶函数", "既奇又偶", "非奇非偶"], "$f(-x)=-x^3-x=-f(x)$，定义域关于原点对称，所以为奇函数。"),
        ("函数 $f(x)=x^2+\\cos x$ 的奇偶性为（　）。", "偶函数", ["奇函数", "既奇又偶", "非奇非偶"], "$x^2$ 与 $\\cos x$ 均为偶函数，和仍为偶函数。"),
        ("函数 $f(x)=\\sin 2x$ 的最小正周期为（　）。", "$\\pi$", ["$2\\pi$", "$\\frac\\pi2$", "$4\\pi$"], "$\\sin(kx)$ 的最小正周期为 $\\frac{2\\pi}{|k|}$，故此处为 $\\pi$。"),
        ("函数 $f(x)=|\\sin x|$ 的最小正周期为（　）。", "$\\pi$", ["$2\\pi$", "$\\frac\\pi2$", "$4\\pi$"], "绝对值使正负半波重合，$|\\sin(x+\\pi)|=|\\sin x|$，最小正周期为 $\\pi$。"),
        ("函数 $f(x)=x\\sin x$ 的奇偶性为（　）。", "偶函数", ["奇函数", "既奇又偶", "非奇非偶"], "$x$ 与 $\\sin x$ 都是奇函数，两者乘积为偶函数。"),
        ("函数 $f(x)=x^2\\sin x$ 的奇偶性为（　）。", "奇函数", ["偶函数", "既奇又偶", "非奇非偶"], "$x^2$ 为偶函数、$\\sin x$ 为奇函数，乘积为奇函数。"),
        ("设函数 $f(x)$ 的定义域关于原点对称，$g(x)=f(x)+f(-x)$。则 $g(x)$ 一定是（　）。", "偶函数", ["奇函数", "单调函数", "周期函数"], "$g(-x)=f(-x)+f(x)=g(x)$，所以 $g$ 一定为偶函数。"),
        ("设函数 $f(x)$ 的定义域关于原点对称，$h(x)=f(x)-f(-x)$。则 $h(x)$ 一定是（　）。", "奇函数", ["偶函数", "常函数", "周期函数"], "$h(-x)=f(-x)-f(x)=-h(x)$，所以 $h$ 一定为奇函数。"),
        ("函数 $f(x)=\\cos(3x-1)$ 的最小正周期为（　）。", "$\\frac{2\\pi}{3}$", ["$2\\pi$", "$\\frac\\pi3$", "$3\\pi$"], "相位平移不改变周期，$\\cos(3x-1)$ 的最小正周期为 $\\frac{2\\pi}{3}$。"),
        ("函数 $f(x)=\\sin x\\cos x$ 的最小正周期为（　）。", "$\\pi$", ["$2\\pi$", "$\\frac\\pi2$", "$4\\pi$"], "$\\sin x\\cos x=\\frac12\\sin2x$，故最小正周期为 $\\pi$。"),
    ]
    return _exact_choice_blueprint(request, "parity_periodicity", rows)


def _derivative_definition_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if "平均变化率与瞬时变化率" not in request["knowledge"] or question_type != "single_choice":
        return None
    rows = [
        ("函数 $f(x)=x^2$ 在区间 $[1,3]$ 上的平均变化率为（　）。", "$4$", ["$2$", "$6$", "$8$"], "平均变化率为 $\\frac{f(3)-f(1)}{3-1}=\\frac{9-1}{2}=4$。"),
        ("函数 $f(x)=x^3$ 在 $x=2$ 处的瞬时变化率为（　）。", "$12$", ["$6$", "$8$", "$4$"], "$f'(x)=3x^2$，所以 $f'(2)=12$。"),
        ("若函数 $f$ 在 $x=2$ 处可导，则 $\\lim_{h\\to0}\\frac{f(2+h)-f(2)}h=$（　）。", "$f'(2)$", ["$f(2)$", "$2f'(2)$", "$f'(0)$"], "该极限正是函数在 $x=2$ 处导数的定义。"),
        ("质点位移 $s(t)=t^2+2t$，则 $t=3$ 时的瞬时速度为（　）。", "$8$", ["$6$", "$11$", "$5$"], "瞬时速度 $v(t)=s'(t)=2t+2$，故 $v(3)=8$。"),
        ("函数 $f(x)=\\frac1x$ 在区间 $[1,2]$ 上的平均变化率为（　）。", "$-\\frac12$", ["$\\frac12$", "$-1$", "$\\frac14$"], "平均变化率为 $\\frac{f(2)-f(1)}{2-1}=\\frac12-1=-\\frac12$。"),
        ("若 $f$ 在 $x=1$ 处可导，则 $\\lim_{h\\to0}\\frac{f(1+2h)-f(1)}h=$（　）。", "$2f'(1)$", ["$f'(1)$", "$f'(2)$", "$\\frac12f'(1)$"], "令 $\\Delta x=2h$，原极限为 $2\\lim_{\\Delta x\\to0}\\frac{f(1+\\Delta x)-f(1)}{\\Delta x}=2f'(1)$。"),
        ("函数 $f(x)=\\sqrt{x}$ 在 $x=4$ 处的瞬时变化率为（　）。", "$\\frac14$", ["$\\frac12$", "$2$", "$4$"], "$f'(x)=\\frac1{2\\sqrt{x}}$，所以 $f'(4)=\\frac14$。"),
        ("函数 $f(x)=x^3$ 在区间 $[-1,1]$ 上的平均变化率为（　）。", "$1$", ["$0$", "$2$", "$3$"], "平均变化率为 $\\frac{1-(-1)}{1-(-1)}=1$。"),
        ("已知 $\\lim_{h\\to0}\\frac{f(3+h)-f(3)}h=5$，则 $f'(3)=$（　）。", "$5$", ["$3$", "$0$", "$15$"], "由导数定义，所给极限就是 $f'(3)$。"),
        ("若 $f$ 在 $x=a$ 处可导，则 $\\lim_{h\\to0}\\frac{f(a+h)-f(a-h)}{2h}=$（　）。", "$f'(a)$", ["$2f'(a)$", "$f(a)$", "$0$"], "把分子拆成两个以 $f(a)$ 为基准的增量，两部分极限各贡献 $\\frac12f'(a)$。"),
    ]
    return _exact_choice_blueprint(request, "derivative_definition", rows)


def _nonchoice_exact_blueprint(
    request: dict[str, Any],
    prefix: str,
    question_type: str,
    rows: list[tuple[str, str, str]],
) -> dict[str, Any]:
    family = _exact_family_index(request, prefix)
    stem, answer, solution = rows[family]
    dimension_pairs = (
        ["question_angle", "reasoning_path"],
        ["representation", "condition_organization"],
        ["context", "reasoning_path"],
        ["question_angle", "representation"],
        ["context", "condition_organization"],
    )
    return {
        "question_type": question_type,
        "stem_markdown": stem,
        "options": {},
        "answer": answer,
        "solution_markdown": solution,
        "changed_dimensions": dimension_pairs[family % len(dimension_pairs)],
        "family_id": f"{prefix}-{family}",
        "contract": "题干、答案和精确推导必须原样保留",
    }


def _extremum_other_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    """Exact extrema/minimum scaffolds for the three non-multiple-choice forms."""
    knowledge = str(request.get("knowledge") or "")
    if not ("极值" in knowledge or "最值" in knowledge):
        return None
    if question_type == "single_choice":
        rows = [
            (r"研究函数 $f(x)=x^3-3x$ 在区间 $\mathbb R$ 上的极值，下列结论正确的是（　）。", r"$x=-1$ 是极大值点", [r"$x=1$ 是极大值点", r"$x=0$ 是极值点", "函数没有极值点"], r"$f'(x)=3(x-1)(x+1)$，导数在 $x=-1$ 处由正变负，故 $x=-1$ 是极大值点。"),
            (r"函数 $f(x)=x+\frac4x$ 的定义域为 $(0,+\infty)$，下列结论正确的是（　）。", r"函数的最小值为 $4$", [r"函数的最大值为 $4$", r"$x=4$ 是极小值点", r"函数在定义域上单调递增"], r"$f'(x)=1-\frac4{x^2}$，函数在 $x=2$ 左减右增，故最小值为 $f(2)=4$。"),
            (r"函数 $f(x)=\ln x-x$ 的定义域为 $(0,+\infty)$，下列结论正确的是（　）。", r"函数的最大值为 $-1$", [r"函数的最小值为 $-1$", r"函数的最大值为 $0$", r"函数没有极值点"], r"$f'(x)=\frac1x-1$，函数在 $x=1$ 左增右减，故最大值为 $f(1)=-1$。"),
            (r"函数 $f(x)=xe^{-x}$ 的定义域为 $(0,+\infty)$，下列结论正确的是（　）。", r"函数的最大值为 $\frac1e$", [r"函数的最小值为 $\frac1e$", r"$x=e$ 是极大值点", r"函数在定义域上单调递增"], r"$f'(x)=e^{-x}(1-x)$，导数在 $x=1$ 处由正变负，故最大值为 $1/e$。"),
            (r"函数 $f(x)=x^4-2x^2$ 在区间 $[-2,2]$ 上的最值判断中，正确的是（　）。", r"函数的最小值为 $-1$", [r"函数的最小值为 $0$", r"函数的最大值为 $0$", r"$x=0$ 是极小值点"], r"$f'(x)=4x(x-1)(x+1)$；比较驻点与端点函数值，最小值为 $f(\pm1)=-1$。"),
            (r"函数 $f(x)=x-\ln x$ 的定义域为 $(0,+\infty)$，下列结论正确的是（　）。", r"函数的最小值为 $1$", [r"函数的最大值为 $1$", r"$x=e$ 是极小值点", r"函数没有最小值"], r"$f'(x)=1-\frac1x$，函数在 $x=1$ 左减右增，故最小值为 $f(1)=1$。"),
            (r"函数 $f(x)=e^x-3x$ 在区间 $\mathbb R$ 上的最值判断中，正确的是（　）。", r"最小值为 $3-3\ln3$", [r"最大值为 $3-3\ln3$", r"最小值为 $-3\ln3$", r"函数没有最小值"], r"$f'(x)=e^x-3$，唯一驻点为 $x=\ln3$ 且导数左负右正，故最小值为 $3-3\ln3$。"),
            (r"函数 $f(x)=x^2+\frac{16}{x}$ 的定义域为 $(0,+\infty)$，下列结论正确的是（　）。", r"函数的最小值为 $12$", [r"函数的最小值为 $8$", r"$x=4$ 是极小值点", r"函数存在最大值"], r"$f'(x)=2x-\frac{16}{x^2}=\frac{2(x^3-8)}{x^2}$，故在 $x=2$ 处取最小值 $12$。"),
            (r"函数 $f(x)=\frac{\ln x}{x}$ 的定义域为 $(0,+\infty)$，下列结论正确的是（　）。", r"函数的最大值为 $\frac1e$", [r"函数的最小值为 $\frac1e$", r"$x=1$ 是极大值点", r"函数没有最大值"], r"$f'(x)=\frac{1-\ln x}{x^2}$，函数在 $x=e$ 左增右减，故最大值为 $1/e$。"),
            (r"函数 $f(x)=(x^2-4)^2$ 在区间 $\mathbb R$ 上的极值判断中，正确的是（　）。", r"$x=0$ 是极大值点", [r"$x=0$ 是极小值点", r"函数的最小值为 $16$", r"函数存在最大值"], r"$f'(x)=4x(x-2)(x+2)$，导数在 $x=0$ 处由正变负，故 $x=0$ 是极大值点。"),
        ]
        return _exact_choice_blueprint(request, "extremum_single", rows)
    if question_type == "fill_blank" and "求最值" not in knowledge:
        rows = [
            (r"函数 $f(x)=x^3-3x$ 在区间 $[-2,2]$ 上的极大值为____。", "$2$", r"$f'(x)=3(x-1)(x+1)$，比较驻点与端点函数值，极大值为 $f(-1)=2$。"),
            (r"函数 $f(x)=x+\frac4x$ 在区间 $(0,+\infty)$ 上的最小值为____。", "$4$", r"$f'(x)=1-\frac4{x^2}$，函数在 $x=2$ 左减右增，故最小值为 $4$。"),
            (r"函数 $f(x)=\ln x-x$ 在区间 $(0,+\infty)$ 上的最大值为____。", "$-1$", r"$f'(x)=\frac1x-1$，函数在 $x=1$ 左增右减，故最大值为 $-1$。"),
            (r"函数 $f(x)=xe^{-x}$ 在区间 $(0,+\infty)$ 上的最大值为____。", r"$\frac1e$", r"$f'(x)=e^{-x}(1-x)$，函数在 $x=1$ 左增右减号为正、负，故最大值为 $1/e$。"),
            (r"函数 $f(x)=x^4-2x^2$ 在区间 $[-2,2]$ 上的最小值为____。", "$-1$", r"$f'(x)=4x(x-1)(x+1)$，比较驻点与端点函数值，最小值为 $f(\pm1)=-1$。"),
            (r"函数 $f(x)=x-\ln x$ 在区间 $(0,+\infty)$ 上的最小值为____。", "$1$", r"$f'(x)=1-\frac1x$，函数在 $x=1$ 左减右增，故最小值为 $1$。"),
            (r"函数 $f(x)=e^x-2x$ 在区间 $\mathbb R$ 上的最小值为____。", r"$2-2\ln2$", r"$f'(x)=e^x-2$，在 $x=\ln2$ 处由负变正，故最小值为 $2-2\ln2$。"),
            (r"函数 $f(x)=x^2+\frac{16}{x}$ 在区间 $(0,+\infty)$ 上的最小值为____。", "$12$", r"$f'(x)=\frac{2(x^3-8)}{x^2}$，函数在 $x=2$ 左减右增，故最小值为 $12$。"),
            (r"函数 $f(x)=\frac{\ln x}{x}$ 在区间 $(0,+\infty)$ 上的最大值为____。", r"$\frac1e$", r"$f'(x)=\frac{1-\ln x}{x^2}$，函数在 $x=e$ 左增右减，故最大值为 $1/e$。"),
            (r"函数 $f(x)=(x^2-4)^2$ 在区间 $\mathbb R$ 上的极小值为____。", "$0$", r"函数值恒非负，且在 $x=\pm2$ 处取到 $0$，故极小值为 $0$。"),
        ]
        return _nonchoice_exact_blueprint(request, "extremum_fill", "fill_blank", rows)
    if question_type == "solution":
        rows = [
            (r"求函数 $f(x)=x^3-3x$ 在区间 $[-2,2]$ 上的极值。", r"极大值 $2$，极小值 $-2$", r"$f'(x)=3(x-1)(x+1)$。导数在 $x=-1$ 处由正变负，在 $x=1$ 处由负变正，故极大值为 $f(-1)=2$，极小值为 $f(1)=-2$。"),
            (r"求函数 $f(x)=x+\frac4x$ 在区间 $(0,+\infty)$ 上的最小值。", r"$4$", r"$f'(x)=1-\frac4{x^2}$。当 $0<x<2$ 时导数为负，当 $x>2$ 时导数为正，故函数在 $x=2$ 处取得最小值 $4$。"),
            (r"求函数 $f(x)=\ln x-x$ 在区间 $(0,+\infty)$ 上的最大值。", r"$-1$", r"$f'(x)=\frac1x-1$。函数在 $(0,1)$ 上递增，在 $(1,+\infty)$ 上递减，故最大值为 $f(1)=-1$。"),
            (r"求函数 $f(x)=xe^{-x}$ 在区间 $(0,+\infty)$ 上的最大值。", r"$\frac1e$", r"$f'(x)=e^{-x}(1-x)$。函数在 $(0,1)$ 上递增，在 $(1,+\infty)$ 上递减，故最大值为 $f(1)=1/e$。"),
            (r"求函数 $f(x)=x^4-2x^2$ 在区间 $[-2,2]$ 上的最大值与最小值。", r"最大值 $8$，最小值 $-1$", r"$f'(x)=4x(x-1)(x+1)$，驻点为 $-1,0,1$。比较这些点与端点 $\pm2$ 的函数值，得最大值 $8$、最小值 $-1$。"),
            (r"求函数 $f(x)=x-\ln x$ 在区间 $(0,+\infty)$ 上的最小值。", "$1$", r"$f'(x)=1-\frac1x$。函数在 $(0,1)$ 上递减，在 $(1,+\infty)$ 上递增，故最小值为 $f(1)=1$。"),
            (r"求函数 $f(x)=e^x-3x$ 在区间 $\mathbb R$ 上的最小值。", r"$3-3\ln3$", r"$f'(x)=e^x-3$，唯一驻点为 $x=\ln3$。导数在其左侧为负、右侧为正，故最小值为 $f(\ln3)=3-3\ln3$。"),
            (r"求函数 $f(x)=x^2+\frac{16}{x}$ 在区间 $(0,+\infty)$ 上的最小值。", "$12$", r"$f'(x)=2x-\frac{16}{x^2}=\frac{2(x^3-8)}{x^2}$。函数在 $x=2$ 左减右增，故最小值为 $f(2)=12$。"),
            (r"求函数 $f(x)=\frac{\ln x}{x}$ 在区间 $(0,+\infty)$ 上的最大值。", r"$\frac1e$", r"$f'(x)=\frac{1-\ln x}{x^2}$。函数在 $(0,e)$ 上递增，在 $(e,+\infty)$ 上递减，故最大值为 $f(e)=1/e$。"),
            (r"求函数 $f(x)=(x^2-4)^2$ 在区间 $\mathbb R$ 上的极值。", r"极大值 $16$，极小值 $0$", r"$f'(x)=4x(x-2)(x+2)$。导数符号表明 $x=0$ 为极大值点、$x=\pm2$ 为极小值点，故极大值为 $16$，极小值为 $0$。"),
        ]
        return _nonchoice_exact_blueprint(request, "extremum_solution", "solution", rows)
    return None


def _inequality_challenge_choice_blueprint(
    request: dict[str, Any],
    question_type: str,
) -> dict[str, Any] | None:
    """Parameter-range single choices that genuinely satisfy challenge difficulty."""
    knowledge = str(request.get("knowledge") or "")
    if (
        question_type != "single_choice"
        or str(request.get("difficulty") or "auto") != "challenge"
        or not ("不等式" in knowledge or "构造函数" in knowledge)
    ):
        return None
    rows = [
        (
            r"设参数 $a>0$，函数 $f_a(x)=e^x-ax-1$（$x\in\mathbb R$）。若 "
            r"$f_a(x)\geq0$ 对任意实数 $x$ 恒成立，则 $a$ 的取值集合为（　）。",
            r"$\{1\}$",
            [r"$(0,1]$", r"$[1,+\infty)$", r"$(0,e]$"],
            r"先由 $f_a'(x)=e^x-a$ 得唯一极小值点 $x=\ln a$，最小值为 "
            r"$a-a\ln a-1$。再令 $\phi(a)=a-a\ln a-1$，则 "
            r"$\phi'(a)=-\ln a$，故 $\phi(a)\leq\phi(1)=0$。恒成立要求最小值非负，"
            r"所以只能 $a=1$；此时 $e^x\geq1+x$，充分。",
        ),
        (
            r"设参数 $a>0$。若 $\ln x\leq ax-(1+\ln2)$ 对任意 $x>0$ 恒成立，"
            r"则 $a$ 的取值范围为（　）。",
            r"$[2,+\infty)$",
            [r"$(0,2]$", r"$[1,+\infty)$", r"$(2,+\infty)$"],
            r"取 $x=\frac12$ 得必要条件 $a\geq2$。当 $a=2$ 时，对 $t=2x$ 使用 "
            r"$\ln t\leq t-1$，得到 $\ln x\leq2x-1-\ln2$；当 $a>2$ 时右端更大。"
            r"因此充要范围为 $[2,+\infty)$。",
        ),
        (
            r"设参数 $a\geq0$。若 $x^2-2a\ln x\geq a$ 对任意 $x>0$ 恒成立，"
            r"则 $a$ 的取值范围为（　）。",
            r"$[0,1]$",
            [r"$[0,2]$", r"$(0,1]$", r"$[1,+\infty)$"],
            r"$a=0$ 时成立。$a>0$ 时令 $t=x^2/a$，则左端减去 $a$ 为 "
            r"$a[t-1-\ln t-\ln a]$。由 $t-1-\ln t\geq0$，其关于 $x$ 的最小值为 "
            r"$-a\ln a$，故恒成立等价于 $0<a\leq1$。结合端点得到 $[0,1]$。",
        ),
        (
            r"设参数 $a>0$。若 $e^x\geq ax$ 对任意 $x\in\mathbb R$ 恒成立，"
            r"则 $a$ 的取值范围为（　）。",
            r"$(0,e]$",
            [r"$(0,1]$", r"$[1,e]$", r"$(0,+\infty)$"],
            r"构造 $F_a(x)=e^x-ax$。由 $F_a'(x)=e^x-a$，唯一极小值点为 $x=\ln a$，"
            r"最小值为 $a-a\ln a=a(1-\ln a)$。因 $a>0$，恒成立当且仅当 "
            r"$1-\ln a\geq0$，即 $0<a\leq e$。",
        ),
        (
            r"设参数 $a>0$。若 $\ln x\leq ax+a-2$ 对任意 $x>0$ 恒成立，"
            r"则 $a$ 的取值范围为（　）。",
            r"$[1,+\infty)$",
            [r"$(0,1]$", r"$(1,+\infty)$", r"$[e,+\infty)$"],
            r"构造 $F_a(x)=\ln x-ax$，其唯一极大值点为 $x=1/a$，最大值为 "
            r"$-1-\ln a$。题设等价于 $-1-\ln a\leq a-2$，即 "
            r"$H(a)=a-1+\ln a\geq0$。因 $H'(a)=1+1/a>0,H(1)=0$，故范围为 "
            r"$[1,+\infty)$。",
        ),
        (
            r"设实数参数 $a$。若 $e^x\geq1+x+ax^2$ 对任意 $x\geq0$ 恒成立，"
            r"则 $a$ 的取值范围为（　）。",
            r"$(-\infty,\frac12]$",
            [r"$[0,\frac12]$", r"$(-\infty,1]$", r"$[\frac12,+\infty)$"],
            r"若 $a>\frac12$，令 $F(x)=e^x-1-x-ax^2$，则 "
            r"$F(0)=F'(0)=0,F''(0)=1-2a<0$，故在 $0$ 的右邻域内 $F<0$，不成立。"
            r"当 $a=\frac12$ 时，$F''(x)=e^x-1\geq0$，结合 $F'(0)=F(0)=0$ 得 "
            r"$F\geq0$；任意更小的 $a$ 右端不增，仍成立。故范围为 $(-\infty,\frac12]$。",
        ),
    ]
    return _exact_choice_blueprint(
        request,
        "inequality_challenge_choice",
        rows,
    )


def _inequality_solution_blueprint(
    request: dict[str, Any],
    question_type: str,
) -> dict[str, Any] | None:
    """Exact, structurally distinct challenge solutions for fragile inequality prompts."""
    knowledge = str(request.get("knowledge") or "")
    if question_type != "solution" or not ("不等式" in knowledge or "构造函数" in knowledge):
        return None
    rows = [
        (
            r"设 $a>0$，函数 $f_a(x)=e^x-ax-1\ (x\in\mathbb R)$。"
            r"（1）求 $f_a(x)$ 的最小值；（2）若 $f_a(x)\geq0$ 对任意实数 $x$ 恒成立，"
            r"求 $a$ 的值，并证明必要性与充分性。最终答案只写参数数值。",
            r"$1$",
            r"（1）$f_a'(x)=e^x-a$，故 $x=\ln a$ 为唯一极小值点，最小值为 $a-a\ln a-1$。"
            r"（2）令 "
            r"$\phi(a)=a-a\ln a-1$，则 $\phi'(a)=-\ln a$，故 $\phi(a)\leq\phi(1)=0$。"
            r"恒成立要求最小值非负，所以只能 $a=1$；此时 $e^x\geq1+x$，充分。",
        ),
        (
            r"设 $a>0,b\in\mathbb R$，函数 $F_a(x)=\ln x-ax\ (x>0)$。"
            r"（1）求 $F_a(x)$ 的最大值；（2）证明不等式 $\ln x\leq ax+b$ 对任意 $x>0$ 恒成立"
            r"当且仅当 $b\geq-1-\ln a$；（3）若 $b=a-2$，求参数 $a$ 的最小值。"
            r"最终答案只写该最小值。",
            r"$1$",
            r"（1）$F_a'(x)=1/x-a$，故 $x=1/a$ 为唯一极大值点，最大值为 $-1-\ln a$。"
            r"（2）恒成立等价于 $\max_{x>0}(\ln x-ax)\leq b$，即 $b\geq-1-\ln a$。"
            r"（3）代入 $b=a-2$，得 $a-1+\ln a\geq0$。令 $H(a)=a-1+\ln a$，"
            r"则 $H'(a)=1+1/a>0,H(1)=0$，所以 $a\geq1$，最小值为 $1$。",
        ),
        (
            r"设 $g(x)=\frac{e^x-1-x}{x^2}\ (x>0)$。"
            r"（1）通过构造辅助函数并逐层求导，证明 $g(x)$ 在 $(0,+\infty)$ 上严格递增；"
            r"（2）若 $e^x\geq1+x+ax^2$ 对任意 $x\geq0$ 恒成立，求实数 $a$ 的最大值。"
            r"最终答案只写该最大值。",
            r"$1/2$",
            r"（1）$g'(x)=H(x)/x^3$，其中 $H(x)=e^x(x-2)+x+2$。"
            r"令 $J(x)=H'(x)=e^x(x-1)+1$，则 $J'(x)=xe^x>0$，且 $J(0)=H(0)=0$，"
            r"故 $H(x)>0$，从而 $g'(x)>0$。"
            r"（2）若 $a>1/2$，函数 $F=e^x-1-x-ax^2$ 满足 $F(0)=F'(0)=0,F''(0)<0$，"
            r"故在 $0$ 的右邻域内 $F<0$，矛盾；而 $a=1/2$ 时由两次单调性可证 $F\geq0$。"
            r"所以最大值为 $1/2$。",
        ),
        (
            r"设 $a>0,b\in\mathbb R$，函数 $f_a(x)=e^x-ax\ (x\in\mathbb R)$。"
            r"（1）求 $f_a(x)$ 的最小值；（2）证明不等式 $e^x\geq ax+b$ 对任意 $x\in\mathbb R$ 恒成立"
            r"当且仅当 $b\leq a-a\ln a$；（3）若 $b=0$，求参数 $a$ 的最大值。"
            r"最终答案只写该最大值。",
            r"$e$",
            r"（1）$f_a'(x)=e^x-a$，故 $x=\ln a$ 为唯一极小值点，最小值为 $a-a\ln a$。"
            r"（2）恒成立等价于 $b\leq\min_{x\in\mathbb R}(e^x-ax)$，即 $b\leq a-a\ln a$。"
            r"（3）代入 $b=0$，得 $a(1-\ln a)\geq0$。因 $a>0$，故 $\ln a\leq1$，"
            r"即 $0<a\leq e$，最大值为 $e$。",
        ),
        (
            r"设 $a>0,b\in\mathbb R$，函数 $P_a(x)=x^2-2a\ln x\ (x>0)$。"
            r"（1）求 $P_a(x)$ 的最小值；（2）证明 $P_a(x)\geq b$ 对任意 $x>0$ 恒成立"
            r"当且仅当 $b\leq a-a\ln a$；（3）若 $b=a$，求参数 $a$ 的最大值。"
            r"最终答案只写该最大值。",
            r"$1$",
            r"（1）$P_a'(x)=2x-2a/x=2(x^2-a)/x$，故唯一极小值点为 $x=\sqrt a$，"
            r"最小值为 $a-a\ln a$。（2）恒成立等价于 $b\leq\min_{x>0}P_a(x)$，"
            r"即 $b\leq a-a\ln a$。（3）代入 $b=a$ 得 $-a\ln a\geq0$。因 $a>0$，"
            r"故 $0<a\leq1$，最大值为 $1$。",
        ),
        (
            r"设 $a>0,b\in\mathbb R$，函数 $Q_a(x)=x-a\ln x\ (x>0)$。"
            r"（1）求 $Q_a(x)$ 的最小值；（2）证明 $Q_a(x)\geq b$ 对任意 $x>0$ 恒成立"
            r"当且仅当 $b\leq a-a\ln a$；（3）若 $b=a$，求参数 $a$ 的最大值。"
            r"最终答案只写该最大值。",
            r"$1$",
            r"（1）$Q_a'(x)=1-a/x=(x-a)/x$，故唯一极小值点为 $x=a$，"
            r"最小值为 $a-a\ln a$。（2）恒成立等价于 $b\leq\min_{x>0}Q_a(x)$，"
            r"即 $b\leq a-a\ln a$。（3）代入 $b=a$ 得 $-a\ln a\geq0$。因 $a>0$，"
            r"故 $0<a\leq1$，最大值为 $1$。",
        ),
        (
            r"设实数参数 $a$。（1）当 $0<a<1$ 时，证明 $x^a\leq ax+1-a$ 对任意 $x>0$ 恒成立；"
            r"（2）分类讨论参数，求使该不等式对任意 $x>0$ 恒成立的 $a$ 的取值范围，"
            r"并说明区间外参数为何不成立。最终答案只写参数范围。",
            r"$[0,1]$",
            r"令 $F_a(x)=ax+1-a-x^a$。当 $0<a<1$ 时，"
            r"$F_a'(x)=a(1-x^{a-1})$，故 $F_a$ 在 $(0,1)$ 上递减、在 $(1,+\infty)$ 上递增，"
            r"且 $F_a(1)=0$，所以不等式成立。$a=0,1$ 时两边恒等。若 $a<0$ 或 $a>1$，"
            r"则 $F_a(1)=F_a'(1)=0$ 而 $F_a''(1)=-a(a-1)<0$，故 $x=1$ 为严格局部极大点，"
            r"其邻域存在 $F_a(x)<0$。因此取值范围为 $[0,1]$。",
        ),
        (
            r"设参数 $a\geq0$。（1）证明 $\ln(1+x)\geq\frac{2x}{x+2}$ 对任意 $x\geq0$ 恒成立；"
            r"（2）若 $\ln(1+x)\geq\frac{x}{1+ax}$ 对任意 $x\geq0$ 恒成立，"
            r"求 $a$ 的最小值，并证明必要性与充分性。最终答案只写该最小值。",
            r"$\frac12$",
            r"（1）令 $h(x)=\ln(1+x)-\frac{2x}{x+2}$，则 "
            r"$h'(x)=\frac{x^2}{(1+x)(x+2)^2}\geq0$，且 $h(0)=0$，故结论成立。"
            r"（2）当 $a\geq\frac12$ 时，$\frac{x}{1+ax}\leq\frac{2x}{x+2}$，故充分。"
            r"若 $0\leq a<\frac12$，令 $F_a(x)=\ln(1+x)-\frac{x}{1+ax}$，"
            r"则 $F_a(0)=F_a'(0)=0,F_a''(0)=2a-1<0$，故充分小的 $x>0$ 使 $F_a(x)<0$。"
            r"所以最小值为 $\frac12$。",
        ),
        (
            r"设 $g(x)=\frac{1-\cos x}{x^2}\ (0<x\leq\pi)$。"
            r"（1）通过构造两层辅助函数证明 $g(x)$ 在 $(0,\pi]$ 上严格递减；"
            r"（2）若 $1-\cos x\geq ax^2$ 对任意 $x\in[-\pi,\pi]$ 恒成立，"
            r"求实数参数 $a$ 的最大值。最终答案只写该最大值。",
            r"$\frac{2}{\pi^2}$",
            r"（1）$g'(x)=-q(x)/x^3$，其中 $q(x)=2(1-\cos x)-x\sin x$。"
            r"令 $r(x)=q'(x)=\sin x-x\cos x$，则 $r'(x)=x\sin x\geq0$，"
            r"且 $r(0)=q(0)=0$，故 $q(x)>0$，从而 $g'(x)<0$。"
            r"（2）原条件对 $x\ne0$ 等价于 $a\leq g(|x|)$。由单调性，"
            r"$\min_{0<x\leq\pi}g(x)=g(\pi)=\frac{2}{\pi^2}$；该值在 $x=\pm\pi$ 取等，"
            r"故最大值为 $\frac{2}{\pi^2}$。",
        ),
        (
            r"设实数参数 $a$。（1）作代换 $t=\ln x$，通过偶性与二阶导数证明 "
            r"$x+\frac1x-2\geq(\ln x)^2$ 对任意 $x>0$ 恒成立；"
            r"（2）若 $x+\frac1x-2\geq a(\ln x)^2$ 对任意 $x>0$ 恒成立，"
            r"求 $a$ 的最大值。最终答案只写该最大值。",
            r"$1$",
            r"（1）令 $t=\ln x$，需证 $H(t)=e^t+e^{-t}-2-t^2\geq0$。$H$ 为偶函数；"
            r"当 $t\geq0$ 时，$H''(t)=e^t+e^{-t}-2\geq0$，又 $H'(0)=H(0)=0$，"
            r"故 $H(t)\geq0$。（2）$a=1$ 已充分。若 $a>1$，令 "
            r"$H_a(t)=e^t+e^{-t}-2-at^2$，则 $H_a(0)=H_a'(0)=0$ 而 "
            r"$H_a''(0)=2-2a<0$，故零点附近存在 $H_a(t)<0$。所以最大值为 $1$。",
        ),
        (
            r"设实数参数 $a$。（1）令 $B(t)=(t+1)\ln t-2(t-1)\ (t>0)$，"
            r"证明 $B(t)$ 与 $t-1$ 同号；（2）若 "
            r"$(x-1)\ln x\geq a(\sqrt{x}-1)^2$ 对任意 $x>0$ 恒成立，"
            r"求 $a$ 的最大值，并证明该常数最优。最终答案只写该最大值。",
            r"$4$",
            r"（1）$B'(t)=\ln t-1+\frac1t=:K(t)$，而 "
            r"$K'(t)=\frac{t-1}{t^2}$，故 $K(t)\geq K(1)=0$。于是 $B$ 递增且 $B(1)=0$，"
            r"所以 $B(t)$ 与 $t-1$ 同号。（2）令 $t=\sqrt{x}$，则 $a=4$ 时两端之差为 "
            r"$2(t-1)B(t)\geq0$。反之，对 $t\ne1$ 有 "
            r"$a\leq\frac{2(t+1)\ln t}{t-1}$；令 $t\to1$ 得 $a\leq4$。故最大值为 $4$。",
        ),
        (
            r"在闭区间 $[1,e]$ 内任取 $1\leq x<y\leq e$。"
            r"（1）运用拉格朗日中值定理求差商 "
            r"$\frac{\ln y-\ln x}{y-x}$ 的下确界，并说明该下确界为何不必取到；"
            r"（2）若 $\ln y-\ln x\geq a(y-x)$ 对上述任意 $x,y$ 恒成立，"
            r"求实数参数 $a$ 的最大值。最终答案只写该最大值。",
            r"$\frac1e$",
            r"（1）由拉格朗日中值定理，存在 $\xi\in(x,y)$，使 "
            r"$\frac{\ln y-\ln x}{y-x}=\frac1\xi>\frac1e$。取 $y=e,x=e-h$，"
            r"其中 $h\to0^+$，该差商趋于 $(\ln x)'|_{x=e}=\frac1e$，"
            r"故下确界恰为 $\frac1e$，但 $x<y$ 时不取到。"
            r"（2）$a=\frac1e$ 由前述估计充分；上述趋近又说明任何 $a>\frac1e$ 均失败，"
            r"故最大值为 $\frac1e$。",
        ),
    ]
    return _nonchoice_exact_blueprint(
        request,
        "inequality_solution",
        "solution",
        rows,
    )


def _inequality_fill_blueprint(
    request: dict[str, Any],
    question_type: str,
) -> dict[str, Any] | None:
    """Exact challenge fill-ins for inequality auto batches.

    These keep the requested fill-blank form while still requiring a parameter,
    a transcendental structure and a global/endpoint argument.  The blank is the
    unique final parameter value; the full proof remains in the solution.
    """
    knowledge = str(request.get("knowledge") or "")
    if question_type != "fill_blank" or not ("不等式" in knowledge or "构造函数" in knowledge):
        return None
    rows = [
        (
            r"设参数 $a>0$，$f_a(x)=e^x-ax-1$（$x\in\mathbb R$）。"
            r"若 $f_a(x)\geq0$ 对任意实数 $x$ 恒成立，则 $a=$______。",
            r"$1$",
            r"先对 $x$ 求最值：$f_a'(x)=e^x-a$，故唯一极小值点为 $x=\ln a$，"
            r"且 $\min f_a=a-a\ln a-1$。再令 $\phi(a)=a-a\ln a-1$，则 "
            r"$\phi'(a)=-\ln a$，所以 $\phi(a)\leq\phi(1)=0$。恒成立要求最小值非负，"
            r"只能有 $a=1$；此时由 $e^x\geq1+x$ 知充分。",
        ),
        (
            r"设参数 $a>0$。若不等式 $\ln x\leq ax-(1+\ln2)$ 对任意 $x>0$ 恒成立，"
            r"则 $a$ 的最小值为______。",
            r"$2$",
            r"必要性：取等号候选点 $x=\frac12$，得 $-\ln2\leq\frac a2-1-\ln2$，"
            r"故 $a\geq2$。充分性：当 $a=2$ 时，对 $t=2x$ 使用 "
            r"$\ln t\leq t-1$，得到 $\ln x\leq2x-1-\ln2$；$a>2$ 时右端更大。"
            r"所以最小值为 $2$。",
        ),
        (
            r"设参数 $a\geq0$。若 $x^2-2a\ln x\geq a$ 对任意 $x>0$ 恒成立，"
            r"则 $a$ 的最大值为______。",
            r"$1$",
            r"$a=0$ 时显然成立。$a>0$ 时令 $t=x^2/a$，则左端减去 $a$ 为 "
            r"$a[t-1-\ln t-\ln a]$。由 $t-1-\ln t\geq0$，其关于 $x$ 的最小值为 "
            r"$-a\ln a$，故恒成立等价于 $0<a\leq1$。结合端点 $a=0$，最大值为 $1$。",
        ),
        (
            r"设参数 $a>0$。若 $e^x\geq ax$ 对任意 $x\in\mathbb R$ 恒成立，"
            r"则 $a$ 的最大值为______。",
            r"$e$",
            r"构造 $F_a(x)=e^x-ax$。由 $F_a'(x)=e^x-a$，唯一极小值点为 $x=\ln a$，"
            r"最小值为 $a-a\ln a=a(1-\ln a)$。恒成立当且仅当该最小值非负，"
            r"即 $0<a\leq e$，故参数最大值为 $e$。",
        ),
        (
            r"设参数 $a>0$。若 $\ln x\leq ax+a-2$ 对任意 $x>0$ 恒成立，"
            r"则 $a$ 的最小值为______。",
            r"$1$",
            r"构造 $F_a(x)=\ln x-ax$。由 $F_a'(x)=1/x-a$，其唯一极大值点为 $x=1/a$，"
            r"最大值为 $-1-\ln a$。题设恒成立等价于 $-1-\ln a\leq a-2$，即 "
            r"$H(a)=a-1+\ln a\geq0$。因 $H'(a)=1+1/a>0,H(1)=0$，故 $a\geq1$，"
            r"最小值为 $1$。",
        ),
        (
            r"设实数参数 $a$。若 $e^x\geq1+x+ax^2$ 对任意 $x\geq0$ 恒成立，"
            r"则 $a$ 的最大值为______。",
            r"$\frac12$",
            r"若 $a>\frac12$，令 $F(x)=e^x-1-x-ax^2$，则 "
            r"$F(0)=F'(0)=0,F''(0)=1-2a<0$，所以在 $0$ 的右邻域内 $F(x)<0$，矛盾。"
            r"当 $a=\frac12$ 时，$F''(x)=e^x-1\geq0$，由 $F'(0)=F(0)=0$ 依次得到 "
            r"$F'(x)\geq0,F(x)\geq0$。故最大值为 $\frac12$。",
        ),
        (
            r"设参数 $a\in(0,1)$。若不等式 $x^a(1-x)^{1-a}\leq\frac12$ "
            r"对区间 $x\in(0,1)$ 内任意实数 $x$ 恒成立，则 $a=$______。",
            r"$\frac12$",
            r"固定 $a$，取对数并构造函数 $L_a(x)=a\ln x+(1-a)\ln(1-x)$。由 "
            r"$L_a'(x)=\frac{a-x}{x(1-x)}$ 及两端的极限，$L_a$ 在 $x=a$ 处取得唯一最大值，"
            r"故原式恒成立等价于 $a^a(1-a)^{1-a}\leq\frac12$。再构造参数辅助函数 "
            r"$H(a)=a\ln a+(1-a)\ln(1-a)$，则 $H'(a)=\ln\frac a{1-a}$，"
            r"$H''(a)=\frac1a+\frac1{1-a}>0$，所以 $H(a)\geq H(\frac12)=-\ln2$，"
            r"等号仅在 $a=\frac12$ 取得。结合上式只能有 $a=\frac12$；此时 "
            r"$x(1-x)\leq\frac14$，故充分，且等号仅在 $x=\frac12$ 取得。",
        ),
        (
            r"设实数参数 $a$。若不等式 $\sin x\leq x-ax^3$ "
            r"对区间 $x\in[0,\frac\pi2]$ 内任意实数 $x$ 恒成立，"
            r"则 $a$ 的最大值为______。",
            r"$\frac{4(\pi-2)}{\pi^3}$",
            r"当 $x>0$ 时，题设等价于 $a\leq R(x)=\frac{x-\sin x}{x^3}$。求得 "
            r"$R'(x)=\frac{N(x)}{x^4}$，其中 $N(x)=3\sin x-x\cos x-2x$。"
            r"依次构造辅助函数可得 $N''(x)=x\cos x-\sin x$，而 "
            r"$(x\cos x-\sin x)'=-x\sin x<0$。结合各函数在 $0$ 处的边界值，"
            r"依次推出 $N''(x)<0,N'(x)<0,N(x)<0$，故 $R$ 在 $(0,\frac\pi2]$ 上严格递减。"
            r"因此 $a\leq R(\frac\pi2)=\frac{4(\pi-2)}{\pi^3}$。取该值时，对 "
            r"$0<x\leq\frac\pi2$ 有 $a\leq R(x)$，且 $x=0$ 时原式也成立，所以该常数确为最大值。",
        ),
        (
            r"设实数参数 $a$。若不等式 $|x^2-ax|\leq3-2\sqrt2$ 对区间 $x\in[0,1]$ "
            r"内任意实数 $x$ 恒成立，则 $a=$______。",
            r"$2(\sqrt2-1)$",
            r"记 $E=3-2\sqrt2=(\sqrt2-1)^2$。先取区间端点 $x=1$，由 "
            r"$|1-a|\leq E$ 得 $a\geq1-E=2(\sqrt2-1)$，并同时得到 $0<a<2$。"
            r"因此内点 $x=\frac a2$ 属于 $(0,1)$；再代入题设得 $\frac{a^2}{4}\leq E$，"
            r"从而 $a\leq2(\sqrt2-1)$。两条必要条件结合，唯一得到 $a=2(\sqrt2-1)$。"
            r"最后验证充分性：此时辅助函数 $q(x)=x^2-ax$ 在 $x=\sqrt2-1$ 处取值 $-E$，"
            r"而两个端点值为 $q(0)=0,q(1)=E$；由其先减后增可知 $-E\leq q(x)\leq E$。",
        ),
        (
            r"设参数 $a>0$。若不等式 $x^a+\frac1{x^a}\geq a\left(x+\frac1x\right)$ "
            r"对任意 $x>0$ 恒成立，则 $a=$______。",
            r"$1$",
            r"先考查有限边界点 $x=1$，得到 $2\geq2a$，故 $a\leq1$。"
            r"再考查无穷远：若 $0<a<1$，将不等式两边同除以 $x$，则左边 "
            r"$x^{a-1}+x^{-a-1}\to0$，右边 $a(1+x^{-2})\to a>0$，与恒成立矛盾，"
            r"故必须 $a\geq1$。结合两段必要性可知 $a=1$。最后代回后两边恒等，故充分。",
        ),
        (
            r"设实数参数 $a$。若不等式 "
            r"$\ln x\leq a(x-1)+(1-a)\left(1-\frac1x\right)$ "
            r"对区间 $x\in[1,+\infty)$ 内任意实数 $x$ 恒成立，则 $a$ 的最小值为______。",
            r"$\frac12$",
            r"令 $F_a(x)=a(x-1)+(1-a)(1-\frac1x)-\ln x$。有 "
            r"$F_a(1)=F_a'(1)=0$ 且 $F_a''(1)=2a-1$；若 $F_a''(1)<0$，则由二阶导数在右邻域"
            r"连续可知 $F_a$ 先减为负，故恒成立必有 $a\geq\frac12$。再验证充分性。把 "
            r"$F_a(x)$ 分解为 $F_a(x)=H(x)+(a-\frac12)\frac{(x-1)^2}{x}$，其中 "
            r"$H(x)=\frac12(x-\frac1x)-\ln x$。由于 $H(1)=0$ 且 "
            r"$H'(x)=\frac{(x-1)^2}{2x^2}\geq0$，故在给定区间上 $H(x)\geq0$；"
            r"结合 $a\geq\frac12$ 得 $F_a(x)\geq0$。因此最小值为 $\frac12$。",
        ),
        (
            r"设参数 $a\geq0$。对任意满足 $x>0,y>0,xy=1$ 的实数 $x,y$，"
            r"若不等式 $x+y-2\geq a(\ln x-\ln y)^2$ 恒成立，则 $a$ 的最大值为______。",
            r"$\frac14$",
            r"由约束令 $t=\ln x$，则 $t\in\mathbb R,y=e^{-t}$，原不等式化为 "
            r"$e^t+e^{-t}-2\geq4at^2$。构造辅助函数 "
            r"$F_a(t)=e^t+e^{-t}-2-4at^2$；恒成立使 $t=0$ 为极小值点，而 "
            r"$F_a''(0)=2-8a\geq0$，故 $a\leq\frac14$。再验证 $a=\frac14$：令 "
            r"$G(t)=e^t+e^{-t}-2-t^2$，则 $G$ 为偶函数，且 "
            r"$G''(t)=e^t+e^{-t}-2\geq0$。结合 $G'(0)=G(0)=0$，先在 "
            r"$[0,+\infty)$ 上推出 $G'(t)\geq0,G(t)\geq0$，再由偶性推广到全体实数。"
            r"因此最大值为 $\frac14$。",
        ),
    ]
    return _nonchoice_exact_blueprint(
        request,
        "inequality_fill",
        "fill_blank",
        rows,
    )


def _optimum_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if "求最值" not in request["knowledge"] or question_type != "fill_blank":
        return None
    rows = [
        ("函数 $f(x)=-x^2+4x+1$ 在 $\\mathbb R$ 上的最大值为______。", "$5$", "配方得 $f(x)=-(x-2)^2+5$，故最大值为 $5$。"),
        ("函数 $f(x)=x^2-6x+10$ 在 $\\mathbb R$ 上的最小值为______。", "$1$", "配方得 $f(x)=(x-3)^2+1$，故最小值为 $1$。"),
        ("函数 $f(x)=x+\\frac4x$（$x>0$）的最小值为______。", "$4$", "由基本不等式 $x+\\frac4x\\ge2\\sqrt4=4$，等号在 $x=2$ 时成立。"),
        ("函数 $f(x)=\\ln x-x$（$x>0$）的最大值为______。", "$-1$", "$f'(x)=\\frac1x-1$，在 $x=1$ 左正右负，故最大值为 $f(1)=-1$。"),
        ("函数 $f(x)=xe^{-x}$（$x>0$）的最大值为______。", "$\\frac1e$", "$f'(x)=e^{-x}(1-x)$，函数在 $(0,1)$ 递增、$(1,+\\infty)$ 递减，最大值为 $f(1)=\\frac1e$。"),
        ("函数 $f(x)=x(4-x)$ 在 $[0,4]$ 上的最大值为______。", "$4$", "$f(x)=-(x-2)^2+4$，且 $x=2$ 在给定区间内，所以最大值为 $4$。"),
        ("函数 $f(x)=(x-2)^2+3$ 在 $[0,5]$ 上的最小值为______。", "$3$", "平方项非负，且 $x=2$ 属于区间，故最小值为 $3$。"),
        ("函数 $f(x)=x^3-3x$ 在 $[-2,2]$ 上的最大值为______。", "$2$", "$f'(x)=3(x^2-1)$，比较端点及驻点 $x=\\pm1$ 的函数值，最大值为 $2$。"),
        ("函数 $f(x)=e^x-x$ 在 $\\mathbb R$ 上的最小值为______。", "$1$", "$f'(x)=e^x-1$，在 $x=0$ 左负右正，故最小值为 $f(0)=1$。"),
        ("函数 $f(x)=\\sin x+\\cos x$ 在 $[0,\\frac\\pi2]$ 上的最大值为______。", "$\\sqrt2$", "$\\sin x+\\cos x=\\sqrt2\\sin(x+\\frac\\pi4)$，在 $x=\\frac\\pi4$ 取最大值 $\\sqrt2$。"),
    ]
    return _nonchoice_exact_blueprint(request, "optimum", "fill_blank", rows)


def _zeros_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if "函数零点个数" not in request["knowledge"] or question_type != "solution":
        return None
    rows = [
        ("求函数 $f(x)=(x-1)(x+2)(x-3)$ 的零点个数。", "$3$", "由因式分解式可知零点为 $x=1,-2,3$，互不相同，所以零点个数为 $3$。"),
        ("求函数 $f(x)=x^4-5x^2+4$ 的零点个数。", "$4$", "令 $t=x^2$，则 $t^2-5t+4=(t-1)(t-4)=0$，故 $x=\\pm1,\\pm2$，共有 $4$ 个零点。"),
        ("判断方程 $\\ln x=x-1$（$x>0$）的实根个数。", "$1$", "设 $h(x)=x-1-\\ln x$，则 $h'(x)=1-\\frac1x$，$h$ 在 $x=1$ 取得唯一最小值 $0$，故只有根 $x=1$。"),
        ("判断方程 $e^{-x}=x$ 的实根个数。", "$1$", "根必满足 $x>0$。令 $h(x)=e^{-x}-x$，则 $h'(x)=-e^{-x}-1<0$，且 $h(0)>0,h(1)<0$，故恰有一个实根。"),
        ("求函数 $f(x)=x^3-3x$ 的零点个数。", "$3$", "$f(x)=x(x^2-3)$，零点为 $0,\\pm\\sqrt3$，所以共有 $3$ 个。"),
        ("判断方程 $|x|=x^2$ 的实根个数。", "$3$", "当 $x\\ge0$ 时得 $x=0,1$；当 $x<0$ 时得 $x=-1$，共 $3$ 个实根。"),
        ("求函数 $f(x)=\\sin x$ 在区间 $[0,3\\pi]$ 内的零点个数。", "$4$", "区间内零点为 $0,\\pi,2\\pi,3\\pi$，共 $4$ 个。"),
        ("判断方程 $2^x=1$ 的实根个数。", "$1$", "指数函数 $2^x$ 严格递增，且 $2^0=1$，所以只有实根 $x=0$。"),
        ("判断方程 $x+\\ln x=0$（$x>0$）的实根个数。", "$1$", "令 $h(x)=x+\\ln x$，则 $h'(x)=1+\\frac1x>0$；又 $h(x)$ 从 $-\\infty$ 连续增至 $+\\infty$，故恰有一个实根。"),
        ("求函数 $f(x)=\\frac{x^2-1}{x^2+1}$ 的零点个数。", "$2$", "分母恒正，零点由 $x^2-1=0$ 给出，即 $x=\\pm1$，共有 $2$ 个。"),
    ]
    return _nonchoice_exact_blueprint(request, "zeros", "solution", rows)


def _expectation_variance_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    if not ("期望" in request["knowledge"] or "方差" in request["knowledge"]) or question_type != "solution":
        return None
    rows = [
        ("随机变量 $X$ 服从参数 $p=\\frac13$ 的两点分布，即 $P(X=1)=\\frac13$，$P(X=0)=\\frac23$。求 $E(X)$ 与 $D(X)$。", "$E(X)=\\frac13,\\quad D(X)=\\frac29$", "两点分布有 $E(X)=p=\\frac13$，$D(X)=p(1-p)=\\frac13\\cdot\\frac23=\\frac29$。"),
        ("随机变量 $X$ 取 $-1,2$ 的概率分别为 $\\frac23,\\frac13$。求 $E(X)$ 与 $D(X)$。", "$E(X)=0,\\quad D(X)=2$", "$E(X)=-1\\cdot\\frac23+2\\cdot\\frac13=0$；$E(X^2)=1\\cdot\\frac23+4\\cdot\\frac13=2$，故 $D(X)=2$。"),
        ("随机变量 $X$ 取 $0,1,2$ 的概率分别为 $\\frac14,\\frac12,\\frac14$。求 $E(X)$ 与 $D(X)$。", "$E(X)=1,\\quad D(X)=\\frac12$", "$E(X)=1$，$E(X^2)=0+\\frac12+1=\\frac32$，故 $D(X)=\\frac32-1=\\frac12$。"),
        ("已知 $E(X)=3,D(X)=4$，令 $Y=2X-1$。求 $E(Y)$ 与 $D(Y)$。", "$E(Y)=5,\\quad D(Y)=16$", "由期望和方差的线性性质，$E(Y)=2E(X)-1=5$，$D(Y)=2^2D(X)=16$。"),
        ("随机变量 $X,Y$ 相互独立，且 $E(X)=1,E(Y)=2,D(X)=3,D(Y)=4$。求 $S=X+Y$ 的期望与方差。", "$E(S)=3,\\quad D(S)=7$", "$E(S)=E(X)+E(Y)=3$；独立时方差可加，$D(S)=3+4=7$。"),
        ("随机变量 $X\\sim B(4,\\frac12)$。求 $E(X)$ 与 $D(X)$。", "$E(X)=2,\\quad D(X)=1$", "二项分布满足 $E(X)=np=2$，$D(X)=np(1-p)=1$。"),
        ("随机变量 $X$ 取 $1,3$ 的概率分别为 $\\frac34,\\frac14$。求 $E(X)$ 与 $D(X)$。", "$E(X)=\\frac32,\\quad D(X)=\\frac34$", "$E(X)=\\frac32$，$E(X^2)=\\frac34+\\frac94=3$，故 $D(X)=3-\\frac94=\\frac34$。"),
        ("随机变量 $X\\sim B(6,\\frac13)$。求 $E(X)$ 与 $D(X)$。", "$E(X)=2,\\quad D(X)=\\frac43$", "$E(X)=np=2$，$D(X)=np(1-p)=6\\cdot\\frac13\\cdot\\frac23=\\frac43$。"),
        ("已知 $E(X)=1,D(X)=2$，令 $Y=3-2X$。求 $E(Y)$ 与 $D(Y)$。", "$E(Y)=1,\\quad D(Y)=8$", "$E(Y)=3-2E(X)=1$，$D(Y)=(-2)^2D(X)=8$。"),
        ("随机变量 $X,Y$ 相互独立，且 $E(X)=5,E(Y)=2,D(X)=1,D(Y)=3$。求 $Z=X-Y$ 的期望与方差。", "$E(Z)=3,\\quad D(Z)=4$", "$E(Z)=E(X)-E(Y)=3$；独立时 $D(X-Y)=D(X)+D(Y)=4$。"),
    ]
    return _nonchoice_exact_blueprint(request, "expectation_variance", "solution", rows)


def _safety_blueprint(request: dict[str, Any], question_type: str) -> dict[str, Any] | None:
    return (
        _inequality_challenge_choice_blueprint(request, question_type)
        or _inequality_solution_blueprint(request, question_type)
        or _inequality_fill_blueprint(request, question_type)
        or _monotonicity_blueprint(request, question_type)
        or _inequality_blueprint(request, question_type)
        or _extremum_blueprint(request, question_type)
        or _extremum_other_blueprint(request, question_type)
        or _derivative_blueprint(request, question_type)
        or _total_probability_blueprint(request, question_type)
        or _tangent_blueprint(request, question_type)
        or _parity_periodicity_blueprint(request, question_type)
        or _derivative_definition_blueprint(request, question_type)
        or _optimum_blueprint(request, question_type)
        or _zeros_blueprint(request, question_type)
        or _expectation_variance_blueprint(request, question_type)
    )


def _select_safety_blueprint(
    bank: Path,
    request: dict[str, Any],
    question_type: str,
) -> dict[str, Any] | None:
    """Assign a globally unused exact family, then fall back to model drafting."""
    blueprint = _safety_blueprint(request, question_type)
    if not blueprint:
        return None

    family_id = str(blueprint.get("family_id") or "")
    family_match = re.fullmatch(r"(.+)-(\d+)", family_id)
    if not family_match:
        return None if _difficulty_gate_error(
            request["difficulty"],
            question_type,
            blueprint.get("stem_markdown"),
            blueprint.get("solution_markdown"),
            target_level=_strict_target_level(request),
        ) else blueprint
    family_prefix = family_match.group(1)
    used_families: set[int] = set()
    for row in _history_rows(bank):
        previous = row.get("question") or {}
        if str(row.get("student_id") or "") != request["student_id"]:
            continue
        if str(row.get("knowledge") or "") != request["knowledge"]:
            continue
        if str(previous.get("question_type") or "") != question_type:
            continue
        previous_family = str((previous.get("generation") or {}).get("safety_blueprint_family") or "")
        previous_match = re.fullmatch(rf"{re.escape(family_prefix)}-(\d+)", previous_family)
        if not previous_match:
            continue
        used_families.add(int(previous_match.group(1)))

    diversity_round = max(1, int(request.get("diversity_round") or 1))
    batch_size = max(1, int(request.get("batch_size") or 1))
    slot_index = int(request.get("slot_index") or 0)
    family_start = (diversity_round - 1) * batch_size + slot_index
    family_count = SAFETY_FAMILY_COUNTS.get(family_prefix, 10)
    family_start %= family_count
    rejection_rows = [
        row
        for row in _rejection_rows(bank)
        if str(row.get("student_id") or "") == request["student_id"]
        and str(row.get("knowledge") or "") == request["knowledge"]
        and str(row.get("question_type") or "") == question_type
    ]
    # A verifier-policy change must not permanently exhaust exact families.
    # Keep old records for audit, but only let safe-blueprint rejections from
    # the current mathematical-verification policy suppress a proven family.
    active_rejection_rows = [
        row
        for row in rejection_rows
        if not str(row.get("safety_blueprint_family") or "")
        or str(row.get("verification_policy_version") or "")
        == SAFE_BLUEPRINT_VERIFICATION_POLICY_VERSION
    ]
    rejected_structures = {
        str(row.get("structure") or "") for row in active_rejection_rows if row.get("structure")
    }
    rejected_math_structures = {
        str(row.get("math_structure") or "")
        for row in active_rejection_rows
        if row.get("math_structure")
    }
    reserved_families = _batch_reserved_structure_keys(
        str(request.get("batch_id") or ""),
        int(request.get("slot_index") or 0),
    )
    def family_is_available(family: int) -> bool:
        if family in used_families:
            return False
        candidate = _safety_blueprint({**request, "_family_override": family}, question_type)
        if not candidate:
            return False
        if str(candidate.get("family_id") or "") in reserved_families:
            return False
        return (
            _number_agnostic_structure(candidate.get("stem_markdown")) not in rejected_structures
            and _math_structure(candidate.get("stem_markdown")) not in rejected_math_structures
            and not _difficulty_gate_error(
                request["difficulty"],
                question_type,
                candidate.get("stem_markdown"),
                candidate.get("solution_markdown"),
                target_level=_strict_target_level(request),
            )
        )
    available_families = [
        (family_start + offset) % family_count
        for offset in range(family_count)
        if family_is_available((family_start + offset) % family_count)
    ]
    if not available_families:
        return None
    selected_family = available_families[0]
    return _safety_blueprint({**request, "_family_override": selected_family}, question_type)


def _seal_safety_blueprint(draft: dict[str, Any], blueprint: dict[str, Any] | None) -> None:
    """Prevent a model from drifting away from an exact, proven scaffold."""
    if not blueprint:
        return
    for key in ("question_type", "stem_markdown", "options", "answer", "solution_markdown", "changed_dimensions"):
        draft[key] = blueprint[key]
    draft["_safety_blueprint_family"] = str(blueprint.get("family_id") or "")
    draft["_verification_policy_version"] = SAFE_BLUEPRINT_VERIFICATION_POLICY_VERSION
    draft["_draft_source"] = "skill_safety_blueprint"
    if not draft.get("design_summary"):
        draft["design_summary"] = "依据出题任务单，以单道母题锚定结构，并切换到可精确校验的新函数族。"


def _dynamic_structure_recipe(
    request: dict[str, Any],
    question_type: str = "",
) -> dict[str, str]:
    base_index = (
        (max(1, int(request.get("diversity_round") or 1)) - 1)
        * max(1, int(request.get("batch_size") or 1))
        + int(request.get("slot_index") or 0)
    )
    index = base_index + (max(1, int(request.get("attempt") or 1)) - 1) * 97
    object_count = len(DYNAMIC_STRUCTURE_OBJECTS)
    transform_count = len(DYNAMIC_CONDITION_TRANSFORMS)
    proof_count = len(DYNAMIC_PROOF_ROUTES)
    knowledge = str(request.get("knowledge") or "")
    if "全概率公式" in knowledge:
        contexts = (
            "三个互斥来源与一个观测事件",
            "两阶段检测或筛选流程",
            "不同通道的成功率与来源比例",
            "分层抽样后观察到某结果",
            "带一个未知分支概率的混合模型",
        )
        transforms = (
            "先求观测事件的全概率，再求给定观测后的来源概率",
            "先由全概率得到总体比例，再反推一个分支参数",
            "比较两种方案的总体成功率，再作条件概率判断",
            "把树状分支改写为互斥事件分解并核对概率和为 1",
        )
        routes = (
            "列出完备事件组，逐分支相乘求和，再用联合概率除以总概率",
            "建立全概率方程求未知量，再把结果代回另一条件概率",
            "分别计算两个方案的全概率，并用精确分数比较",
        )
        requires_linked_parts = (
            question_type == "solution"
            and str(request.get("difficulty") or "auto") in {"matched", "challenge"}
        )
        return {
            "recipe_id": f"total-probability-{index % (len(contexts) * len(transforms) * len(routes)):03d}",
            "mathematical_object": contexts[index % len(contexts)],
            "condition_transform": transforms[(index // len(contexts)) % len(transforms)],
            "proof_route": routes[(index // (len(contexts) * len(transforms))) % len(routes)],
            "contract": (
                "必须在题干中明确写出（1）（2）两个相互依赖的问题：（1）用全概率公式求总体概率；"
                "（2）必须使用第（1）问结果求后验概率或反推参数。两问都要有精确答案与推导，禁止只改场景和百分数。"
                if requires_linked_parts
                else "分支必须互斥且完备，所有概率位于 [0,1]，答案使用精确分数并逐分支核算。"
            ),
        }
    if "期望" in knowledge or "方差" in knowledge:
        contexts = (
            "离散分布列与线性变换",
            "二项分布与一次收益函数",
            "两个独立随机变量的和或差",
            "含未知概率的三点分布",
            "先条件化再求总期望的两阶段模型",
        )
        routes = (
            "先核对概率和并求期望，再由二阶矩求方差",
            "先由给定矩确定未知概率，再求线性变换后的数字特征",
            "先分条件求期望，再使用全期望公式汇总",
        )
        return {
            "recipe_id": f"expectation-variance-{index % (len(contexts) * len(routes)):03d}",
            "mathematical_object": contexts[index % len(contexts)],
            "condition_transform": "至少改变随机变量取值结构、条件组织或所求变换中的两项，禁止只换概率数字",
            "proof_route": routes[(index // len(contexts)) % len(routes)],
            "contract": "题面必须给全分布条件；逐项核算概率和、期望与二阶矩，独立性只能在题面明确给出后使用。",
        }
    if question_type == "solution" and ("不等式" in knowledge or "构造函数" in knowledge):
        safe_objects = (
            "参数化的指数函数减一次函数，驻点与最值必须精确",
            "正半轴上的对数函数减线性函数，极值点必须精确",
            "含 ln(1+x) 的差函数，导数分子可直接化为平方或单项式",
            "t ln t 型凸函数，等号点固定为 t=1",
            "指数函数的二阶差函数，按导数单调性逐层证明",
            "正变量的幂与倒数差函数，最小值点可精确定位",
        )
        safe_routes = (
            "构造原不等式两端之差，求唯一最小值并核对等号条件",
            "先完成变量代换，再用一阶导数证明差函数非负",
            "用二阶导数证明一阶导数单调，再由零点推出原函数非负",
            "先求含参数函数的精确最值，再把恒成立条件化为参数范围",
        )
        return {
            "recipe_id": f"inequality-solution-{index % (len(safe_objects) * len(safe_routes)):03d}",
            "mathematical_object": safe_objects[index % len(safe_objects)],
            "condition_transform": "只允许直接差函数、正变量代换或精确参数最值；定义域在题面一次写全",
            "proof_route": safe_routes[(index // len(safe_objects)) % len(safe_routes)],
            "contract": (
                "必须是无 A/B/C/D 的完整解答题；先独立写出精确证明再组织题面。"
                "禁止 f(a-x)、f(c-x)、f(phi(x)) 等复合自变量比较，禁止数值试根，"
                "并明确写出定义域、导数符号、最值点与等号条件。"
            ),
        }
    return {
        "recipe_id": f"dynamic-{index % (object_count * transform_count * proof_count):03d}",
        "mathematical_object": DYNAMIC_STRUCTURE_OBJECTS[index % object_count],
        "condition_transform": DYNAMIC_CONDITION_TRANSFORMS[(index // object_count) % transform_count],
        "proof_route": DYNAMIC_PROOF_ROUTES[(index // (object_count * transform_count)) % proof_count],
        "contract": "三项都必须落实到题面与最终解析；若组合不相容，保留 proof_route 并更换数学对象，禁止回退到最近失败题。",
    }


def _feedback_recovery_contract(
    request: dict[str, Any],
    question_type: str,
    feedback: list[str],
) -> dict[str, Any]:
    text = "；".join(str(value) for value in feedback if str(value).strip())
    if not text:
        return {"active": False}
    rules = ["必须更换数学对象、条件组织和推理路径，不得只换数字或背景名词"]
    if "一步基础题" in text or "难度" in text:
        if "全概率公式" in str(request.get("knowledge") or "") and question_type == "solution":
            rules.append("题干必须含（1）（2）：先求全概率，再用该结果求后验概率或反推参数")
        else:
            rules.append("增加一个与首步结论相互依赖的新任务，使最终答案至少经过两段非机械推理")
    if "一致答案" in text or "答案" in text:
        rules.append("先用两种独立路线复算到同一精确答案，再反向编写题面；禁止小数近似")
    if "JSON" in text or "缺少" in text:
        rules.append("严格逐字段返回 required_json，非选择题的 options 必须为空对象")
    return {"active": True, "previous_failure": text[:600], "mandatory_changes": rules}


def _generation_messages(
    request: dict[str, Any],
    slot: dict[str, Any],
    references: list[dict[str, Any]],
    avoid_stems: list[str],
    feedback: list[str],
    exact_blueprint: dict[str, Any] | None = None,
    task_spec: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    focus = "智能匹配" if request["focus"] == "auto" else request["focus"]
    attempt = max(1, int(request.get("attempt") or 1))
    question_type = str(slot.get("question_type") or "")
    variation_index = int(request.get("slot_index") or 0) + (attempt - 1) * 2
    variation_profile = VARIATION_PROFILES[variation_index % len(VARIATION_PROFILES)]
    system = (
        "你是高中数学新题生成器，只返回 JSON 对象。generation_task_spec 是唯一出题指令。"
        "你只能使用 single_anchor_question_teacher_only 中的一道完整母题作为结构参照；"
        "diagnostic_evidence_summaries 只用于理解学情，不含题干，也绝对不得被拼接成新题。"
        "至少改变母题两个维度且包含结构性变化；禁止复制、同义改写、只换数字或融合多道原题。"
        "题面必须信息完备且可独立求解，答案唯一，解析简洁完整。"
        "输出前必须在内部从题面重做一遍并核对提问对象、定义域、边界与答案；自检失败就重新设计。"
        "只输出最终成题和最终推导，禁止在 solution_markdown 中记录试算、猜测、自我反驳或重新审视过程。"
        "如果用户载荷含 exact_safety_blueprint，必须原样保留其中的题干、系数、定义域、选项和答案，只可润色解析。"
        "不要输出 Markdown 标题或代码围栏。\n\n"
        "以下是当前生题 Skill 的正式流程与硬门，必须逐条遵守：\n"
        + _personal_skill_rules()
    )
    user_payload = {
        "task": "生成一道全新的、可独立求解的高中数学题",
        "generation_mode": request["mode"],
        "student_profile": (
            {"student_id": request["student_id"], "name": request["student_name"]}
            if request["mode"] == "student"
            else None
        ),
        "target": {
            "knowledge": request["knowledge"],
            "question_type": slot.get("question_type"),
            "difficulty": DIFFICULTY_LABELS[request["difficulty"]],
            "target_level": _request_target_level(request),
            "difficulty_contract": _difficulty_contract(
                request["difficulty"],
                question_type,
                _strict_target_level(request),
            ),
            "training_focus": focus,
            "diagnostic_evidence_count": request["reference_count"],
            "batch_slot": int(request.get("slot_index") or 0) + 1,
            "attempt": max(1, int(request.get("attempt") or 1)),
            "diversity_round": max(1, int(request.get("diversity_round") or 1)),
            "required_variation_profile": variation_profile,
        },
        "generation_task_spec": task_spec or {},
        "single_anchor_question_teacher_only": _compact_reference(references[0]),
        "diagnostic_evidence_summaries": [
            _diagnostic_evidence_summary(row) for row in references
        ],
        "recent_generated_stems_to_avoid": avoid_stems,
        "previous_attempt_feedback": feedback,
        "failed_attempt_recovery": _feedback_recovery_contract(request, question_type, feedback),
        "required_structure_recipe": _dynamic_structure_recipe(request, question_type),
        "stability_recovery": (
            {
                "active": True,
                "contract": (
                    "前序候选未通过。改用低歧义、闭式可验证的新结构：优先选择导数可直接因式分解、"
                    "驻点和边界可精确求出的函数；先完整求解，再反向编写题面与选项。"
                ),
                "forbidden": (
                    "禁止未经证明的复合函数恒成立条件、定义域映射不闭合、数值试根、"
                    "把题设结论直接改写成选项、或在解析中自我反驳。"
                ),
            }
            if attempt >= 2
            else {"active": False}
        ),
        "regeneration_contract": (
            "这是再次生成。必须更换函数族或数学对象、条件组织和关键推理路径；"
            "不得沿用上一题结构后只改数字、字母、区间端点或选项顺序。"
            if int(request.get("diversity_round") or 1) > 1
            else "首次生成也必须具有结构性变化，禁止照搬单道结构母题。"
        ),
        "mathematical_safety_constraints": _knowledge_safety_constraints(request["knowledge"], question_type),
        "exact_safety_blueprint": exact_blueprint,
        "required_json": {
            "question_type": "必须与 target.question_type 一致",
            "stem_markdown": "完整新题题干；选择题需在题干末尾列出 A/B/C/D 选项；不要使用 Markdown 标题",
            "options": {"A": "选择题选项；非选择题返回空对象"},
            "answer": (
                "只返回一个 A-D 字母"
                if question_type == "single_choice"
                else "返回按 A-D 顺序排列的 2–4 个正确选项字母，不加分隔符"
                if question_type == "multiple_choice"
                else "只返回答案内容；禁止带‘答案：’、Markdown 加粗、代码围栏或数学定界符"
            ),
            "solution_markdown": "只写最终正确推导，不得出现试错或自我修正；不超过 350 个中文字符",
            "changed_dimensions": (
                "字符串数组；至少两个不同值，且至少一个来自 question_angle、condition_organization、"
                "representation、context、reasoning_path"
            ),
            "design_summary": "说明如何遵循任务单并改变单道母题结构；不得泄露原题；不超过 120 个中文字符",
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _verification_messages(
    question_type: str,
    stem: str,
    options: Any,
    *,
    target_difficulty: str = "auto",
    target_level: int | None = None,
    knowledge: str = "",
    enforce_difficulty: bool = True,
) -> list[dict[str, str]]:
    inequality_parameter_guard = (
        "参数恒成立题必须先写成对定义域内每个自变量都成立的参数约束："
        "若得到 a≤G(x)，应取 G 在整个定义域的下确界；若得到 a≥G(x)，应取上确界。"
        "必须比较区间两端、极限与内部驻点，禁止把上确界和下确界写反。"
        if "不等式" in knowledge or "构造函数" in knowledge
        else ""
    )
    choice_answer_contract = (
        "恰好一个 A-D 字母"
        if question_type == "single_choice"
        else "按 A-D 顺序排列的 2–4 个正确选项字母；少于两个正确项必须 failed"
        if question_type == "multiple_choice"
        else (
            "题面最终所求的纯答案；若最后求参数的数值，只返回数值本身，"
            "不带变量名、题号或‘证明见解析’"
        )
        if question_type == "solution" and ("不等式" in knowledge or "构造函数" in knowledge)
        else "独立求出的纯答案"
    )
    difficulty_instruction = (
        "还必须核对题目是否达到 target_difficulty：挑战题若只是一步配方、一次求导或简单二次函数最值，"
        "即使答案正确也必须返回 failed；巩固题若出现长分类和多层综合也必须返回 failed。"
        "挑战合同中的参数/范围、区间分类、复合或超越结构、恒成立证明、多问联动、高阶分析是择三信号，"
        "不是每项都必须同时出现；参数最值→恒成立等价→第二个参数函数判定这类三段联动应按挑战题计算，"
        "不得仅因没有分类讨论或二阶导数而降为基础题。"
        if enforce_difficulty
        else (
            "本次题面来自已证明的安全蓝图，难度与训练目标由本地硬门禁另行校验。"
            "你只按数学正确性、定义域、边界、唯一答案和题面自洽性判定 status；"
            "不得因为主观认为题目不够难、缺少某一种方法、分类讨论或推理段数而返回 failed。"
        )
    )
    payload = {
        "question_type": question_type,
        "knowledge": knowledge,
        "target_difficulty": (
            (
                f"{target_level}级"
                if target_level is not None
                else DIFFICULTY_LABELS.get(target_difficulty, target_difficulty)
            )
            if enforce_difficulty
            else "由本地硬门禁校验"
        ),
        "difficulty_contract": (
            _difficulty_contract(target_difficulty, question_type, target_level)
            if enforce_difficulty
            else "本轮不作主观难度裁决，只复核数学正确性"
        ),
        "verification_scope": (
            "mathematics_and_difficulty" if enforce_difficulty else "mathematical_correctness_only"
        ),
        "stem_markdown": stem,
        "options": options if isinstance(options, dict) else {},
        "required_json": {
            "status": "passed 或 failed；数学成立时必须为 passed，且必须与 notes 的结论一致",
            "answer": f"passed 时返回{choice_answer_contract}；failed 时返回空字符串；不要带答案标签或 Markdown 加粗",
            "solution_markdown": (
                "只写最终正确推导；选择题逐项核对并以‘故选 X’收尾，且 X 必须与 answer 完全一致；"
                "failed 时只写最关键的失败依据；不超过 350 个中文字符"
            ),
            "notes": "定义域、边界、唯一性或题面问题检查；不超过 120 个中文字符",
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是独立高中数学审题员。不要猜测出题者答案，只根据题面重新求解。"
                "只输出最终结论，禁止记录试算、猜测、自我反驳或重新审视过程。"
                + difficulty_instruction
                + "status、answer、solution_markdown 与 notes 必须互相一致；若 notes 认定题目数学成立，"
                "status 必须为 passed 并填写答案，禁止一边写成立一边返回 failed。"
                "必须核对定义域、极值充分性、选项数量和解析最终结论；若无法简洁严格地得到唯一答案，"
                + inequality_parameter_guard
                + "立即返回 status=failed 并给出最关键原因。只返回 JSON 对象。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _clean_markdown_body(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text).strip()


def _choice_answer_labels(value: Any) -> tuple[str, ...]:
    """Return canonical A-D labels from common Chinese choice-answer forms."""
    text = _clean_answer_content(value).upper()
    found = set(re.findall(r"[A-D]", text))
    return tuple(label for label in "ABCD" if label in found)


def _solution_conclusion_labels(value: Any) -> tuple[str, ...]:
    """Extract only explicit positive/final option conclusions from a solution."""
    text = _clean_markdown_body(value).upper()
    found: set[str] = set()
    final_patterns = (
        r"(?:故选|应选|答案(?:为|是)?|正确选项(?:为|是)?|综上(?:应选|选择))\s*[:：]?\s*"
        r"([A-D](?:\s*[、,，/及和与]\s*[A-D]|\s*[A-D])*)",
        r"(?<![A-Z])([A-D])\s*(?:项|选项)?\s*(?:正确|成立|符合题意|应选)",
    )
    for pattern in final_patterns:
        for match in re.finditer(pattern, text):
            found.update(_choice_answer_labels(match.group(1)))
    return tuple(label for label in "ABCD" if label in found)


def _answers_match_for_type(first: Any, second: Any, question_type: str, knowledge: str = "") -> bool:
    if question_type in {"single_choice", "multiple_choice"}:
        left, right = _choice_answer_labels(first), _choice_answer_labels(second)
        return bool(left) and left == right
    return _answers_match(first, second, knowledge)


def _verification_consistency_error(
    question_type: str,
    options: Any,
    answer: Any,
    solution: Any,
) -> str:
    if question_type not in {"single_choice", "multiple_choice"}:
        return ""
    if not isinstance(options, dict):
        return "独立审题发现选择题选项格式无效"
    option_keys = {str(key).strip().upper() for key in options if str(key).strip()}
    if option_keys != set("ABCD") or any(not str(value).strip() for value in options.values()):
        return "独立审题发现选择题必须包含完整且非空的 A/B/C/D 选项"
    normalised_options = [re.sub(r"\s+", "", str(value)) for value in options.values()]
    if len(set(normalised_options)) != len(normalised_options):
        return "独立审题发现选择题存在重复选项"
    labels = _choice_answer_labels(answer)
    if question_type == "single_choice" and len(labels) != 1:
        return "独立审题发现单选题答案不是唯一选项"
    if question_type == "multiple_choice" and len(labels) < 2:
        return "独立审题发现多选题少于两个正确选项"
    if any(label not in option_keys for label in labels):
        return "独立审题答案引用了不存在的选项"
    conclusion_labels = _solution_conclusion_labels(solution)
    if conclusion_labels and conclusion_labels != labels:
        return "独立审题返回的答案与其解析最终结论矛盾"
    return ""


def _normalise_verification_fields(verification: dict[str, Any]) -> None:
    if not verification.get("answer"):
        verification["answer"] = verification.get("correct_answer") or verification.get("final_answer")
    if not verification.get("solution_markdown"):
        verification["solution_markdown"] = (
            verification.get("solution") or verification.get("analysis") or verification.get("explanation")
        )
    if not verification.get("status") and isinstance(verification.get("valid"), bool):
        verification["status"] = "passed" if verification["valid"] else "failed"


def _verification_solution_process_error(solution: Any) -> str:
    """Keep hidden trial-and-error or self-correction out of delivered explanations."""
    text = str(solution or "")
    markers = (
        "重新分析",
        "重新审视",
        "重新计算",
        "此前误算",
        "前面误算",
        "修正为",
        "实为",
        "但验证",
    )
    found = next((marker for marker in markers if marker in text), "")
    return f"独立解析包含试错或自我修正过程（{found}），必须只返回最终推导" if found else ""


def _safe_blueprint_verification_error(
    verification: dict[str, Any],
    draft: dict[str, Any],
    question_type: str,
    knowledge: str,
) -> str:
    """Require a real Cherry pass while tolerating one malformed verifier response."""
    _normalise_verification_fields(verification)
    if str(verification.get("status") or "") != "passed":
        return f"独立审题未通过：{verification.get('notes') or '未给出可验证结论'}"
    if not verification.get("answer"):
        return "独立审题通过但漏返回答案"
    if not verification.get("solution_markdown"):
        verification["solution_markdown"] = draft.get("solution_markdown")
        verification["notes"] = (
            f"{str(verification.get('notes') or '').strip()}；独立答案一致，解析沿用已证明蓝图。"
        ).strip("；")
    if not verification.get("solution_markdown"):
        return "独立审题通过但漏返回解析"
    initial_answer_matches_blueprint = _answers_match_for_type(
        draft.get("answer"),
        verification.get("answer"),
        question_type,
        knowledge,
    )
    process_error = _verification_solution_process_error(verification.get("solution_markdown"))
    if process_error:
        if not initial_answer_matches_blueprint:
            return process_error
        # The verifier has independently reached the proven answer, but some
        # models still narrate a discarded attempt despite the JSON contract.
        # Keep the independent answer as evidence and publish the exact,
        # teacher-safe blueprint proof instead of rejecting a correct question
        # for a presentation-only defect.
        verification["solution_markdown"] = draft.get("solution_markdown")
        verification["notes"] = (
            f"{str(verification.get('notes') or '').strip()}；独立答案一致，"
            "解析已用无试错过程的已证明蓝图收口。"
        ).strip("；")
    if question_type in {"single_choice", "multiple_choice"}:
        answer_labels = _choice_answer_labels(verification.get("answer"))
        conclusion_labels = _solution_conclusion_labels(verification.get("solution_markdown"))
        if conclusion_labels and conclusion_labels != answer_labels:
            verification["answer"] = "".join(conclusion_labels)
            verification["_answer_recovered_from_solution"] = True
            verification["notes"] = (
                f"{str(verification.get('notes') or '').strip()}；JSON 答案字段与最终推导不一致，"
                "已按解析末尾明确结论收口。"
            ).strip("；")
    consistency_error = _verification_consistency_error(
        question_type,
        draft.get("options") or {},
        verification.get("answer"),
        verification.get("solution_markdown"),
    )
    if consistency_error:
        return consistency_error
    if not _answers_match_for_type(
        draft.get("answer"),
        verification.get("answer"),
        question_type,
        knowledge,
    ):
        verifier_answer = _clean_answer_content(verification.get("answer"))[:120]
        blueprint_answer = _clean_answer_content(draft.get("answer"))[:120]
        return (
            "独立审题答案与已证明蓝图答案不一致："
            f"Cherry={verifier_answer or '空'}；蓝图={blueprint_answer or '空'}"
        )
    return ""


def _question_from_draft(
    config: GatewayConfig,
    request: dict[str, Any],
    slot: dict[str, Any],
    references: list[dict[str, Any]],
    draft: dict[str, Any],
    verification: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    question_type = str(draft.get("question_type") or "").strip()
    if question_type != str(slot.get("question_type") or ""):
        raise LiveGenerationError("模型返回的题型与目标题型不一致")
    stem = _clean_markdown_body(draft.get("stem_markdown"))
    answer = _clean_answer_content(draft.get("answer"))
    solution = _clean_markdown_body(draft.get("solution_markdown"))
    independent_answer = _clean_answer_content(verification.get("answer"))
    independent_solution = _clean_markdown_body(verification.get("solution_markdown"))
    blueprint_family = str(draft.get("_safety_blueprint_family") or draft.get("family_id") or "")
    safety_blueprint = bool(blueprint_family)
    draft_source = "skill_safety_blueprint" if safety_blueprint else "cherry_studio_model"
    if safety_blueprint and str(verification.get("status") or "") == "passed" and independent_answer and not independent_solution:
        independent_solution = solution
    if not all((stem, answer, solution)):
        raise LiveGenerationError("生成草稿的题面、答案或解析不完整")
    if str(verification.get("status") or "") != "passed":
        raise LiveGenerationError(f"独立审题未通过：{verification.get('notes') or '题面或答案存在问题'}")
    if not all((independent_answer, independent_solution)):
        raise LiveGenerationError("独立审题通过但漏返回答案或解析")
    consistency_error = _verification_consistency_error(
        question_type,
        draft.get("options") or {},
        independent_answer,
        independent_solution,
    )
    if consistency_error:
        raise LiveGenerationError(consistency_error)
    answer_matches = _answers_match_for_type(answer, independent_answer, question_type, request["knowledge"])
    answer_corrected = not answer_matches
    if answer_corrected and verification.get("_answer_consensus") is not True:
        raise LiveGenerationError("出题答案与独立求解答案不一致")
    if question_type in {"single_choice", "multiple_choice"}:
        answer = "".join(_choice_answer_labels(independent_answer))
    elif answer_corrected:
        answer = independent_answer
    dimensions = [str(value) for value in draft.get("changed_dimensions") or [] if str(value)]
    if len(set(dimensions)) < 2 or not STRUCTURAL_DIMENSIONS.intersection(dimensions):
        raise LiveGenerationError("新题变化维度不足")
    final_difficulty_error = _difficulty_gate_error(
        request["difficulty"],
        question_type,
        stem,
        independent_solution,
        target_level=_strict_target_level(request),
    )
    if final_difficulty_error:
        raise LiveGenerationError(final_difficulty_error)
    target_level = _request_target_level(request)
    difficulty_evidence = _difficulty_gate_evidence(
        request["difficulty"],
        question_type,
        stem,
        independent_solution,
        target_level=target_level,
    )
    pedagogical_fingerprint = _pedagogical_fingerprint(
        knowledge=request["knowledge"],
        question_type=question_type,
        target_level=target_level,
        stem=stem,
        solution=independent_solution,
        options=draft.get("options") if isinstance(draft.get("options"), dict) else {},
        answer=answer,
    )
    now = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(
        f"{request['student_id']}:{request['knowledge']}:{now}:{stem}".encode("utf-8")
    ).hexdigest()[:14]
    reference_ids = [str(row["question_id"]) for row in references]
    task_spec = draft.get("_generation_task_spec") if isinstance(draft.get("_generation_task_spec"), dict) else {}
    anchor_question_id = str(
        draft.get("_anchor_question_id")
        or task_spec.get("diagnostic_anchor_question_id")
        or reference_ids[0]
    )
    structure_anchor = task_spec.get("structure_anchor") if isinstance(task_spec.get("structure_anchor"), dict) else {
        "kind": "mother_question",
        "id": anchor_question_id,
    }
    fulltext_anchor_count = 0 if structure_anchor.get("kind") == "skill_blueprint" else 1
    return {
        "question_id": f"gen_live_{request['student_id'].replace('-', '_')}_{digest}",
        "display_id": (
            "Skill 安全蓝图新题 · Cherry Studio 校验"
            if safety_blueprint
            else "Cherry Studio 模型实时新题"
        ),
        "is_generated": True,
        "student_visible": True,
        "scope": "student_practice" if request["mode"] == "student" else "knowledge_practice",
        "student_id": request["student_id"],
        "question_type": question_type,
        "question_type_label": QUESTION_TYPE_LABELS.get(question_type, question_type),
        "primary_knowledge": request["knowledge"],
        "stem_markdown": stem,
        "stem_html": "",
        "options": draft.get("options") if isinstance(draft.get("options"), dict) else {},
        "answer": answer,
        "solution_markdown": independent_solution,
        "solution_html": "",
        "personalization": {
            "difficulty_key": request["difficulty"],
            "target_level": _request_target_level(request),
            "difficulty_label": f"{_request_target_level(request)}级",
            "training_focus": "智能匹配" if request["focus"] == "auto" else request["focus"],
        },
        "generation": {
            "mother_question_id": anchor_question_id,
            "anchor_question_id": anchor_question_id,
            "reference_question_ids": reference_ids,
            "reference_count": len(reference_ids),
            "diagnostic_evidence_question_ids": reference_ids,
            "diagnostic_evidence_count": len(reference_ids),
            "fulltext_anchor_count": fulltext_anchor_count,
            "structure_anchor": structure_anchor,
            "strategy": "single_anchor_task_spec",
            "task_spec": task_spec,
            "task_spec_id": str(task_spec.get("task_spec_id") or ""),
            "mode": request["mode"],
            "target_question_type": question_type,
            "question_type_assignment": (
                "auto_matched" if request["question_type"] == "auto" else "teacher_selected"
            ),
            "question_type_explanation": (
                f"智能匹配自动分配为{QUESTION_TYPE_LABELS.get(question_type, question_type)}"
                if request["question_type"] == "auto"
                else f"按教师选择生成{QUESTION_TYPE_LABELS.get(question_type, question_type)}"
            ),
            "changed_dimensions": dimensions,
            "pedagogical_fingerprint": pedagogical_fingerprint,
            "relative_difficulty": request["difficulty"],
            "novelty_review": "passed",
            "generator": (
                "smart-question-recommender-safety-blueprint-v1"
                if safety_blueprint
                else "cherry-studio-api-gateway-live-v1"
            ),
            "draft_source": draft_source,
            "model": config.model,
            "model_role": "verifier" if safety_blueprint else "draft_and_verifier",
            "diversity_round": max(1, int(request.get("diversity_round") or 1)),
            "structural_novelty": "passed",
            "generated_at": now,
            "attempt": attempt,
            "design_summary": str(draft.get("design_summary") or "").strip(),
            "safety_blueprint_family": blueprint_family,
            "verification_policy_version": (
                SAFE_BLUEPRINT_VERIFICATION_POLICY_VERSION if safety_blueprint else "model-full-review-v1"
            ),
            "draft_answer_corrected": answer_corrected,
            "deterministic_probability_check": draft.get("_deterministic_probability_check") or None,
        },
        "verification": {
            "checked_from_stem_only": True,
            "independent_answer": independent_answer,
            "answer_matches": True,
            "status": "passed",
            "notes": str(verification.get("notes") or "").strip(),
            "target_difficulty": {
                "key": request["difficulty"],
                "level": target_level,
                "label": f"{target_level}级",
            },
            "difficulty_matches": not bool(
                _difficulty_gate_error(
                    request["difficulty"],
                    question_type,
                    stem,
                    independent_solution,
                    target_level=_strict_target_level(request),
                )
            ),
            "estimated_level": target_level,
            "difficulty_gate_evidence": difficulty_evidence,
            "answer_solution_consistency": "passed",
            "consensus_checks": int(verification.get("_consensus_checks") or 1),
            "model": config.model,
            "provider": "cherry-studio-api-gateway",
            "scope": (
                "mathematical_correctness_only" if safety_blueprint else "mathematics_and_difficulty"
            ),
        },
        "teacher_review": {"status": "pending"},
    }


def _history_similarity_errors(bank: Path, question: dict[str, Any]) -> list[str]:
    stem = _normalise_text(question.get("stem_markdown"))
    structure = _number_agnostic_structure(question.get("stem_markdown"))
    math_structure = _math_structure(question.get("stem_markdown"))
    family = str((question.get("generation") or {}).get("safety_blueprint_family") or "")
    generation = question.get("generation") or {}
    current_fingerprint = generation.get("pedagogical_fingerprint")
    if not isinstance(current_fingerprint, dict):
        current_fingerprint = _pedagogical_fingerprint(
            knowledge=str(question.get("primary_knowledge") or ""),
            question_type=str(question.get("question_type") or ""),
            target_level=int((question.get("personalization") or {}).get("target_level") or 3),
            stem=question.get("stem_markdown"),
            solution=question.get("solution_markdown"),
            options=question.get("options") if isinstance(question.get("options"), dict) else {},
            answer=question.get("answer"),
        )
    current_fingerprint_key = _pedagogical_fingerprint_key(current_fingerprint)
    errors = []
    for row in _history_rows(bank):
        previous = row.get("question") or {}
        if str(previous.get("student_id") or "") != str(question.get("student_id") or ""):
            continue
        if str(previous.get("primary_knowledge") or "") != str(question.get("primary_knowledge") or ""):
            continue
        if str(previous.get("question_type") or "") != str(question.get("question_type") or ""):
            continue
        previous_generation = previous.get("generation") or {}
        previous_fingerprint = previous_generation.get("pedagogical_fingerprint")
        if not isinstance(previous_fingerprint, dict):
            previous_fingerprint = _pedagogical_fingerprint(
                knowledge=str(previous.get("primary_knowledge") or ""),
                question_type=str(previous.get("question_type") or ""),
                target_level=int((previous.get("personalization") or {}).get("target_level") or 3),
                stem=previous.get("stem_markdown"),
                solution=previous.get("solution_markdown"),
                options=previous.get("options") if isinstance(previous.get("options"), dict) else {},
                answer=previous.get("answer"),
            )
        previous_stem = _normalise_text(previous.get("stem_markdown"))
        if not stem or not previous_stem:
            continue
        previous_structure = _number_agnostic_structure(previous.get("stem_markdown"))
        previous_math_structure = _math_structure(previous.get("stem_markdown"))
        if structure == previous_structure or (
            math_structure and previous_math_structure and math_structure == previous_math_structure
        ):
            errors.append(
                f"与历史生成题 {previous.get('question_id') or 'unknown'} 结构相同，仅替换数字或表面措辞"
            )
            break
        if current_fingerprint_key and current_fingerprint_key == _pedagogical_fingerprint_key(previous_fingerprint):
            errors.append(
                f"与历史生成题 {previous.get('question_id') or 'unknown'} 教学结构指纹相同，必须更换方法族、任务意图或推理路径"
            )
            break
        previous_family = str((previous.get("generation") or {}).get("safety_blueprint_family") or "")
        if family and previous_family and family != previous_family:
            continue
        ratio = 1.0 if stem == previous_stem else SequenceMatcher(None, stem, previous_stem).ratio()
        threshold = 1.0 if family and previous_family == family else (0.98 if family else 0.88)
        if ratio >= threshold:
            errors.append(f"与历史生成题 {previous.get('question_id') or 'unknown'} 过于相似（{ratio:.3f}）")
            break
    return errors


def _append_history(bank: Path, request: dict[str, Any], references: list[dict[str, Any]], question: dict[str, Any]) -> None:
    path = _generation_history_path(bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "live-personalized-generation-record-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "delivery_status": "pending" if request.get("defer_history_commit") else "committed",
        "student_id": request["student_id"],
        "student_name": request["student_name"],
        "knowledge": request["knowledge"],
        "request": request,
        "reference_questions": references,
        "question": question,
    }
    with HISTORY_LOCK, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rewrite_history_rows_locked(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def commit_personalized_batch(bank: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Commit any verified 1–5 question subset; repeated calls are idempotent."""
    if not isinstance(payload, dict):
        raise LiveGenerationError("整批提交参数格式不正确")
    raw_batch_ids = payload.get("batch_ids") or []
    raw_question_ids = payload.get("question_ids") or []
    if not isinstance(raw_batch_ids, list) or not isinstance(raw_question_ids, list):
        raise LiveGenerationError("整批提交缺少批次或题目列表")
    batch_ids = {_clean_batch_id(value) for value in raw_batch_ids if _clean_batch_id(value)}
    question_ids = {str(value or "").strip() for value in raw_question_ids if str(value or "").strip()}
    try:
        expected_count = int(payload.get("expected_count") or 0)
    except (TypeError, ValueError) as exc:
        raise LiveGenerationError("整批提交数量不正确") from exc
    if expected_count not in range(1, 6) or len(question_ids) != expected_count or not batch_ids:
        raise LiveGenerationError("整批提交题目数、题目 ID 或批次 ID 不完整")
    path = _generation_history_path(bank.resolve())
    if not path.exists():
        raise LiveGenerationError("整批生成历史不存在，无法提交")
    committed_at = datetime.now(timezone.utc).isoformat()
    with HISTORY_LOCK:
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        matching = [
            row
            for row in rows
            if str((row.get("request") or {}).get("batch_id") or "") in batch_ids
            and str((row.get("question") or {}).get("question_id") or "") in question_ids
            and str(row.get("delivery_status") or "") in {"pending", "committed"}
        ]
        matched_ids = {str((row.get("question") or {}).get("question_id") or "") for row in matching}
        if len(matching) != expected_count or matched_ids != question_ids:
            raise LiveGenerationError(
                f"整批提交校验失败：应有 {expected_count} 道，实际找到 {len(matching)} 道"
            )
        changed = False
        for row in matching:
            if str(row.get("delivery_status") or "") == "pending":
                row["delivery_status"] = "committed"
                row["committed_at"] = committed_at
                changed = True
        if changed:
            _rewrite_history_rows_locked(path, rows)
    return {
        "status": "committed",
        "question_count": expected_count,
        "batch_ids": sorted(batch_ids),
    }


def _discard_pending_batch_history(
    bank: Path,
    batch_id: str,
    preserve_question_ids: set[str] | None = None,
) -> int:
    path = _generation_history_path(bank.resolve())
    if not path.exists() or not batch_id:
        return 0
    preserve_question_ids = preserve_question_ids or set()
    with HISTORY_LOCK:
        rows: list[dict[str, Any]] = []
        removed = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            row_batch = str((row.get("request") or {}).get("batch_id") or "")
            question_id = str((row.get("question") or {}).get("question_id") or "")
            pending = str(row.get("delivery_status") or "committed") == "pending"
            if (
                pending
                and question_id not in preserve_question_ids
                and (row_batch == batch_id or row_batch.startswith(batch_id + "-recovery-"))
            ):
                removed += 1
                continue
            rows.append(row)
        if removed:
            _rewrite_history_rows_locked(path, rows)
    return removed


def _normalise_draft_fields(draft: dict[str, Any], question_type: str) -> None:
    if not draft.get("stem_markdown"):
        draft["stem_markdown"] = draft.get("stem") or draft.get("question") or draft.get("question_text")
    if not draft.get("answer"):
        draft["answer"] = draft.get("correct_answer") or draft.get("final_answer")
    if not draft.get("solution_markdown"):
        draft["solution_markdown"] = draft.get("solution") or draft.get("analysis") or draft.get("explanation")
    if not draft.get("question_type"):
        draft["question_type"] = question_type
    if isinstance(draft.get("options"), list):
        draft["options"] = {
            chr(65 + index): str(value)
            for index, value in enumerate(draft["options"][:4])
        }
    if str(draft.get("question_type") or "") in {"single_choice", "multiple_choice"} and not draft.get("options"):
        option_rows = re.findall(
            r"(?m)^\s*([A-D])\s*[.．、:：]\s*(.+?)\s*$",
            str(draft.get("stem_markdown") or ""),
        )
        if len(option_rows) >= 4:
            draft["options"] = dict(option_rows[:4])
    if isinstance(draft.get("changed_dimensions"), str):
        draft["changed_dimensions"] = [
            value.strip()
            for value in re.split(r"[,，;；\s]+", draft["changed_dimensions"])
            if value.strip()
        ]


def _draft_missing_fields(draft: dict[str, Any]) -> list[str]:
    missing = [key for key in ("stem_markdown", "answer", "solution_markdown") if not draft.get(key)]
    if str(draft.get("question_type") or "") in {"single_choice", "multiple_choice"} and not draft.get("options"):
        missing.append("options")
    dimensions = draft.get("changed_dimensions")
    if not isinstance(dimensions, list) or len({str(value) for value in dimensions if str(value)}) < 2:
        missing.append("changed_dimensions")
    return missing


def _question_type_shape_error(draft: dict[str, Any], question_type: str) -> str:
    """Reject obvious form violations before spending a verifier model call."""
    stem = str(draft.get("stem_markdown") or "")
    options = draft.get("options") if isinstance(draft.get("options"), dict) else {}
    option_labels = re.findall(r"(?m)^\s*([A-D])\s*[.．、:：]", stem)
    choice_wording = bool(re.search(r"下列.{0,18}(?:正确|错误|成立|不成立).{0,8}(?:是|有)|[（(]\s*[）)]", stem))
    if question_type == "solution" and (options or len(set(option_labels)) >= 2 or choice_wording):
        return "目标题型为解答题，但草稿仍含 A-D 选项或选择题措辞；必须改为完整主观题"
    if question_type == "fill_blank" and (options or len(set(option_labels)) >= 2):
        return "目标题型为填空题，但草稿仍含选择题选项"
    if question_type == "fill_blank" and not re.search(r"_{2,}|____|填空|空格", stem):
        return "填空题题面缺少明确空位"
    if question_type in {"single_choice", "multiple_choice"}:
        if set(options) != set("ABCD"):
            return "选择题必须提供 A、B、C、D 四个完整选项"
        if any(not str(options.get(label) or "").strip() for label in "ABCD"):
            return "选择题存在空选项"
    return ""


def _simple_numeric_latex_value(expression: str, x_value: float) -> float | None:
    """Evaluate a deliberately small one-variable LaTeX subset for counterexample gates."""
    text = _strip_latex_presentation(expression).replace("−", "-").strip()
    if not text or any(token in text for token in (r"\frac", r"\sqrt", r"\sin", r"\cos", r"\tan")):
        return None
    text = re.sub(r"\\ln\s*\(([^()]*)\)", r"log(\1)", text)
    text = re.sub(r"\\ln\s+([x0-9.]+)", r"log(\1)", text)
    text = re.sub(r"e\s*\^\s*\{([^{}]+)\}", r"exp(\1)", text)
    text = re.sub(r"e\s*\^\s*(x|[+-]?\d+(?:\.\d+)?)", r"exp(\1)", text)
    text = re.sub(r"x\s*\^\s*\{([^{}]+)\}", r"(x)**(\1)", text)
    text = re.sub(r"x\s*\^\s*([+-]?\d+(?:\.\d+)?)", r"(x)**(\1)", text)
    text = text.replace("^", "**")
    text = re.sub(r"(?<=\d)(?=x|exp|log|\()", "*", text)
    text = re.sub(r"(?<=x)(?=\d|exp|log|\()", "*", text)
    text = re.sub(r"(?<=\))(?=\d|x|exp|log|\()", "*", text)
    text = re.sub(r"\s+", "", text)
    identifiers = set(re.findall(r"[A-Za-z_]+", text))
    if not identifiers.issubset({"x", "exp", "log"}) or not re.fullmatch(r"[0-9A-Za-z_+\-*/().]+", text):
        return None
    try:
        value = eval(text, {"__builtins__": {}}, {"x": float(x_value), "exp": math.exp, "log": math.log})
        number = float(value)
    except (ArithmeticError, TypeError, ValueError, SyntaxError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _declared_equality_point_error(draft: dict[str, Any]) -> str:
    """Reject a claimed equality point when direct substitution disproves it."""
    stem = str(draft.get("stem_markdown") or "")
    equality_match = re.search(
        r"等号(?:仅)?(?:在|当)?[^。；\n]{0,35}?[xX]\s*=\s*\$?\s*([+-]?\d+(?:\.\d+)?)",
        stem,
    )
    if not equality_match:
        return ""
    x_value = float(equality_match.group(1))
    for function_name, expression in re.findall(
        r"([fgh])\s*\(\s*x\s*\)\s*=\s*([^$\n]+)",
        stem,
        flags=re.IGNORECASE,
    ):
        relation = re.search(
            rf"{re.escape(function_name)}\s*\(\s*x\s*\)\s*(?:\\geq|\\leq|≥|≤)\s*\$?\s*([+-]?\d+(?:\.\d+)?)",
            stem,
            flags=re.IGNORECASE,
        )
        if not relation:
            continue
        function_value = _simple_numeric_latex_value(expression, x_value)
        if function_value is None:
            continue
        claimed_value = float(relation.group(1))
        if not math.isclose(function_value, claimed_value, rel_tol=1e-7, abs_tol=1e-7):
            return (
                f"题面声明 x={x_value:g} 为等号点，但直接代入得 "
                f"{function_name}({x_value:g})≈{function_value:.6g}，不等于 {claimed_value:g}"
            )
    return ""


def _draft_completion_messages(
    draft: dict[str, Any],
    question_type: str,
    missing: list[str],
) -> list[dict[str, str]]:
    payload = {
        "task": "补全一个不完整的新题草稿，并重新独立求解核对；只返回完整 JSON 对象",
        "target_question_type": question_type,
        "missing_fields": missing,
        "current_draft": {key: value for key, value in draft.items() if not key.startswith("_")},
        "required_json": {
            "question_type": question_type,
            "stem_markdown": "信息完备的完整题面，选择题包含 A/B/C/D",
            "options": "选择题返回 A/B/C/D 对象，非选择题返回空对象",
            "answer": "唯一答案，不带答案标签或数学定界符",
            "solution_markdown": "最终正确推导，不写试错过程，不超过 350 个中文字符",
            "changed_dimensions": "至少两个结构变化维度",
            "design_summary": "不超过 120 个中文字符",
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是高中数学题目修复器。必须返回字段齐全、题面可独立求解、答案唯一的 JSON 对象。"
                "若当前题无法严格修复，换成同知识点但不同数学结构的新题。禁止输出 JSON 之外的文字。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def generate_personalized_draft(bank: Path, payload: dict[str, Any]) -> dict[str, Any]:
    bank = bank.resolve()
    request = _validate_request(payload)
    _ensure_batch_active(request["batch_id"])
    config = GatewayConfig.from_env()
    stage_deadline = time.monotonic() + min(MAX_STAGE_TIMEOUT_SECONDS, config.timeout_seconds)
    slot, references = _slot_and_references(
        bank,
        request["student_id"],
        request["knowledge"],
        request["reference_count"],
        mode=request["mode"],
        question_type=request["question_type"],
        slot_index=request["slot_index"],
    )
    attempt = max(1, min(5, int(payload.get("attempt") or 1)))
    request["attempt"] = attempt
    quality_feedback = _recent_quality_feedback(bank, request)
    feedback = [
        *[str(value)[:400] for value in payload.get("feedback") or [] if str(value).strip()][:2],
        *_feedback_prompt_lines(quality_feedback),
    ][:8]
    avoid_stems = [
        *_recent_avoid_stems(bank, request["student_id"], request["knowledge"]),
        *_batch_avoid_stems(request["batch_id"]),
    ]
    blueprint = _select_safety_blueprint(bank, request, str(slot.get("question_type") or ""))
    task_spec = _build_generation_task_spec(
        bank,
        request,
        slot,
        references,
        blueprint,
        quality_feedback=quality_feedback,
    )
    if blueprint:
        draft = {}
    else:
        draft = _gateway_chat_json(
            config,
            _generation_messages(
                request,
                slot,
                references,
                avoid_stems,
                feedback,
                blueprint,
                task_spec,
            ),
            temperature=min(0.85, 0.4 + 0.18 * attempt),
            max_tokens=2800,
            deadline=stage_deadline,
            batch_id=request["batch_id"],
        )
        draft["_draft_source"] = "cherry_studio_model"
    _ensure_batch_active(request["batch_id"])
    _seal_safety_blueprint(draft, blueprint)
    draft["_generation_task_spec"] = task_spec
    draft["_anchor_question_id"] = str(task_spec.get("diagnostic_anchor_question_id") or "")
    target_question_type = str(slot.get("question_type") or "")
    _normalise_draft_fields(draft, target_question_type)
    missing = _draft_missing_fields(draft)
    if missing and not blueprint:
        repaired = _gateway_chat_json(
            config,
            _draft_completion_messages(draft, target_question_type, missing),
            temperature=0.1,
            max_tokens=2200,
            deadline=stage_deadline,
            batch_id=request["batch_id"],
        )
        for key, value in repaired.items():
            if value not in (None, "", [], {}):
                draft[key] = value
        draft["_draft_source"] = "cherry_studio_model"
        _normalise_draft_fields(draft, target_question_type)
    _repair_probability_branch_draft(request["knowledge"], draft)
    missing = _draft_missing_fields(draft)
    if missing:
        error = f"模型返回的新题草稿缺少：{', '.join(missing)}"
        _append_rejection(bank, request, draft, error)
        raise LiveGenerationError(error)
    if str(draft.get("question_type") or "") != target_question_type:
        error = "模型返回的题型与目标题型不一致"
        _append_rejection(bank, request, draft, error)
        raise LiveGenerationError(error)
    shape_error = _question_type_shape_error(draft, target_question_type)
    if shape_error:
        _append_rejection(bank, request, draft, shape_error)
        raise LiveGenerationError(shape_error)
    equality_error = _declared_equality_point_error(draft)
    if equality_error:
        _append_rejection(bank, request, draft, equality_error)
        raise LiveGenerationError(equality_error)
    difficulty_error = _difficulty_gate_error(
        request["difficulty"],
        target_question_type,
        draft.get("stem_markdown"),
        draft.get("solution_markdown"),
        target_level=_strict_target_level(request),
    )
    draft["_difficulty_gate_evidence"] = _difficulty_gate_evidence(
        request["difficulty"],
        target_question_type,
        draft.get("stem_markdown"),
        draft.get("solution_markdown"),
        target_level=_strict_target_level(request),
    )
    if difficulty_error:
        _append_rejection(bank, request, draft, difficulty_error)
        raise LiveGenerationError(difficulty_error)
    draft_fingerprint = _pedagogical_fingerprint(
        knowledge=request["knowledge"],
        question_type=target_question_type,
        target_level=_request_target_level(request),
        stem=draft.get("stem_markdown"),
        solution=draft.get("solution_markdown"),
        options=draft.get("options") if isinstance(draft.get("options"), dict) else {},
        answer=draft.get("answer"),
    )
    draft["_pedagogical_fingerprint"] = draft_fingerprint
    probability_error = _probability_branch_arithmetic_error(
        request["knowledge"],
        draft.get("stem_markdown"),
        draft.get("answer"),
        draft.get("solution_markdown"),
    )
    if probability_error:
        _append_rejection(bank, request, draft, probability_error)
        raise LiveGenerationError(probability_error)
    if not blueprint:
        rejection_error = _rejected_structure_error(
            bank,
            request,
            target_question_type,
            str(draft.get("stem_markdown") or ""),
        )
        if rejection_error:
            raise LiveGenerationError(rejection_error)
    _ensure_batch_active(request["batch_id"])
    try:
        _register_batch_draft(
            request["batch_id"],
            request["slot_index"],
            str(draft.get("stem_markdown") or ""),
            (
                str(blueprint.get("family_id"))
                if blueprint and blueprint.get("family_id")
                else f"ped:{_pedagogical_fingerprint_key(draft_fingerprint)}"
            ),
        )
    except LiveGenerationError as exc:
        # A model draft rejected by the in-flight batch gate is still a failed
        # structure. Persist it so a later batch cannot silently recycle it.
        if not blueprint:
            _append_rejection(bank, request, draft, str(exc))
        raise
    return {
        "status": "draft",
        "mode": "cherry_studio_live",
        "draft_source": str(draft.get("_draft_source") or "cherry_studio_model"),
        "blueprint_family": str(draft.get("_safety_blueprint_family") or ""),
        "diversity_round": request["diversity_round"],
        "attempt": attempt,
        "model": config.model,
        "request": request,
        "draft": draft,
        "reference_ids": [str(row.get("question_id") or "") for row in references],
        "reference_count": len(references),
        "diagnostic_evidence_ids": [str(row.get("question_id") or "") for row in references],
        "diagnostic_evidence_count": len(references),
        "anchor_question_id": str(task_spec.get("diagnostic_anchor_question_id") or ""),
        "structure_anchor": task_spec.get("structure_anchor") or {},
        "fulltext_anchor_count": int((task_spec.get("generation_contract") or {}).get("fulltext_source_question_count") or 0),
        "generation_strategy": "single_anchor_task_spec",
        "task_spec": task_spec,
        "question_type": str(slot.get("question_type") or ""),
    }


def verify_personalized_draft(bank: Path, payload: dict[str, Any]) -> dict[str, Any]:
    bank = bank.resolve()
    request = _validate_request(payload)
    _ensure_batch_active(request["batch_id"])
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        _release_batch_draft(request["batch_id"], request["slot_index"])
        raise LiveGenerationError("缺少需要独立校验的新题草稿")
    attempt = max(1, min(5, int(payload.get("attempt") or 1)))
    request["attempt"] = attempt
    try:
        config = GatewayConfig.from_env()
        stage_deadline = time.monotonic() + min(MAX_STAGE_TIMEOUT_SECONDS, config.timeout_seconds)
        slot, references = _slot_and_references(
            bank,
            request["student_id"],
            request["knowledge"],
            request["reference_count"],
            mode=request["mode"],
            question_type=request["question_type"],
            slot_index=request["slot_index"],
        )
        submitted_task_spec = (
            draft.get("_generation_task_spec")
            if isinstance(draft.get("_generation_task_spec"), dict)
            else None
        )
        draft_blueprint = (
            {"family_id": str(draft.get("_safety_blueprint_family") or draft.get("family_id") or "")}
            if draft.get("_safety_blueprint_family") or draft.get("family_id")
            else None
        )
        submitted_feedback_memory = (
            submitted_task_spec.get("teacher_feedback_memory")
            if submitted_task_spec and isinstance(submitted_task_spec.get("teacher_feedback_memory"), list)
            else _recent_quality_feedback(bank, request)
        )
        recovered_task_spec = _build_generation_task_spec(
            bank,
            request,
            slot,
            references,
            draft_blueprint,
            quality_feedback=submitted_feedback_memory,
        )
        if submitted_task_spec and submitted_task_spec != recovered_task_spec:
            raise LiveGenerationError("出题任务单与服务端诊断、题型或结构锚点不一致")
        draft["_generation_task_spec"] = recovered_task_spec
        draft["_anchor_question_id"] = str(
            recovered_task_spec.get("diagnostic_anchor_question_id") or ""
        )
        question_type = str(draft.get("question_type") or "")
        safety_blueprint = bool(draft.get("_safety_blueprint_family") or draft.get("family_id"))
        verification = _gateway_chat_json(
            config,
            _verification_messages(
                question_type,
                str(draft.get("stem_markdown") or ""),
                draft.get("options") or {},
                target_difficulty=request["difficulty"],
                target_level=_strict_target_level(request),
                knowledge=request["knowledge"],
                enforce_difficulty=not safety_blueprint,
            ),
            temperature=0.1,
            max_tokens=2400,
            deadline=stage_deadline,
            batch_id=request["batch_id"],
        )
        _ensure_batch_active(request["batch_id"])
        _normalise_verification_fields(verification)
        if (
            str(verification.get("status") or "") == "passed"
            and draft.get("_deterministic_probability_check")
            and _probability_branch_arithmetic_error(
                request["knowledge"],
                draft.get("stem_markdown"),
                verification.get("answer"),
                verification.get("solution_markdown"),
            )
        ):
            verification["answer"] = draft.get("answer")
            verification["solution_markdown"] = draft.get("solution_markdown")
            verification["_deterministic_probability_corrected"] = True
            verification["notes"] = (
                "Cherry Studio 已确认题面可解；最终数值由 Fraction 分支核算重新封口。"
            )
        verification_status = str(verification.get("status") or "")
        if verification_status not in {"passed", "failed"} or (
            verification_status == "passed"
            and (
                not verification.get("answer")
                or (not safety_blueprint and not verification.get("solution_markdown"))
            )
        ):
            completion_messages = _verification_messages(
                str(draft.get("question_type") or ""),
                str(draft.get("stem_markdown") or ""),
                draft.get("options") or {},
                target_difficulty=request["difficulty"],
                target_level=_strict_target_level(request),
                knowledge=request["knowledge"],
                enforce_difficulty=not safety_blueprint,
            )
            completion_messages.append(
                {
                    "role": "user",
                    "content": (
                        "上一次审题 JSON 漏字段。请重新独立求解并返回完整 status、answer、"
                        "solution_markdown、notes；若题面有问题立即返回 failed。上次响应："
                        + json.dumps(verification, ensure_ascii=False)
                    ),
                }
            )
            repaired_verification = _gateway_chat_json(
                config,
                completion_messages,
                temperature=0.05,
                max_tokens=2400,
                deadline=stage_deadline,
                batch_id=request["batch_id"],
            )
            verification.update(repaired_verification)
            _normalise_verification_fields(verification)
        if safety_blueprint:
            checks = 1
            safe_error = _safe_blueprint_verification_error(
                verification,
                draft,
                question_type,
                request["knowledge"],
            )
            while safe_error and checks < 3:
                retry_messages = _verification_messages(
                    question_type,
                    str(draft.get("stem_markdown") or ""),
                    draft.get("options") or {},
                    target_difficulty=request["difficulty"],
                    target_level=_strict_target_level(request),
                    knowledge=request["knowledge"],
                    enforce_difficulty=False,
                )
                retry_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上一次独立审题未形成可采用的严格结论。请忽略任何前次答案，"
                            "从题面重新计算并逐项核对；只返回完整 JSON。问题类型：" + safe_error
                        ),
                    }
                )
                verification = _gateway_chat_json(
                    config,
                    retry_messages,
                    temperature=0.02,
                    max_tokens=2400,
                    deadline=stage_deadline,
                    batch_id=request["batch_id"],
                )
                checks += 1
                safe_error = _safe_blueprint_verification_error(
                    verification,
                    draft,
                    question_type,
                    request["knowledge"],
                )
            if safe_error:
                raise LiveGenerationError(f"Cherry Studio 连续 {checks} 次独立审题未确认该题：{safe_error}")
            verification["_consensus_checks"] = checks
            verification["notes"] = (
                f"Cherry Studio 第 {checks} 次独立求解确认。{str(verification.get('notes') or '').strip()}"
            ).strip()
        if str(verification.get("status") or "") == "passed":
            process_error = _verification_solution_process_error(verification.get("solution_markdown"))
            if process_error:
                raise LiveGenerationError(process_error)
            consistency_error = _verification_consistency_error(
                question_type,
                draft.get("options") or {},
                verification.get("answer"),
                verification.get("solution_markdown"),
            )
            if consistency_error:
                raise LiveGenerationError(consistency_error)
            draft_matches = _answers_match_for_type(
                draft.get("answer"),
                verification.get("answer"),
                question_type,
                request["knowledge"],
            )
            requires_fragile_consensus = (
                not safety_blueprint
                and question_type == "solution"
                and ("不等式" in request["knowledge"] or "构造函数" in request["knowledge"])
            )
            if requires_fragile_consensus and draft_matches:
                # A single model can reproduce its own plausible-looking mistake.
                # Force another stem-only solve for fragile proof questions even
                # when the first answer happens to match the drafting answer.
                draft_matches = False
            if not draft_matches:
                adjudication_messages = _verification_messages(
                    question_type,
                    str(draft.get("stem_markdown") or ""),
                    draft.get("options") or {},
                    target_difficulty=request["difficulty"],
                    target_level=_strict_target_level(request),
                    knowledge=request["knowledge"],
                    enforce_difficulty=not safety_blueprint,
                )
                adjudication_messages.append(
                    {
                        "role": "user",
                        "content": (
                            (
                                "这是高风险不等式解答题的第二次独立裁决。"
                                if requires_fragile_consensus
                                else "这是答案分歧后的第二次独立裁决。"
                            )
                            + "不要参考出题答案或任何前次结论，"
                            "从题面重新完整求解；只有能得到唯一、严格答案时才返回 passed。"
                        ),
                    }
                )
                adjudication = _gateway_chat_json(
                    config,
                    adjudication_messages,
                    temperature=0.02,
                    max_tokens=2400,
                    deadline=stage_deadline,
                    batch_id=request["batch_id"],
                )
                if not adjudication.get("answer"):
                    adjudication["answer"] = adjudication.get("correct_answer") or adjudication.get("final_answer")
                if not adjudication.get("solution_markdown"):
                    adjudication["solution_markdown"] = (
                        adjudication.get("solution")
                        or adjudication.get("analysis")
                        or adjudication.get("explanation")
                    )
                if not adjudication.get("status") and isinstance(adjudication.get("valid"), bool):
                    adjudication["status"] = "passed" if adjudication["valid"] else "failed"
                if str(adjudication.get("status") or "") != "passed":
                    raise LiveGenerationError("答案分歧后的二次独立裁决未通过")
                if not adjudication.get("answer") or not adjudication.get("solution_markdown"):
                    raise LiveGenerationError("答案分歧后的二次独立裁决不完整")
                adjudication_error = _verification_consistency_error(
                    question_type,
                    draft.get("options") or {},
                    adjudication.get("answer"),
                    adjudication.get("solution_markdown"),
                )
                if adjudication_error:
                    raise LiveGenerationError(adjudication_error)
                consensus_target = draft.get("answer") if safety_blueprint else verification.get("answer")
                if not _answers_match_for_type(
                    consensus_target,
                    adjudication.get("answer"),
                    question_type,
                    request["knowledge"],
                ):
                    raise LiveGenerationError(
                        "二次独立裁决未确认安全蓝图答案"
                        if safety_blueprint
                        else "两次独立求解未达成一致答案"
                    )
                if not safety_blueprint:
                    adjudication["_answer_consensus"] = True
                adjudication["_consensus_checks"] = 2
                adjudication["notes"] = (
                    f"{'二次独立裁决确认安全蓝图答案' if safety_blueprint else '二次独立裁决一致'}。"
                    f"{str(adjudication.get('notes') or '').strip()}"
                ).strip()
                verification = adjudication
            else:
                verification.setdefault("_consensus_checks", 1)
        question = _question_from_draft(config, request, slot, references, draft, verification, attempt)
        errors = validate_generated_questions(
            _read_json(bank / "tags" / "all_question_tags.json"),
            {"schema_version": "generated-question-set-v1", "questions": [question]},
        )
        final_probability_error = _probability_branch_arithmetic_error(
            request["knowledge"],
            question.get("stem_markdown"),
            question.get("answer"),
            question.get("solution_markdown"),
        )
        if final_probability_error:
            errors.append(final_probability_error)
        errors.extend(_history_similarity_errors(bank, question))
        if errors:
            raise LiveGenerationError("；".join(errors[:6]))
        _ensure_batch_active(request["batch_id"])
        _append_history(bank, request, references, question)
    except (GatewayConfigurationError, GatewayRequestError, GenerationCancelledError):
        _release_batch_draft(request["batch_id"], request["slot_index"])
        raise
    except LiveGenerationError as exc:
        _append_rejection(bank, request, draft, str(exc))
        _release_batch_draft(request["batch_id"], request["slot_index"])
        raise
    except Exception:
        _release_batch_draft(request["batch_id"], request["slot_index"])
        raise
    return {
        "status": "generated",
        "mode": "cherry_studio_live",
        "attempt": attempt,
        "model": config.model,
        "question": question,
        "references": references,
    }


def generate_personalized_question(bank: Path, payload: dict[str, Any], *, max_attempts: int = 5) -> dict[str, Any]:
    feedback: list[str] = []
    last_error = "未知错误"
    attempts = max(1, min(MAX_SLOT_ATTEMPTS, int(max_attempts)))
    for attempt in range(1, attempts + 1):
        draft_result: dict[str, Any] | None = None
        try:
            draft_result = generate_personalized_draft(
                bank,
                {**payload, "attempt": attempt, "feedback": feedback},
            )
            return verify_personalized_draft(
                bank,
                {**payload, "attempt": attempt, "draft": draft_result["draft"]},
            )
        except GatewayConfigurationError:
            raise
        except GatewayRequestError:
            raise
        except GenerationCancelledError:
            raise
        except LiveGenerationError as exc:
            last_error = str(exc)
            rejected_stem = str(((draft_result or {}).get("draft") or {}).get("stem_markdown") or "").strip()
            feedback = [last_error]
            if rejected_stem:
                feedback.append(f"上一次未通过题干，禁止重复或只换数字：{rejected_stem[:500]}")
    raise LiveGenerationError(f"连续 {attempts} 次生成均未通过：{last_error}")


def _atomic_write_jsonl(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for question_id in sorted(rows):
            handle.write(json.dumps(rows[question_id], ensure_ascii=False) + "\n")
    temporary.replace(path)


def _append_quality_feedback(bank: Path, question: dict[str, Any], payload: dict[str, Any], teacher_review: dict[str, Any]) -> None:
    feedback_type = str(payload.get("feedback_type") or "").strip()
    if feedback_type not in QUALITY_FEEDBACK_TYPES:
        return
    path = _teacher_quality_feedback_path(bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    generation = question.get("generation") or {}
    fingerprint = generation.get("pedagogical_fingerprint")
    if not isinstance(fingerprint, dict):
        fingerprint = _pedagogical_fingerprint(
            knowledge=str(question.get("primary_knowledge") or ""),
            question_type=str(question.get("question_type") or ""),
            target_level=int((question.get("personalization") or {}).get("target_level") or 3),
            stem=question.get("stem_markdown"),
            solution=question.get("solution_markdown"),
            options=question.get("options") if isinstance(question.get("options"), dict) else {},
            answer=question.get("answer"),
        )
    row = {
        "schema_version": "teacher-quality-feedback-v1",
        "created_at": teacher_review["reviewed_at"],
        "question_id": str(question.get("question_id") or ""),
        "source": "generated",
        "student_id": question.get("student_id"),
        "knowledge": question.get("primary_knowledge"),
        "question_type": question.get("question_type"),
        "feedback_type": feedback_type,
        "target_level": (question.get("personalization") or {}).get("target_level"),
        "teacher_suggested_level": payload.get("teacher_suggested_level"),
        "pedagogical_fingerprint": fingerprint,
        "notes": str(payload.get("notes") or "").strip(),
        "reviewer": teacher_review.get("reviewer") or "",
    }
    with HISTORY_LOCK, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_personalized_review(bank: Path, payload: dict[str, Any]) -> dict[str, Any]:
    bank = bank.resolve()
    if not isinstance(payload, dict) or not isinstance(payload.get("question"), dict):
        raise LiveGenerationError("缺少需要审核的新题")
    question = json.loads(json.dumps(payload["question"], ensure_ascii=False))
    question_id = str(question.get("question_id") or "").strip()
    status = str(payload.get("status") or "").strip()
    if not question_id or not question.get("is_generated"):
        raise LiveGenerationError("新题标识无效")
    if status not in ALLOWED_REVIEW_STATUSES:
        raise LiveGenerationError("审核状态无效")
    teacher_review = {
        "status": "approved" if status == "approved" else "pending",
        "decision": status,
        "reviewer": str(payload.get("reviewer") or "").strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "notes": str(payload.get("notes") or "").strip(),
    }
    question["teacher_review"] = teacher_review
    errors = validate_generated_questions(
        _read_json(bank / "tags" / "all_question_tags.json"),
        {"schema_version": "generated-question-set-v1", "questions": [question]},
        require_approved=status == "approved",
    )
    if errors:
        raise LiveGenerationError("；".join(errors[:6]))
    path = _review_state_path(bank)
    rows: dict[str, dict[str, Any]] = {}
    if path.exists() and path.stat().st_size:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("question_id"):
                rows[str(row["question_id"])] = row
    rows[question_id] = {
        "schema_version": "personalized-question-review-v1",
        "question_id": question_id,
        "student_id": question.get("student_id"),
        "status": status,
        "teacher_review": teacher_review,
        "question": question,
    }
    with HISTORY_LOCK:
        _atomic_write_jsonl(path, rows)
    _append_quality_feedback(bank, question, payload, teacher_review)
    return {"status": "saved", "question_id": question_id, "teacher_review": teacher_review}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Generate one personalized question through Cherry Studio.")
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--student-name", default="")
    parser.add_argument("--knowledge", required=True)
    parser.add_argument("--difficulty", default="auto", choices=sorted(ALLOWED_DIFFICULTIES))
    parser.add_argument("--focus", default="auto", choices=sorted(ALLOWED_FOCUSES))
    parser.add_argument("--reference-count", type=int, default=3, choices=(3, 4, 5))
    args = parser.parse_args()
    result = generate_personalized_question(
        args.structured_dir,
        {
            "student_id": args.student_id,
            "student_name": args.student_name or args.student_id,
            "knowledge": args.knowledge,
            "difficulty": args.difficulty,
            "focus": args.focus,
            "reference_count": args.reference_count,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
