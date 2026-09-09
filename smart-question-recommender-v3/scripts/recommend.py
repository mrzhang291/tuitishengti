import argparse
import base64
import html
import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path


# 添加 scripts 目录到路径，以便导入 coverage_checker
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
try:
    from coverage_checker import check_coverage
except ImportError:
    check_coverage = None

# 添加本脚本所在目录，导入 HTML 模板常量
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recommendation_template import (
    HTML_HEAD_TEMPLATE,
    HTML_FOOTER,
    USAGE_NOTE_HTML,
    OVERALL_FEEDBACK_HTML,
    EXPORT_BAR_HTML,
)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Recommend school-bank questions for a student.")
    parser.add_argument("--workspace", default=None, help="Optional project workspace containing output/full and test-data.")
    parser.add_argument("--data-dir", default=None, help="Question-bank root containing tags/, student/, and questions/. Required when --workspace is not used.")
    parser.add_argument("--student", required=True, help="Student id, name, or ticket number.")
    parser.add_argument("--top-n", type=int, default=5, help="Number of recommended questions.")
    parser.add_argument("--scenario", choices=["new_lesson", "composite", "pre_exam", "post_exam"], default="post_exam", help="教学场景预设。")
    parser.add_argument("--current-knowledge", default=None, help="当前课主知识点（新课场景必填，如：导数与微分·求导运算·基本函数求导）")
    parser.add_argument("--review-scope", default=None, help="复习范围知识点，逗号分隔（复合练习场景必填）")
    parser.add_argument("--exam-scope", default=None, help="考试范围知识点，逗号分隔（考前巩固场景必填，不填则默认全部知识点）")
    parser.add_argument("--question-types", default=None, help="自定义题型配额，覆盖场景默认值。JSON格式，如：{\"single_choice\":5,\"multiple_choice\":2,\"fill_blank\":3,\"solution\":3}")
    parser.add_argument("--format", choices=["html", "markdown", "json"], default="html", help="Output format.")
    args = parser.parse_args()

    # 反问机制：检查场景必填参数
    config = SCENARIO_CONFIG.get(args.scenario, SCENARIO_CONFIG["post_exam"])
    if config["required_param"] == "current_knowledge" and not args.current_knowledge:
        raise SystemExit("【新课课后练习】请提供当前课知识点：--current-knowledge '一级·二级·三级'")
    if config["required_param"] == "review_scope" and not args.review_scope:
        raise SystemExit("【复合练习】请提供复习范围：--review-scope '知识点1,知识点2,知识点3'")
    if config["required_param"] == "exam_scope" and not args.exam_scope:
        print("【考前巩固】未指定考试范围，默认使用全部知识点。", file=sys.stderr)

    # 解析自定义题型配额
    custom_type_distribution = None
    if args.question_types:
        try:
            custom_type_distribution = json.loads(args.question_types)
        except json.JSONDecodeError as e:
            raise SystemExit(f"题型配额 JSON 格式错误：{e}")

    paths = _resolve_data_paths(args.workspace, args.data_dir)
    records = _read_jsonl(paths["records"])
    student = _find_student(records, args.student)
    if not student:
        names = "、".join(record.get("name", "") for record in records[:8])
        raise SystemExit(f"未找到学生：{args.student}。可用姓名示例：{names}")

    tagged_questions = _read_json(paths["tags"])
    mastery = _read_jsonl(paths["mastery"])
    tags_by_question = {item["question_id"]: item["tags"] for item in tagged_questions}
    tagged_by_question = {item["question_id"]: item for item in tagged_questions}
    question_bank = [
        {
            "question_id": item["question_id"],
            "question_type": item.get("question_type"),
            "stem_markdown": item.get("stem_markdown", ""),
        }
        for item in tagged_questions
    ]

    # 如果场景有题型配额，自动调整总题数
    type_distribution = custom_type_distribution if custom_type_distribution else config.get("question_type_distribution")
    effective_top_n = args.top_n
    if type_distribution:
        total_n = sum(type_distribution.values())
        if args.top_n < total_n:
            print(f"【题型配额】当前 top_n={args.top_n}，但场景要求共 {total_n} 道题，已自动调整为 {total_n}。", file=sys.stderr)
            effective_top_n = total_n

    # 读取反馈校准数据（如果存在）
    calibration, blacklist = _load_feedback_calibrations(args.workspace)

    recommendations = recommend_for_student(
        student["student_id"],
        records,
        question_bank,
        tags_by_question,
        mastery,
        top_n=effective_top_n,
        scenario=args.scenario,
        current_knowledge=args.current_knowledge,
        review_scope=args.review_scope,
        exam_scope=args.exam_scope,
        custom_type_distribution=custom_type_distribution,
        calibration=calibration,
        blacklist=blacklist,
    )
    recommendations["retry_recommendations"] = _attach_question_details(
        recommendations["retry_recommendations"], tagged_by_question, paths.get("questions")
    )
    recommendations["extension_recommendations"] = _attach_question_details(
        recommendations["extension_recommendations"], tagged_by_question, paths.get("questions")
    )
    weak_points = [
        item
        for item in mastery
        if item.get("student_id") == student["student_id"] and item.get("mastery_score", 1) < 0.8
    ][:5]

    # 检查覆盖度
    coverage_gaps = []
    if check_coverage is not None:
        try:
            coverage_gaps = check_coverage(tagged_questions, weak_points, min_questions=3)
        except Exception:
            pass

    config = SCENARIO_CONFIG.get(args.scenario, SCENARIO_CONFIG["post_exam"])
    payload = {
        "student": {
            "student_id": student["student_id"],
            "name": student.get("name", ""),
            "class": student.get("class", ""),
            "total_score": student.get("total_score"),
            "score_breakdown": _compute_score_breakdown(student.get("answers", [])),
        },
        "data_source": paths["source"],
        "weak_points": weak_points,
        "recommendations": recommendations,
        "scenario": args.scenario,
        "scenario_name": config["name"],
        "scenario_description": config["description"],
        "coverage_gaps": coverage_gaps,
    }

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        print(_render_markdown(payload))
    else:
        print(_render_html(payload))


