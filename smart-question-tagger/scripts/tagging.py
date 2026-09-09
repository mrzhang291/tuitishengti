import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.append(str(SCRIPTS_DIR))

from gaokao_taxonomy import classify_text_detailed, infer_secondary_knowledge as infer_curriculum_secondary


ABILITY_TAGS = ["运算求解", "逻辑推理", "数形结合", "分类讨论", "建模应用"]


def difficulty_from_score_rate(score_rate):
    if score_rate is None:
        return 3
    if score_rate > 90:
        return 1
    if score_rate > 70:
        return 2
    if score_rate > 50:
        return 3
    if score_rate > 30:
        return 4
    return 5


def tag_question(question):
    stem_text = " ".join(str(question.get(key, "")) for key in ("stem_markdown", "stem_text"))
    solution_text = " ".join(str(question.get(key, "")) for key in ("solution_markdown", "solution_text"))
    text = " ".join((stem_text, solution_text, str(question.get("answer", ""))))
    primary_info = classify_text_detailed(stem_text, solution_text)
    classification = primary_info.pop("classification")
    primary = primary_info["primary_knowledge"]
    secondary = infer_curriculum_secondary(text, primary)
    score_rate = question.get("score_rate")
    difficulty = difficulty_from_score_rate(score_rate)
    ability_tags = _infer_ability_tags(text, question.get("question_type"))
    confidence_detail = _tag_confidence_detail(
        classification,
        ability_tags,
        score_rate,
        question.get("quality_flags") or [],
        question.get("question_type"),
    )
    overall_confidence = confidence_detail["overall"]
    evidence_labels = [item["label"] for item in classification.get("evidence", [])[:5]]

    return {
        "question_id": question["question_id"],
        "display_id": question.get("display_id"),
        "question_number": question.get("question_number"),
        "question_type": question.get("question_type"),
        "score_rate": score_rate,
        "avg_score": question.get("avg_score"),
        "full_score": question.get("full_score", question.get("score")),
        "stem_markdown": question.get("stem_markdown", ""),
        "answer": question.get("answer", ""),
        "tags": {
            "curriculum_theme": primary_info["curriculum_theme"],
            "knowledge_unit": primary_info["knowledge_unit"],
            "skill_tags": primary_info["skill_tags"],
            "primary_knowledge": primary,
            "secondary_knowledge": secondary,
            "ability_tags": ability_tags,
            "difficulty": difficulty,
            "difficulty_basis": _difficulty_basis(score_rate),
            "teaching_tag": _teaching_tag(difficulty),
            "error_prone_points": _error_prone_points(primary),
            "prerequisite_knowledge": _prerequisites(primary),
            "tag_reasoning": (
                f"主知识点由证据 {('、'.join(evidence_labels) or '默认规则')} 判定；"
                f"第二候选为 {classification.get('runner_up')}，分差 {classification.get('margin')}。"
            ),
            "tag_evidence": classification.get("evidence", []),
            "tag_candidates": classification.get("candidates", []),
            "classification_margin": classification.get("margin_ratio"),
            "tags_generated_by": "local_rules_v2_evidence_scored",
            "tags_reviewed": False,
            "tags_confidence": overall_confidence,
            "tags_confidence_detail": confidence_detail,
            "needs_teacher_review": overall_confidence < 0.8,
        },
    }


def build_indices(tagged_questions):
    knowledge_index = defaultdict(list)
    difficulty_index = defaultdict(list)
    ability_index = defaultdict(list)

    for question in tagged_questions:
        question_id = question["question_id"]
        tags = question["tags"]
        knowledge_names = [tags["primary_knowledge"]] + list(tags.get("secondary_knowledge", []))
        for name in knowledge_names:
            knowledge_index[name].append(question_id)
        difficulty_index[str(tags["difficulty"])].append(question_id)
        for ability in tags.get("ability_tags", []):
            ability_index[ability].append(question_id)

    return {
        "knowledge_index": _sorted_index(knowledge_index),
        "difficulty_index": _sorted_index(difficulty_index),
        "ability_index": _sorted_index(ability_index),
    }