def _find_student(records, query):
    query = str(query).strip()
    for record in records:
        if query in {
            str(record.get("student_id", "")),
            str(record.get("name", "")),
            str(record.get("ticket_number", "")),
        }:
            return record
    return None


def _compute_score_breakdown(answers):
    """按题型统计得分与总分。"""
    breakdown = {}
    for ans in answers:
        qtype = ans.get("question_type", "")
        if not qtype:
            continue
        if qtype not in breakdown:
            breakdown[qtype] = {"score": 0.0, "full_score": 0.0}
        try:
            breakdown[qtype]["score"] += float(ans.get("score", 0) or 0)
            breakdown[qtype]["full_score"] += float(ans.get("full_score", 0) or 0)
        except (TypeError, ValueError):
            pass
    return breakdown


# 教学场景预设配置
SCENARIO_CONFIG = {
    "new_lesson": {
        "name": "新课课后练习",
        "required_param": "current_knowledge",
        "knowledge_filter": "exact_match",
        "teaching_tags": ["基础巩固", "综合提升"],
        "difficulty_range": (1, 3),
        "question_type_distribution": {
            "single_choice": 3,
            "fill_blank": 2,
        },
        "weakness_weight": 0.25,
        "difficulty_fit_weight": 0.55,
        "wrong_boost": 0.05,
        "unattempted_boost": 0.15,
        "description": "巩固刚学知识点，基础题为主，难度1-3，单选3+填空2",
    },
    "composite": {
        "name": "复合练习",
        "required_param": "review_scope",
        "knowledge_filter": "in_scope",
        "teaching_tags": ["易错辨析", "方法迁移", "综合提升"],
        "difficulty_range": (2, 3),
        "question_type_distribution": {
            "single_choice": 2,
            "multiple_choice": 1,
            "fill_blank": 1,
            "solution": 1,
        },
        "weakness_weight": 0.45,
        "difficulty_fit_weight": 0.35,
        "wrong_boost": 0.10,
        "unattempted_boost": 0.10,
        "description": "知识串联、跨点综合，混合题型，单选2+多选1+填空1+解答1",
    },
    "pre_exam": {
        "name": "考前巩固",
        "required_param": "exam_scope",
        "knowledge_filter": "in_scope",
        "teaching_tags": ["综合提升", "竞赛拓展"],
        "difficulty_range": (3, 5),
        "question_type_distribution": {
            "single_choice": 3,
            "multiple_choice": 3,
            "fill_blank": 3,
            "solution": 3,
        },
        "weakness_weight": 0.60,
        "difficulty_fit_weight": 0.20,
        "wrong_boost": 0.15,
        "unattempted_boost": 0.05,
        "description": "模拟实战、限时训练，中高难度综合题，单选3+多选3+填空3+解答3",
    },
    "post_exam": {
        "name": "考后强化",
        "required_param": None,
        "knowledge_filter": "weak_tags",
        "teaching_tags": None,
        "difficulty_range": None,
        "question_type_distribution": {
            "single_choice": 5,
            "multiple_choice": 2,
            "fill_blank": 3,
            "solution": 3,
        },
        "weakness_weight": 0.70,
        "difficulty_fit_weight": 0.10,
        "wrong_boost": 0.20,
        "unattempted_boost": 0.05,
        "description": "错题重做、薄弱点攻克，单选5+多选2+填空3+解答3",
    },
}


def recommend_for_student(student_id, records, question_bank, tags_by_question, mastery, top_n=5, scenario="post_exam", current_knowledge=None, review_scope=None, exam_scope=None, custom_type_distribution=None, calibration=None, blacklist=None):
    calibration = calibration or {}
    blacklist = blacklist or {}
    config = SCENARIO_CONFIG.get(scenario, SCENARIO_CONFIG["post_exam"])
    student_record = next((record for record in records if record.get("student_id") == student_id), None)
    if not student_record:
        return {"retry_recommendations": [], "extension_recommendations": []}

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

    # 准备知识点过滤条件
    scope_knowledge = set()
    if config["knowledge_filter"] == "exact_match" and current_knowledge:
        scope_knowledge = {current_knowledge}
    elif config["knowledge_filter"] == "in_scope":
        if review_scope:
            scope_knowledge = set(k.strip() for k in review_scope.split(","))
        elif exam_scope:
            scope_knowledge = set(k.strip() for k in exam_scope.split(","))
    elif config["knowledge_filter"] == "weak_tags":
        scope_knowledge = set(weak_tags.keys())

    retry_candidates = []
    extension_candidates = []

    for question in question_bank:
        question_id = question["question_id"]
        tags = tags_by_question.get(question_id)
        if not tags:
            continue

        # 黑名单过滤
        if question_id in blacklist:
            continue

        answer = answers.get(question_id)
        attempted = answer is not None
        if attempted and question_id not in wrong_question_ids:
            continue

        primary = tags["primary_knowledge"]
        teaching_tag = tags.get("teaching_tag", "")
        difficulty = tags.get("difficulty", 3)

        # 应用难度校准
        if question_id in calibration:
            difficulty += calibration[question_id]
            difficulty = max(1, min(5, difficulty))

        # 知识点过滤
        if config["knowledge_filter"] == "exact_match" and scope_knowledge:
            if primary not in scope_knowledge:
                continue
        elif config["knowledge_filter"] == "in_scope" and scope_knowledge:
            if primary not in scope_knowledge:
                continue
        elif config["knowledge_filter"] == "weak_tags":
            if primary not in scope_knowledge:
                continue

        # 教学标签过滤
        if config["teaching_tags"] and teaching_tag not in config["teaching_tags"]:
            continue

        # 难度范围过滤
        if config["difficulty_range"]:
            min_d, max_d = config["difficulty_range"]
            if not (min_d <= difficulty <= max_d):
                continue

        mastery_row = weak_tags.get(primary)
        question_count = mastery_row.get("question_count", 0) if mastery_row else 0
        confidence_weight = min(question_count / 4, 1.0)
        weakness = (1 - mastery_row["mastery_score"]) * confidence_weight if mastery_row else 0.2
        already_wrong_boost = config["wrong_boost"] if question_id in wrong_question_ids else 0.0
        unattempted_boost = config["unattempted_boost"] if not attempted else 0.0
        difficulty_fit = _difficulty_fit(difficulty, mastery_row["mastery_score"] if mastery_row else 0.75, config.get("difficulty_range"))
        score = round(
            config["weakness_weight"] * weakness
            + config["difficulty_fit_weight"] * difficulty_fit
            + already_wrong_boost
            + unattempted_boost,
            4,
        )

        candidate = {
            "student_id": student_id,
            "question_id": question_id,
            "strategy": "补弱题" if mastery_row else "巩固题",
            "score": score,
            "reason": (
                f"{primary} 掌握度偏低（基于 {question_count} 道题），当前掌握度 {mastery_row['mastery_score']:.2f}"
                if mastery_row
                else f"{primary} 可用于巩固"
            ),
            "primary_knowledge": primary,
            "difficulty": difficulty,
            "question_type": question.get("question_type"),
            "stem_preview": _preview(question.get("stem_markdown", ""), 120),
            "source": "wrong_question" if question_id in wrong_question_ids else "unattempted_question",
            "question_count": question_count,
            "confidence_weight": round(confidence_weight, 2),
        }

        if question_id in wrong_question_ids:
            retry_candidates.append(candidate)
        else:
            extension_candidates.append(candidate)

    retry_sorted = _weighted_round_robin(retry_candidates, top_n, weak_tags, threshold=0.4)
    extension_sorted = _weighted_round_robin(extension_candidates, top_n, weak_tags, threshold=0.4)

    # 题型配额控制（优先使用用户自定义配额）
    type_distribution = custom_type_distribution if custom_type_distribution else config.get("question_type_distribution")
    if type_distribution:
        # 合并候选池，在全局范围内应用题型配额
        total_n = sum(type_distribution.values())

        # 标记来源
        for c in retry_sorted:
            c["source"] = "wrong_question"
        for c in extension_sorted:
            c["source"] = "unattempted_question"

        # 合并候选池
        all_candidates = retry_sorted + extension_sorted

        # 题型配额优先：如果某种题型候选不足，从原始候选池补充
        for q_type, count in type_distribution.items():
            type_in_pool = [c for c in all_candidates if c.get("question_type") == q_type]
            if len(type_in_pool) < count:
                # 从原始候选池中补充同类型题目（按 score 降序，避免重复）
                used_ids = {c["question_id"] for c in all_candidates}
                supplement = sorted(
                    [c for c in retry_candidates + extension_candidates
                     if c.get("question_type") == q_type and c["question_id"] not in used_ids],
                    key=lambda c: c["score"],
                    reverse=True,
                )
                for c in supplement[: count - len(type_in_pool)]:
                    all_candidates.append(c)

        # 按 score 降序排列
        all_candidates.sort(key=lambda c: c["score"], reverse=True)

        # 应用全局题型配额
        selected = _apply_type_quota(all_candidates, type_distribution, total_n)

        # 按来源重新分回两个分区
        retry_sorted = [c for c in selected if c.get("source") == "wrong_question"]
        extension_sorted = [c for c in selected if c.get("source") == "unattempted_question"]
    else:
        retry_sorted = retry_sorted[:top_n]
        extension_sorted = extension_sorted[:top_n]

    return {
        "retry_recommendations": retry_sorted,
        "extension_recommendations": extension_sorted,
    }