def calculate_student_mastery(records, question_tags):
    mastery_rows = []
    for record in records:
        grouped = defaultdict(lambda: {"earned": 0.0, "full": 0.0, "correct": 0, "total": 0, "weak_ids": []})
        for answer in record.get("answers", []):
            question_id = answer.get("question_id")
            tags = question_tags.get(question_id)
            if not tags:
                continue
            tag_name = tags["primary_knowledge"]
            full_score = float(answer.get("full_score") or 0)
            score = float(answer.get("score") or 0)
            if full_score <= 0:
                continue
            bucket = grouped[tag_name]
            bucket["earned"] += score
            bucket["full"] += full_score
            bucket["total"] += 1
            if score >= full_score:
                bucket["correct"] += 1
            else:
                bucket["weak_ids"].append(question_id)

        for tag_name, bucket in grouped.items():
            mastery_score = round(bucket["earned"] / bucket["full"], 2) if bucket["full"] else 0.0
            recent_accuracy = round(bucket["correct"] / bucket["total"], 2) if bucket["total"] else 0.0
            mastery_rows.append(
                {
                    "student_id": record["student_id"],
                    "name": record.get("name", ""),
                    "class": record.get("class", ""),
                    "tag_name": tag_name,
                    "mastery_score": mastery_score,
                    "recent_accuracy": recent_accuracy,
                    "question_count": bucket["total"],
                    "weak_question_ids": bucket["weak_ids"],
                    "status": _mastery_status(mastery_score),
                }
            )
    return sorted(mastery_rows, key=lambda item: (item["student_id"], item["mastery_score"], item["tag_name"]))


def recommend_for_student(student_id, records, question_bank, tags_by_question, mastery, top_n=5):
    student_record = next((record for record in records if record.get("student_id") == student_id), None)
    if not student_record:
        return []

    answers = {answer["question_id"]: answer for answer in student_record.get("answers", [])}
    weak_tags = {
        row["tag_name"]: row
        for row in mastery
        if row.get("student_id") == student_id and row.get("mastery_score", 1) < 0.8
    }
    wrong_question_ids = {
        question_id
        for question_id, answer in answers.items()
        if float(answer.get("score") or 0) < float(answer.get("full_score") or 0)
    }

    candidates = []
    for question in question_bank:
        question_id = question["question_id"]
        tags = tags_by_question.get(question_id)
        if not tags:
            continue
        answer = answers.get(question_id)
        attempted = answer is not None
        if attempted and question_id not in wrong_question_ids:
            continue

        primary = tags["primary_knowledge"]
        mastery_row = weak_tags.get(primary)
        weakness = 1 - mastery_row["mastery_score"] if mastery_row else 0.2
        already_wrong_boost = 0.15 if question_id in wrong_question_ids else 0.0
        unattempted_boost = 0.20 if not attempted else 0.0
        difficulty = tags.get("difficulty", 3)
        difficulty_fit = _difficulty_fit(difficulty, mastery_row["mastery_score"] if mastery_row else 0.75)
        score = round(0.55 * weakness + 0.25 * difficulty_fit + already_wrong_boost + unattempted_boost, 4)

        candidates.append(
            {
                "student_id": student_id,
                "question_id": question_id,
                "strategy": "补弱题" if mastery_row else "巩固题",
                "score": score,
                "reason": f"{primary} 掌握度偏低，当前掌握度 {mastery_row['mastery_score']:.2f}" if mastery_row else f"{primary} 可用于巩固",
                "primary_knowledge": primary,
                "difficulty": difficulty,
                "question_type": question.get("question_type"),
                "stem_preview": _preview(question.get("stem_markdown", ""), 80),
                "source": "wrong_question" if question_id in wrong_question_ids else "unattempted_question",
            }
        )

    return sorted(candidates, key=lambda item: (-item["score"], item["question_id"]))[:top_n]