def _render_html(payload):
    """渲染完整 HTML 文档（含 <!DOCTYPE>、MathJax、全局样式、全局脚本）。

    模块化拼装：使用说明 → 学生画像 → 薄弱点 → 覆盖度警告 → 题目卡片 → 总体建议 → 导出栏。
    全局 CSS 与 JS 统一来自 recommendation_template 模块，确保每次输出结构一致。
    """
    student = payload["student"]
    scenario_name = payload.get("scenario_name", "考后强化")
    scenario_desc = payload.get("scenario_description", "")
    data_source = payload.get("data_source", "")

    retry = payload["recommendations"].get("retry_recommendations", [])
    extension = payload["recommendations"].get("extension_recommendations", [])
    all_recs = retry + extension

    title = f"{student.get('name', '')} - 考后强化推荐题"

    body_parts = [
        USAGE_NOTE_HTML,
        _render_student_profile(student, scenario_name, scenario_desc, data_source),
        _render_weak_points(payload.get("weak_points", [])),
        _render_coverage_gaps(payload.get("coverage_gaps", [])),
        _render_question_cards(all_recs),
        OVERALL_FEEDBACK_HTML,
        EXPORT_BAR_HTML,
    ]
    body = "\n".join(p for p in body_parts if p)
    return HTML_HEAD_TEMPLATE.format(title=_e(title)) + body + HTML_FOOTER


def _render_student_profile(student, scenario_name, scenario_desc, data_source):
    """学生画像：姓名/班级/总分 + 题型得分明细表格。"""
    breakdown = student.get("score_breakdown") or {}
    type_labels = {
        "single_choice": "单选题",
        "multiple_choice": "多选题",
        "fill_blank": "填空题",
        "solution": "解答题",
    }
    type_order = ["single_choice", "multiple_choice", "fill_blank", "solution"]
    rows_html = ""
    for qtype in type_order:
        if qtype in breakdown:
            b = breakdown[qtype]
            label = type_labels.get(qtype, qtype)
            score = _fmt_score(b.get("score", 0))
            full = _fmt_score(b.get("full_score", 0))
            rows_html += f"        <tr><th>{_e(label)}</th><td>{_e(score)}</td><td>{_e(full)}</td></tr>\n"

    return f"""    <section class="sq-profile">
      <h3>智能推题结果：{_e(student.get('name', ''))}（{_e(student.get('student_id', ''))}）</h3>
      <p><strong>班级：</strong>{_e(student.get('class', ''))}　<strong>期中总分：</strong>{_e(student.get('total_score', ''))}</p>
      <table class="sq-profile-table">
        <thead><tr><th>题型</th><th>得分</th><th>总分</th></tr></thead>
        <tbody>
{rows_html}        </tbody>
      </table>
      <p><strong>数据来源：</strong>{_e(data_source)}</p>
      <p><strong>场景：</strong>{_e(scenario_name)}　<em>{_e(scenario_desc)}</em></p>
    </section>"""


def _render_weak_points(weak_points):
    """薄弱点列表（掌握度 < 0.8）。"""
    if not weak_points:
        return ""
    rows = "\n".join(
        f"      <li><strong>{_e(item['tag_name'])}</strong>：掌握度 {_e(item['mastery_score'])}（基于 {item.get('question_count', '?')} 道题），状态 {_e(item['status'])}</li>"
        for item in weak_points
    )
    return f"""    <section class="sq-weak-points">
      <h4>主要薄弱点（掌握度 &lt; 0.8）</h4>
      <ol>
{rows}
      </ol>
    </section>"""


def _render_coverage_gaps(coverage_gaps):
    """覆盖度警告（条件渲染：题库覆盖不足时才出现）。"""
    if not coverage_gaps:
        return ""
    gap_rows = "\n".join(
        f"        <li><strong>{_e(g['tag_name'])}</strong>：题库仅 {_e(g['available'])} 题（建议至少 3 题）</li>"
        for g in coverage_gaps
    )
    return f"""    <div class="sq-coverage">
      <h4 style="margin: 0 0 8px; font-size: 15px;">⚠️ 以下薄弱点题库覆盖不足，建议生成变式题</h4>
      <ul style="margin: 0; padding-left: 20px;">
{gap_rows}
      </ul>
      <p style="margin: 8px 0 0; font-size: 13px; color: #666;">
        💡 提示：生成后可运行 <code>python scripts/generate_variants.py export-prompts --target-knowledge "..." --question-type single_choice --difficulty 3 --count 2</code>
      </p>
    </div>"""


def _render_question_cards(recommendations):
    """题目卡片容器：合并 retry + extension，重新编号。"""
    if not recommendations:
        return '    <section class="sq-cards"><h4>完整题面与答案解析</h4><p style="color:#888;">暂无推荐</p></section>'
    cards = "\n".join(
        _render_question_card(item, idx)
        for idx, item in enumerate(recommendations, start=1)
    )
    return f"""    <section class="sq-cards">
      <h4>完整题面与答案解析</h4>
{cards}
    </section>"""



def _render_markdown(payload):
    student = payload["student"]
    scenario_name = payload.get("scenario_name", "考后强化")
    # 题型得分明细
    breakdown = student.get("score_breakdown") or {}
    type_labels = {
        "single_choice": "单选题",
        "multiple_choice": "多选题",
        "fill_blank": "填空题",
        "solution": "解答题",
    }
    type_order = ["single_choice", "multiple_choice", "fill_blank", "solution"]
    breakdown_parts = []
    for qtype in type_order:
        if qtype in breakdown:
            b = breakdown[qtype]
            label = type_labels.get(qtype, qtype)
            score = _fmt_score(b.get("score", 0))
            full = _fmt_score(b.get("full_score", 0))
            breakdown_parts.append(f"{label}：{score}/{full}")
    breakdown_line = "、".join(breakdown_parts)
    lines = [
        f"### 学生：{student['name']}（{student['student_id']}）",
        f"- 班级：{student.get('class', '')}",
        f"- 期中总分：{student.get('total_score', '')}",
    ]
    if breakdown_line:
        lines.append(f"- 题型得分：{breakdown_line}")
    lines.extend([
        f"- 数据来源：{payload.get('data_source', '')}",
        f"- 场景：{scenario_name}",
        "",
        "#### 主要薄弱点（掌握度 < 0.8）",
    ])
    for item in payload["weak_points"]:
        lines.append(
            f"- {item['tag_name']}：掌握度 {item['mastery_score']}（基于 {item.get('question_count', '?')} 道题），状态 {item['status']}"
        )

    retry = payload["recommendations"].get("retry_recommendations", [])
    extension = payload["recommendations"].get("extension_recommendations", [])

    lines.extend(["", "#### 重做推荐（错题再练）", "| 推荐题目 | 知识点 | 难度 | 来源 | 推荐原因 |", "| --- | --- | --- | --- | --- |"])
    for item in retry:
        lines.append(f"| `{item['question_id']}` | {item['primary_knowledge']} | {item['difficulty']} | {item['source']} | {item['reason']} |")
    if not retry:
        lines.append("| *暂无* | | | | |")

    lines.extend(["", "#### 拓展推荐（同类新题）", "| 推荐题目 | 知识点 | 难度 | 来源 | 推荐原因 |", "| --- | --- | --- | --- | --- |"])
    for item in extension:
        lines.append(f"| `{item['question_id']}` | {item['primary_knowledge']} | {item['difficulty']} | {item['source']} | {item['reason']} |")
    if not extension:
        lines.append("| *暂无* | | | | |")

    return "\n".join(lines)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_jsonl(path):
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _attach_question_details(recommendations, tagged_by_question, questions_root):
    attached = []
    for item in recommendations:
        rich = dict(item)
        question_id = item["question_id"]
        tagged = tagged_by_question.get(question_id, {})
        detail = _load_question_detail(questions_root, question_id) or tagged
        question_dir = Path(questions_root) / question_id if questions_root else None
        tags = tagged.get("tags", {})
        rich["question_detail"] = {
            "question_id": question_id,
            "question_number": detail.get("question_number", tagged.get("question_number")),
            "question_type": detail.get("question_type", tagged.get("question_type")),
            "stem_html": _localize_asset_refs(
                _repair_question_html(
                    detail.get("stem_html") or _paragraphize(detail.get("stem_markdown") or tagged.get("stem_markdown", "")),
                    detail,
                ),
                question_dir,
            ),
            "answer": detail.get("answer", tagged.get("answer", "")),
            "solution_html": _localize_asset_refs(
                _repair_question_html(detail.get("solution_html") or _paragraphize(detail.get("solution_markdown") or ""), detail),
                question_dir,
            ),
            "primary_knowledge": tags.get("primary_knowledge", item.get("primary_knowledge")),
            "secondary_knowledge": tags.get("secondary_knowledge", []),
            "ability_tags": tags.get("ability_tags", []),
            "difficulty_basis": tags.get("difficulty_basis", ""),
            "error_prone_points": tags.get("error_prone_points", []),
            "source_found": bool(detail),
        }
        attached.append(rich)
    return attached


def _load_question_detail(questions_root, question_id):
    if not questions_root:
        return None
    path = Path(questions_root) / question_id / "question.json"
    if not path.exists():
        return None
    return _read_json(path)


def _render_question_card(item, idx):
    """渲染单张题目卡片（含反馈表单）。

    公式渲染保留题源原始 LaTeX `\\(...\\)` / `\\[...\\]`，由浏览器端 MathJax 统一渲染。
    """
    detail = item.get("question_detail") or {}
    secondary = "；".join(detail.get("secondary_knowledge") or [])
    abilities = "；".join(detail.get("ability_tags") or [])
    errors = "；".join(detail.get("error_prone_points") or [])
    solution = detail.get("solution_html") or "<p>暂无解析。</p>"
    question_id = _e(item['question_id'])
    short_id = question_id.replace('-', '_')
    primary_knowledge = _e(detail.get('primary_knowledge', ''))

    feedback_form = _render_feedback_form(question_id, short_id, primary_knowledge)

    return f"""      <article class="sq-card">
        <h5>{idx}. <code>{question_id}</code></h5>
        <div class="sq-card-meta"><strong>题型：</strong>{_e(detail.get('question_type', ''))}　<strong>难度：</strong>{_e(item.get('difficulty', ''))}　<strong>来源：</strong>{_e(item.get('source', ''))}</div>
        <div class="sq-card-meta"><strong>主知识点：</strong>{primary_knowledge}　<strong>副知识点：</strong>{_e(secondary)}　<strong>能力：</strong>{_e(abilities)}</div>
        <div class="sq-card-meta"><strong>推荐原因：</strong>{_e(item.get('reason', ''))}</div>
        <div class="sq-card-section"><strong>完整题面</strong>{detail.get('stem_html', '')}</div>
        <div class="sq-card-section"><strong>答案</strong><p>{_e(detail.get('answer', '') or '暂无答案')}</p></div>
        <div class="sq-card-section"><strong>答案与解析</strong>{solution}</div>
        <div class="sq-card-meta"><strong>难度依据：</strong>{_e(detail.get('difficulty_basis', ''))}　<strong>易错点：</strong>{_e(errors)}</div>
{feedback_form}
      </article>"""