def run_midterm_closed_loop(workspace, output_dir, sample_student_id=None):
    workspace = Path(workspace)
    output_dir = Path(output_dir)
    tagging_input = _read_json(workspace / "test-data" / "midterm_tagging_input.json")
    records = _read_jsonl(workspace / "test-data" / "student_exam_records.jsonl")
    tagged_questions = [tag_question(question) for question in tagging_input]
    tags_by_question = {item["question_id"]: item["tags"] for item in tagged_questions}
    question_bank = [_question_summary(item) for item in tagged_questions]
    indices = build_indices(tagged_questions)
    mastery = calculate_student_mastery(records, tags_by_question)
    student_id = sample_student_id or records[0]["student_id"]
    recommendations = recommend_for_student(student_id, records, question_bank, tags_by_question, mastery, top_n=5)

    _write_json(output_dir / "tags" / "midterm_tags.json", tagged_questions)
    _write_json(output_dir / "index" / "knowledge_index.json", indices["knowledge_index"])
    _write_json(output_dir / "index" / "difficulty_index.json", indices["difficulty_index"])
    _write_json(output_dir / "index" / "ability_index.json", indices["ability_index"])
    _write_jsonl(output_dir / "student" / "student_mastery.jsonl", mastery)
    _write_json(
        output_dir / "recommendations" / "sample_recommendations.json",
        {"student_id": student_id, "recommendations": recommendations},
    )
    _write_review_csv(output_dir / "review" / "midterm_tag_review.csv", tagged_questions)

    return {
        "tagged_questions": len(tagged_questions),
        "student_mastery_records": len(mastery),
        "recommendations": len(recommendations),
        "sample_student_id": student_id,
        "output_dir": str(output_dir),
    }


def run_full_closed_loop(workspace, output_dir, sample_student_id=None):
    workspace = Path(workspace)
    output_dir = Path(output_dir)
    records = _read_jsonl(workspace / "test-data" / "student_exam_records.jsonl")
    midterm_score_data = {
        item["question_id"]: item
        for item in _read_json(workspace / "test-data" / "midterm_tagging_input.json")
    }
    all_questions = _load_all_questions(workspace, midterm_score_data)
    tagged_questions = [tag_question(question) for question in all_questions]
    tags_by_question = {item["question_id"]: item["tags"] for item in tagged_questions}
    question_bank = [_question_summary(item) for item in tagged_questions]
    indices = build_indices(tagged_questions)
    mastery = calculate_student_mastery(records, tags_by_question)
    student_id = sample_student_id or records[0]["student_id"]
    recommendations = recommend_for_student(student_id, records, question_bank, tags_by_question, mastery, top_n=5)

    _write_json(output_dir / "tags" / "all_question_tags.json", tagged_questions)
    _write_jsonl(output_dir / "tags" / "question_tags.jsonl", _flat_question_tags(tagged_questions))
    _write_json(output_dir / "index" / "knowledge_index.json", indices["knowledge_index"])
    _write_json(output_dir / "index" / "difficulty_index.json", indices["difficulty_index"])
    _write_json(output_dir / "index" / "ability_index.json", indices["ability_index"])
    _write_jsonl(output_dir / "student" / "student_mastery.jsonl", mastery)
    _write_json(
        output_dir / "recommendations" / "sample_recommendations.json",
        {"student_id": student_id, "recommendations": recommendations},
    )
    _write_review_csv(output_dir / "review" / "all_tag_review.csv", tagged_questions)

    return {
        "tagged_questions": len(tagged_questions),
        "midterm_tagged_questions": sum(1 for item in tagged_questions if item["question_id"] in midterm_score_data),
        "student_mastery_records": len(mastery),
        "recommendations": len(recommendations),
        "sample_student_id": student_id,
        "output_dir": str(output_dir),
    }