def _render_feedback_form(question_id, short_id, primary_knowledge):
    """两层教师反馈表单：第一层快速标记，第二层动态展开详情。

    全局 JS 函数 updateFeedbackDetail / exportAllFeedback 定义在 recommendation_template.HTML_FOOTER 中，
    本函数只输出表单 HTML，不再内嵌 <script>。
    """
    return f"""        <div class="sq-feedback">
          <details>
            <summary>教师反馈</summary>
            <div class="sq-feedback-panel" id="fb-{question_id}">
              <div class="sq-feedback-quick">
                <div class="sq-feedback-quick-label">快速标记：</div>
                <div class="sq-feedback-quick-options">
                  <label><input type="radio" name="feedback-main-{short_id}" value="accept" checked style="margin-right: 4px;" onchange="updateFeedbackDetail('{short_id}')"> 接受推荐</label>
                  <label><input type="radio" name="feedback-main-{short_id}" value="difficulty" style="margin-right: 4px;" onchange="updateFeedbackDetail('{short_id}')"> 难度不合适</label>
                  <label><input type="radio" name="feedback-main-{short_id}" value="knowledge" style="margin-right: 4px;" onchange="updateFeedbackDetail('{short_id}')"> 知识点标签有误</label>
                  <label><input type="radio" name="feedback-main-{short_id}" value="other" style="margin-right: 4px;" onchange="updateFeedbackDetail('{short_id}')"> 其他问题</label>
                </div>
              </div>
              <div id="feedback-detail-{short_id}" class="sq-feedback-detail">
                <div class="fb-section" data-for="difficulty">
                  <div class="sq-feedback-detail-label">难度校准（请选择一个）：</div>
                  <div class="sq-feedback-detail-options">
                    <label><input type="radio" name="difficulty-{short_id}" value="too_easy"> 过易</label>
                    <label><input type="radio" name="difficulty-{short_id}" value="easy"> 稍易</label>
                    <label><input type="radio" name="difficulty-{short_id}" value="fit"> 适中</label>
                    <label><input type="radio" name="difficulty-{short_id}" value="hard"> 稍难</label>
                    <label><input type="radio" name="difficulty-{short_id}" value="too_hard"> 过难</label>
                  </div>
                </div>
                <div class="fb-section" data-for="knowledge">
                  <div class="sq-feedback-detail-label">知识点标签校准：</div>
                  <div class="sq-feedback-current-tag">当前标签：{primary_knowledge}</div>
                  <input type="text" id="fb-knowledge-{short_id}" class="sq-feedback-text" placeholder="请填写您认为更准确的知识点标签...">
                </div>
                <div class="fb-section" data-for="other">
                  <div class="sq-feedback-detail-label">问题描述：</div>
                  <textarea id="fb-other-{short_id}" class="sq-feedback-text" rows="2" placeholder="请描述您发现的问题（如题干错误、答案有误等）..."></textarea>
                </div>
              </div>
            </div>
          </details>
        </div>"""


def _paragraphize(markdown_text):
    paragraphs = [line.strip() for line in str(markdown_text).splitlines() if line.strip()]
    return "\n".join(f"<p>{_e(line)}</p>" for line in paragraphs)


def _render_mathml_formulas(html_text):
    return re.sub(
        r'<span class="formula" data-formula-id="[^"]+">(.*?)</span>',
        lambda match: _text_formula_to_mathml(html.unescape(match.group(1))),
        str(html_text),
        flags=re.DOTALL,
    )


def _text_formula_to_mathml(text):
    text = str(text).strip()
    if not text:
        return ""
    return f'<math xmlns="http://www.w3.org/1998/Math/MathML">{_mathml_sequence(text)}</math>'


def _mathml_sequence(text):
    text = str(text).strip()
    fraction = _split_top_level_fraction(text)
    if fraction:
        numerator, denominator = fraction
        return f"<mfrac>{_mathml_group(numerator)}{_mathml_group(denominator)}</mfrac>"

    pieces = []
    index = 0
    pattern = r"sqrt\(([^()]+)\)|\(([^()]+)\)/\(([^()]+)\)"
    for match in re.finditer(pattern, text):
        if match.start() > index:
            pieces.append(_mathml_plain(text[index : match.start()]))
        if match.group(1) is not None:
            pieces.append(f"<msqrt>{_mathml_sequence(match.group(1))}</msqrt>")
        else:
            pieces.append(f"<mfrac>{_mathml_group(match.group(2))}{_mathml_group(match.group(3))}</mfrac>")
        index = match.end()
    if index < len(text):
        pieces.append(_mathml_plain(text[index:]))
    return "".join(pieces)


def _mathml_group(text):
    return f"<mrow>{_mathml_sequence(text)}</mrow>"


def _split_top_level_fraction(text):
    if text.startswith("("):
        close = _matching_paren_index(text, 0)
        if close is not None and text[close + 1 : close + 3] == "/(" and text.endswith(")"):
            return text[1:close], text[close + 3 : -1]
    return None


def _matching_paren_index(text, start):
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == "(":
            depth += 1
        elif text[idx] == ")":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _mathml_plain(text):
    tokens = re.findall(r"[A-Za-z]+|[0-9]+|[π∞]|[+\-*/=<>≤≥∈∪,|{}()[\]]|_|\^|'|\.|[^A-Za-z0-9+\-*/=<>≤≥∈∪,|{}()[\]_^'\.π∞]+", text)
    output = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if idx + 2 < len(tokens) and tokens[idx + 1] in {"^", "_"}:
            base = _mathml_token(token)
            script = _mathml_token(tokens[idx + 2])
            tag = "msup" if tokens[idx + 1] == "^" else "msub"
            output.append(f"<{tag}>{base}{script}</{tag}>")
            idx += 3
            continue
        output.append(_mathml_token(token))
        idx += 1
    return "".join(output)


def _repair_question_html(html_text, detail):
    html_text = str(html_text)
    stem_formula = _first_formula_text(detail.get("stem_html", ""))
    if stem_formula:
        html_text = re.sub(
            r'(<span class="formula" data-formula-id="[^"]+">)[0-9a-f]{32}(</span>)',
            lambda match: f"{match.group(1)}{html.escape(stem_formula)}{match.group(2)}",
            html_text,
        )
    if "(1,2)" in (detail.get("solution_text", "") or ""):
        html_text = re.sub(
            r'(<span class="formula" data-formula-id="[^"]+">)\(1\)(</span>)',
            r"\1(1,2)\2",
            html_text,
        )
    return html_text


def _first_formula_text(html_text):
    match = re.search(r'<span class="formula" data-formula-id="[^"]+">(.*?)</span>', str(html_text))
    return html.unescape(match.group(1)) if match else ""


def _mathml_token(token):
    token = str(token)
    if not token:
        return ""
    if token.isspace():
        return '<mspace width="0.35em"/>'
    escaped = _e(token)
    if re.fullmatch(r"[0-9]+", token):
        return f"<mn>{escaped}</mn>"
    if re.fullmatch(r"[A-Za-z]+|[π∞]", token):
        return f"<mi>{escaped}</mi>"
    if token in {"(", ")", "[", "]", "{", "}", ",", "|"}:
        return f"<mo>{escaped}</mo>"
    return f"<mo>{escaped}</mo>"