def _infer_primary_knowledge(text):
    if _is_geometry_optimization(text):
        return "导数应用·极值与最值·求最值"
    if _has_geometry_modeling_context(text):
        return "立体几何·空间几何体·棱锥体积"
    if any(token in text for token in ("概率", "P(", "P（", "分布", "E(X)", "期望", "方差", "全概率")):
        if any(token in text for token in ("全概率", "P(", "P（", "P(B)", "概率模型")):
            return "概率统计·概率模型·全概率公式"
        if any(token in text for token in ("E(X)", "期望", "方差")):
            return "概率统计·数字特征·期望"
        return "概率统计·概率模型·全概率公式"
    if any(token in text for token in ("lim", "Δx", "导数定义", "变化率")):
        return "导数与微分·导数定义·导数几何意义"
    if "切线" in text:
        return "导数与微分·切线方程·求切线"
    if any(token in text for token in ("零点", "三个根", "三个零点")):
        return "导数应用·零点问题·函数零点个数"
    if any(token in text for token in ("最大值", "最小值", "最值")):
        return "导数应用·极值与最值·求最值"
    if "极值" in text:
        return "导数应用·极值与最值·求极值"
    if any(token in text for token in ("单调", "减函数", "增函数")):
        return "导数应用·单调性·判断单调区间"
    if any(token in text for token in ("不等式", "证明", "恒成立")):
        return "导数应用·不等式证明·构造函数证明"
    if any(token in text for token in ("sin", "cos", "周期", "奇偶")):
        return "函数综合·函数性质·周期性应用"
    return "导数与微分·求导运算·基本函数求导"


def _infer_secondary_knowledge(text, primary):
    candidates = []
    if primary != "立体几何·空间几何体·棱锥体积" and _has_geometry_modeling_context(text):
        candidates.append("立体几何·空间几何体·体积建模")
    if primary != "导数与微分·求导运算·基本函数求导" and any(token in text for token in ("f^'", "求导", "导数")):
        candidates.append("导数与微分·求导运算·基本函数求导")
    if primary != "导数应用·单调性·判断单调区间" and "单调" in text:
        candidates.append("导数应用·单调性·判断单调区间")
    if primary not in ("导数应用·极值与最值·求极值", "导数应用·极值与最值·求最值") and any(token in text for token in ("极值", "最大值", "最小值", "最值")):
        candidates.append("导数应用·极值与最值·求最值")
    if primary != "函数综合·函数构造·构造函数证不等式" and any(token in text for token in ("构造", "证明")):
        candidates.append("函数综合·函数构造·构造函数证不等式")
    if primary != "概率统计·数字特征·期望" and any(token in text for token in ("E(X)", "期望")):
        candidates.append("概率统计·数字特征·期望")
    return candidates[:3]


def _infer_ability_tags(text, question_type):
    tags = []
    if any(token in text for token in ("计算", "求", "解得", "方程", "f^'", "V^'", "V'", "导数", "单调递增", "单调递减")):
        tags.append("运算求解")
    if question_type == "solution" or any(token in text for token in ("证明", "充要", "存在", "唯一", "推出")):
        tags.append("逻辑推理")
    if any(token in text for token in ("图", "几何", "切线", "交点", "棱锥", "体积", "容积", "方盒")):
        tags.append("数形结合")
    if _contains_parameter_discussion(text):
        tags.append("分类讨论")
    if any(token in text for token in ("概率模型", "家庭", "甲袋", "乙袋", "实际", "铁片", "容器")):
        tags.append("建模应用")
    if not tags:
        tags.append("运算求解")
    ordered = [tag for tag in ABILITY_TAGS if tag in tags]
    return ordered[:3]


def _difficulty_basis(score_rate):
    if score_rate is None:
        return "model_estimated"
    return f"score_rate {float(score_rate):.1f}%"


def _contains_parameter_discussion(text):
    if any(token in text for token in ("参数", "取值范围", "分类讨论", "恒成立")):
        return True
    return re.search(r"(?<![A-Za-z])[abm](?:[∈>≥<≤=]|\))", text) is not None


def _has_geometry_modeling_context(text):
    return any(token in text for token in ("正四棱锥", "棱锥", "空间几何体", "体积", "容积", "铁片", "容器", "方盒"))


def _is_geometry_optimization(text):
    if not _has_geometry_modeling_context(text):
        return False
    return any(token in text for token in ("最大值", "最小值", "最大", "最小", "最值", "V(x)", "V^'", "单调递增", "单调递减"))


def _teaching_tag(difficulty):
    if difficulty <= 2:
        return "基础巩固"
    if difficulty == 3:
        return "综合提升"
    if difficulty == 4:
        return "易错辨析"
    return "竞赛拓展"