def _localize_asset_refs(html_text, question_dir):
    """将题目中的 assets/ 图片引用替换为 base64 data URI，确保 HTML 离线可用。"""
    if not question_dir:
        return str(html_text)
    assets_dir = Path(question_dir) / "assets"
    if not assets_dir.exists():
        return str(html_text)

    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }

    def _replace_src(match):
        prefix = match.group(1)  # src=" or src='
        asset_path_str = match.group(2)
        # Handle both "assets/xxx" and absolute file:/// paths
        if asset_path_str.startswith("assets/"):
            asset_file = assets_dir / Path(asset_path_str).name
        elif asset_path_str.startswith("file:///"):
            # Already an absolute path, try to resolve
            asset_file = Path(asset_path_str.replace("file:///", ""))
        else:
            return match.group(0)

        if not asset_file.exists():
            return match.group(0)

        ext = asset_file.suffix.lower()
        mime = mime_map.get(ext, "image/png")
        try:
            with open(asset_file, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            return f'{prefix}data:{mime};base64,{data}"'
        except Exception:
            return match.group(0)

    pattern = re.compile(r'(src=["\'])(assets/[^"\']+|file:///[^"\']+)')
    return pattern.sub(_replace_src, str(html_text))


def _resolve_data_paths(workspace_arg, data_dir_arg=None):
    """Resolve the four data paths (tags/mastery/records/questions) used by the recommender.

    Resolve only caller-supplied data. Bundled reference data is a test fixture and
    must never be mistaken for the current teacher's bank.
    """
    # Priority 1: explicit --data-dir
    if data_dir_arg:
        data_root = Path(data_dir_arg).resolve()
        dir_paths = {
            "tags": data_root / "tags" / "all_question_tags.json",
            "mastery": data_root / "student" / "student_mastery.jsonl",
            "records": data_root / "student" / "student_exam_records.jsonl",
            "questions": data_root / "questions",
            "source": f"data-dir:{data_root}",
        }
        missing = [str(p) for k, p in dir_paths.items() if k != "source" and not p.exists()]
        if missing:
            raise SystemExit(
                f"--data-dir 指定的目录 {data_root} 缺少必要文件：{'; '.join(missing)}。"
                f"请确认该目录结构为 tags/all_question_tags.json、student/student_mastery.jsonl、"
                f"student/student_exam_records.jsonl、questions/<qid>/question.json。"
            )
        return dir_paths

    # Priority 2: --workspace
    if workspace_arg:
        workspace = Path(workspace_arg).resolve()
        structured = workspace / "output" / "full"
        records_candidates = [
            structured / "student" / "student_exam_records.jsonl",
            workspace / "test-data" / "student_exam_records.jsonl",
        ]
        records_path = next((path for path in records_candidates if path.exists()), records_candidates[0])
        workspace_paths = {
            "tags": structured / "tags" / "all_question_tags.json",
            "mastery": structured / "student" / "student_mastery.jsonl",
            "records": records_path,
            "questions": structured / "questions",
            "source": f"workspace:{workspace}",
        }
        required_keys = ("tags", "mastery", "records", "questions")
        existing = [key for key in required_keys if workspace_paths[key].exists()]
        if len(existing) == len(required_keys):
            return workspace_paths
        missing_keys = [key for key in required_keys if key not in existing]
        missing_paths = [str(workspace_paths[k]) for k in missing_keys]
        raise SystemExit(
            f"--workspace {workspace} 数据不完整，缺少：{'; '.join(missing_paths)}。"
            f"请确认目录结构，或改用 --data-dir 指向结构化题库根目录。"
        )

    raise SystemExit("请使用 --data-dir 指定结构化题库，或使用 --workspace 指定当前项目；不会自动加载内置示例数据。")


def _apply_type_quota(candidates, distribution, top_n):
    """按题型配额从候选池中挑选题目。"""
    if not distribution:
        return candidates[:top_n]
    result = []
    used_ids = set()
    # 第一遍：按配额严格挑选
    for q_type, count in distribution.items():
        type_count = 0
        for c in candidates:
            if c.get("question_type") == q_type and c["question_id"] not in used_ids:
                result.append(c)
                used_ids.add(c["question_id"])
                type_count += 1
                if type_count >= count:
                    break
    # 第二遍：如果总数量不足 top_n，用剩余候选补足
    if len(result) < top_n:
        for c in candidates:
            if c["question_id"] not in used_ids:
                result.append(c)
                used_ids.add(c["question_id"])
                if len(result) >= top_n:
                    break
    return result


def _weighted_round_robin(candidates, top_n, weak_tags, threshold=0.4):
    """加权轮询：掌握度低于阈值的知识点每轮取2道，否则取1道"""
    if not candidates:
        return []

    # 1. 按知识点分组（每组内部按 score 降序）
    groups = defaultdict(list)
    for c in candidates:
        groups[c["primary_knowledge"]].append(c)
    for pk in groups:
        groups[pk].sort(key=lambda c: c["score"], reverse=True)

    # 2. 知识点按掌握度排序（越低越优先）
    pks = sorted(
        groups.keys(),
        key=lambda pk: weak_tags.get(pk, {}).get("mastery_score", 1),
    )

    # 3. 分配权重
    weights = {}
    for pk in pks:
        mastery = weak_tags.get(pk, {}).get("mastery_score", 1)
        weights[pk] = 2 if mastery < threshold else 1

    # 4. 用 deque 做加权轮询
    queues = {pk: deque(groups[pk]) for pk in pks}
    result, used = [], set()

    while len(result) < top_n:
        has_more = False
        for pk in pks:
            for _ in range(weights[pk]):
                found = False
                while queues[pk]:
                    item = queues[pk].popleft()
                    if item["question_id"] not in used:
                        result.append(item)
                        used.add(item["question_id"])
                        found = True
                        has_more = True
                        break
                if not found or len(result) >= top_n:
                    break
            if len(result) >= top_n:
                break

        if not has_more:
            break

    return result


def _difficulty_fit(difficulty, mastery_score, target_range=None):
    if target_range:
        low, high = target_range
        target = (low + high) / 2
    else:
        target = 2 if mastery_score < 0.6 else 3
    return max(0.0, 1 - abs(difficulty - target) / 4)


def _preview(text, target=120):
    text = re.sub(r"<[^>]+>", "", str(text))
    text = " ".join(text.split())
    if len(text) <= target:
        return text
    # 在目标长度附近找最近的语义断点
    search_start = max(0, target - 30)
    search_end = min(len(text), target + 30)
    snippet = text[search_start:search_end]
    # 优先匹配完整语义边界（按优先级排序）
    boundaries = re.finditer(r"[;；]|[:：]|[.．]|[?？]|[!！]|(?:\\)|[,，]", snippet)
    best = None
    for m in boundaries:
        pos = search_start + m.end()
        if pos <= target and (best is None or pos > best):
            best = pos
    if best is None:
        # 回退：找空格
        space_match = list(re.finditer(r" ", snippet))
        for m in reversed(space_match):
            pos = search_start + m.start()
            if pos <= target:
                best = pos
                break
    if best is None:
        best = target
    return text[:best].rstrip() + "…"


def _e(value):
    return html.escape(str(value))


def _fmt_score(value):
    """格式化分数：整数去尾零，小数保留一位。"""
    try:
        v = float(value)
        if v == int(v):
            return str(int(v))
        return f"{v:.1f}"
    except (TypeError, ValueError):
        return str(value)


def _load_feedback_calibrations(workspace_arg):
    """读取反馈校准数据，返回 (calibration_dict, blacklist_set)。"""
    calibration = {}
    blacklist = set()
    workspace = Path(workspace_arg).resolve() if workspace_arg else Path.cwd()
    feedback_dir = workspace / "output" / "feedback"
    if not feedback_dir.exists():
        return calibration, blacklist

    # 读取难度校准
    calib_path = feedback_dir / "difficulty_calibration.json"
    if calib_path.exists():
        try:
            calib_data = json.loads(calib_path.read_text(encoding="utf-8"))
            for adj in calib_data.get("adjustments", []):
                qid = adj.get("question_id")
                if qid:
                    calibration[qid] = adj.get("adjustment", 0)
        except (json.JSONDecodeError, OSError):
            pass

    # 读取黑名单
    blacklist_path = feedback_dir / "blacklist.json"
    if blacklist_path.exists():
        try:
            bl_data = json.loads(blacklist_path.read_text(encoding="utf-8"))
            for item in bl_data.get("items", []):
                qid = item.get("question_id")
                if qid:
                    blacklist.add(qid)
        except (json.JSONDecodeError, OSError):
            pass

    return calibration, blacklist


if __name__ == "__main__":
    main()