def _error_prone_points(primary):
    if primary.startswith("立体几何"):
        return ["空间图形关系理解错误", "体积公式使用错误", "变量范围忽略"]
    if primary.startswith("概率统计"):
        return ["事件条件理解错误", "概率公式套用混淆", "期望计算漏项"]
    if "切线" in primary:
        return ["切点坐标代入错误", "导数值与斜率对应错误"]
    if "极值" in primary or "最值" in primary or "单调" in primary:
        return ["导数符号判断错误", "忽略定义域或端点"]
    if "零点" in primary:
        return ["极值与零点关系判断错误", "参数范围漏讨论"]
    return ["基本公式记忆不牢", "代数化简错误"]


def _prerequisites(primary):
    if primary.startswith("立体几何"):
        return ["棱锥体积公式", "基本空间想象"]
    if primary.startswith("概率统计"):
        return ["古典概型", "条件概率与全概率公式"]
    if primary.startswith("导数应用"):
        return ["基本初等函数求导", "导数与函数单调性的关系"]
    return ["导数定义", "基本初等函数求导"]


def _tag_confidence_detail(classification, ability_tags, score_rate, quality_flags, question_type):
    primary = float(classification.get("primary_confidence") or 0.54)
    theme = min(0.98, primary + 0.05)
    knowledge_unit = min(0.97, primary + 0.025)
    skill_tags = max(0.5, primary - 0.04)

    ability_signal = min(len(ability_tags), 3)
    ability = 0.68 + 0.045 * ability_signal
    if question_type == "solution":
        ability += 0.035
    ability = round(min(0.88, ability), 3)

    difficulty = 0.96 if score_rate is not None else 0.55
    flags = set(quality_flags or [])
    benign = {"contains_images", "answer_math_display_repaired"}
    blocking = {"missing_image_asset", "answer_contamination", "markdown_math_loss"}
    if flags & blocking:
        structure = 0.35
    elif flags - benign:
        structure = 0.72
    else:
        structure = 0.96

    # Overall confidence describes curriculum/knowledge tagging. Difficulty has
    # its own confidence because absent score-rate should not make an otherwise
    # obvious knowledge label impossible to auto-triage.
    overall = (
        0.22 * theme
        + 0.20 * knowledge_unit
        + 0.30 * primary
        + 0.12 * skill_tags
        + 0.08 * ability
        + 0.08 * structure
    )
    overall = round(max(0.0, min(0.98, overall)), 3)
    return {
        "curriculum_theme": round(theme, 3),
        "knowledge_unit": round(knowledge_unit, 3),
        "primary_knowledge": round(primary, 3),
        "skill_tags": round(skill_tags, 3),
        "ability_tags": ability,
        "difficulty": round(difficulty, 3),
        "structure": round(structure, 3),
        "overall": overall,
        "classification_margin": float(classification.get("margin_ratio") or 0),
    }


def _mastery_status(mastery_score):
    if mastery_score < 0.6:
        return "待补弱项"
    if mastery_score < 0.8:
        return "正在巩固"
    return "已掌握"


def _difficulty_fit(difficulty, mastery_score):
    target = 2 if mastery_score < 0.6 else 3
    return max(0.0, 1 - abs(difficulty - target) / 4)


def _question_summary(tagged_question):
    return {
        "question_id": tagged_question["question_id"],
        "question_type": tagged_question.get("question_type"),
        "stem_markdown": tagged_question.get("stem_markdown", ""),
    }


def _load_all_questions(workspace, midterm_score_data):
    question_paths = []
    for root in (workspace / "homework-data", workspace / "test-data" / "midterm_2025_2026_q1_q19"):
        question_paths.extend(root.rglob("question.json"))

    questions = []
    seen = set()
    for path in sorted(question_paths):
        question = _read_json(path)
        question_id = question["question_id"]
        if question_id in seen:
            continue
        seen.add(question_id)
        if question_id in midterm_score_data:
            question = {**question, **_score_fields(midterm_score_data[question_id])}
        questions.append(question)
    return questions


def _score_fields(score_item):
    return {
        "score_rate": score_item.get("score_rate"),
        "avg_score": score_item.get("avg_score"),
        "full_score": score_item.get("full_score"),
    }


def _flat_question_tags(tagged_questions):
    rows = []
    for question in tagged_questions:
        tags = question["tags"]
        rows.append(
            {
                "question_id": question["question_id"],
                "display_id": question.get("display_id"),
                "curriculum_theme": tags.get("curriculum_theme"),
                "knowledge_unit": tags.get("knowledge_unit"),
                "skill_tags": tags.get("skill_tags", []),
                "primary_knowledge": tags["primary_knowledge"],
                "secondary_knowledge": tags.get("secondary_knowledge", []),
                "ability_tags": tags.get("ability_tags", []),
                "difficulty": tags.get("difficulty"),
                "difficulty_basis": tags.get("difficulty_basis"),
                "teaching_tag": tags.get("teaching_tag"),
                "tags_confidence": tags.get("tags_confidence"),
                "tags_confidence_detail": tags.get("tags_confidence_detail", {}),
                "classification_margin": tags.get("classification_margin"),
                "needs_teacher_review": tags.get("needs_teacher_review", True),
                "review_reasons": tags.get("review_reasons", []),
                "tags_reviewed": tags.get("tags_reviewed", False),
            }
        )
    return rows


def _preview(text, length):
    text = " ".join(str(text).split())
    return text[:length]


def _sorted_index(index):
    return {key: sorted(value) for key, value in sorted(index.items())}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_jsonl(path):
    records = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_review_csv(path, tagged_questions):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "question_id",
        "display_id",
        "stem_markdown_preview",
        "curriculum_theme_model",
        "knowledge_unit_model",
        "skill_tags_model",
        "primary_knowledge_model",
        "primary_confidence",
        "runner_up_primary",
        "classification_margin",
        "tag_evidence",
        "primary_knowledge_teacher",
        "secondary_knowledge_model",
        "difficulty_model",
        "difficulty_teacher",
        "difficulty_basis",
        "ability_tags_model",
        "teaching_tag_model",
        "error_prone_points_model",
        "review_status",
        "review_notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for question in tagged_questions:
            tags = question["tags"]
            teacher_review = tags.get("teacher_review") or {}
            teacher_decision = teacher_review.get("decision")
            review_status = {
                "approve": "已通过",
                "change": "已修改",
                "defer": "暂缓",
            }.get(teacher_decision, "待审核")
            writer.writerow(
                {
                    "question_id": question["question_id"],
                    "display_id": question.get("display_id", ""),
                    "stem_markdown_preview": _preview(question.get("stem_markdown", ""), 80),
                    "curriculum_theme_model": tags.get("curriculum_theme", ""),
                    "knowledge_unit_model": tags.get("knowledge_unit", ""),
                    "skill_tags_model": "；".join(tags.get("skill_tags", [])),
                    "primary_knowledge_model": tags["primary_knowledge"],
                    "primary_confidence": (tags.get("tags_confidence_detail") or {}).get("primary_knowledge", ""),
                    "runner_up_primary": (tags.get("tag_candidates") or [{}, {}])[1].get("primary_knowledge", "")
                    if len(tags.get("tag_candidates") or []) > 1
                    else "",
                    "classification_margin": tags.get("classification_margin", ""),
                    "tag_evidence": "；".join(item.get("label", "") for item in tags.get("tag_evidence", [])),
                    "primary_knowledge_teacher": tags.get("primary_knowledge", "") if teacher_decision == "change" else "",
                    "secondary_knowledge_model": "；".join(tags.get("secondary_knowledge", [])),
                    "difficulty_model": tags["difficulty"],
                    "difficulty_teacher": tags.get("difficulty", "") if teacher_decision == "change" else "",
                    "difficulty_basis": tags["difficulty_basis"],
                    "ability_tags_model": "；".join(tags.get("ability_tags", [])),
                    "teaching_tag_model": tags["teaching_tag"],
                    "error_prone_points_model": "；".join(tags.get("error_prone_points", [])),
                    "review_status": review_status,
                    "review_notes": teacher_review.get("review_notes", ""),
                }
            )
