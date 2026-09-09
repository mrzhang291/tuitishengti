from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from apply_teacher_review import apply_decisions, validate_decisions
from audit_tags import _audit_question
from build_review_workbench import build_workbench, tag_signature
from generate_student_performance import generate_performance
from review_server import WorkbenchHandler


APPLY_SCRIPT = SCRIPTS / "apply_teacher_review.py"


def sample_question() -> dict:
    return {
        "question_id": "demo_q001",
        "display_id": "DEMO-Q001",
        "source_exam": "演示试卷",
        "question_number": 1,
        "question_type": "solution",
        "stem_markdown": "已知参数 $a$，分类讨论函数的导数。",
        "stem_html": "<p>已知参数 $a$，分类讨论函数的导数。</p>",
        "solution_markdown": "求导后分类讨论。",
        "solution_html": "<p>求导后分类讨论。</p>",
        "answer": "略",
        "quality_flags": [],
        "tags": {
            "curriculum_theme": "T7导数",
            "knowledge_unit": "U7.2 导数运算（基本函数/复合/隐函数）",
            "skill_tags": ["基本函数求导", "复合函数求导", "导数运算"],
            "primary_knowledge": "导数与微分·求导运算·基本函数求导",
            "secondary_knowledge": [],
            "ability_tags": ["运算求解"],
            "difficulty": 3,
            "difficulty_basis": "model_estimated",
            "teaching_tag": "综合提升",
            "error_prone_points": [],
            "prerequisite_knowledge": [],
            "tag_reasoning": "规则判断",
            "tag_evidence": [{"source": "stem", "label": "求导", "match": "导数", "weight": 4.0}],
            "tag_candidates": [
                {"primary_knowledge": "导数与微分·求导运算·基本函数求导", "score": 4.0},
                {"primary_knowledge": "导数应用·单调性·判断单调区间", "score": 3.8},
            ],
            "classification_margin": 0.05,
            "tags_generated_by": "local_rules_v2_evidence_scored",
            "tags_reviewed": False,
            "tags_confidence": 0.68,
            "tags_confidence_detail": {
                "primary_knowledge": 0.66,
                "classification_margin": 0.05,
                "structure": 0.96,
            },
            "needs_teacher_review": True,
            "review_reasons": ["confidence_below_threshold"],
        },
    }


class TeacherReviewLoopTests(unittest.TestCase):
    def test_workbench_builds_prioritized_embedded_page_and_root_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "tags").mkdir()
            (bank / "audit").mkdir()
            (bank / "tags" / "all_question_tags.json").write_text(
                json.dumps([sample_question()], ensure_ascii=False), encoding="utf-8"
            )
            with (bank / "audit" / "tag_quality_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["question_id", "priority", "flags"])
                writer.writeheader()
                writer.writerow({"question_id": "demo_q001", "priority": "high", "flags": "unreviewed；classification_margin_low"})
            (bank / "index.html").write_text("<html><body><header>题库</header></body></html>", encoding="utf-8")

            result = build_workbench(bank)
            page = (bank / "review" / "index.html").read_text(encoding="utf-8")
            root = (bank / "index.html").read_text(encoding="utf-8")

            self.assertEqual(result["review_queue"], 1)
            self.assertEqual(result["priority_counts"]["high"], 1)
            self.assertIn("DEMO-Q001", page)
            self.assertIn("teacher-review-v1", page)
            self.assertNotIn(">智能组卷<", page)
            self.assertIn('"class_paper"', page)
            self.assertIn("组卷要求", page)
            self.assertIn("按以上要求重新组卷原题错题集", page)
            self.assertIn("原题错题集", page)
            self.assertIn("个性化智能新题", page)
            self.assertIn("3–5 道可追溯学情证据和唯一结构锚点", page)
            self.assertIn("按条件实时生成", page)
            self.assertIn("任务单驱动生成新题", page)
            self.assertIn("按知识点生成", page)
            self.assertIn("按学情生成", page)
            self.assertIn("rec-global-question-type", page)
            self.assertIn("rec-global-count", page)
            self.assertIn("/api/personalized-generation/draft", page)
            self.assertIn("/api/personalized-generation/verify", page)
            self.assertIn("generateLivePersonalQuestion", page)
            self.assertIn("按条件换原题", page)
            self.assertIn("重新生成结构新题", page)
            self.assertIn("student_generated_question_candidates", page)
            self.assertIn("single_anchor_task_spec", page)
            self.assertIn("diagnostic_evidence_question_ids", page)
            self.assertIn("fulltext_anchor_count", page)
            self.assertIn("structure_anchor", page)
            self.assertIn("source-wrong-question-set-v1", page)
            self.assertIn("class-source-wrong-question-set-v1", page)
            self.assertIn("generated-question-review-v5", page)
            self.assertIn("removedLegacyDifficulty", page)
            self.assertIn("personal-generation-config-v2", page)
            self.assertIn('id="tag-review-entry"', root)
            self.assertIn('href="http://127.0.0.1:8765/review/index.html"', root)
            self.assertIn("打开实时工作台", page)
            self.assertIn("static-mode", page)
            self.assertIn("live-quick-grid", page)
            self.assertIn("toolbar-more", page)
            self.assertIn("cleanAnswerContent", page)
            self.assertIn("formatMathNarrative", page)
            self.assertIn("timeoutMs=85000", page)
            self.assertIn("errorCode='client_stage_timeout'", page)
            self.assertIn("服务端 JSON 不完整", page)
            self.assertIn("rec-generation-pipeline", page)
            self.assertIn("学情任务单", page)
            self.assertIn("单母题起草", page)
            self.assertIn("Cherry 独立求解", page)
            self.assertIn("本地硬校验", page)
            self.assertIn("renderGenerationPipeline", page)
            self.assertIn("error.retryable", page)
            self.assertIn("error.errorCode", page)
            self.assertIn("preparePersonalQuestionTypeControls", page)
            self.assertIn("智能匹配（批量自动分配不同题型）", page)
            self.assertIn("supported_target_question_types", page)
            self.assertIn("学情证据的题型不会限制新题题型", page)
            self.assertIn("const typed=slots.filter", page)
            self.assertNotIn("questionType==='auto'||slot.question_type===questionType", page)
            self.assertIn("difficulty_matches===true", page)
            self.assertIn("!generatedVerified(question,rows[index])", page)
            self.assertIn("batchId", page)
            self.assertIn("rec-stop-generation", page)
            self.assertIn("/api/personalized-generation/cancel", page)
            self.assertIn("/api/personalized-generation/commit", page)
            self.assertIn("cancelAllGenerationBatches", page)
            self.assertIn("commitGenerationBatches", page)
            self.assertIn("rec-workspace", page)
            self.assertIn("rec-paper-builder", page)
            self.assertIn("data-paper-preview", page)
            self.assertIn("学生卷公式预览", page)
            self.assertIn("updatePersonalPaperPreview", page)
            self.assertIn("MathJax.typesetClear", page)
            self.assertIn("rec-paper-mode-preview", page)
            self.assertIn("rec-paper-mode-edit", page)
            self.assertIn("personalPaperViewMode", page)
            self.assertIn("paper-preview-mode", page)
            self.assertIn("paper-preview-answer-panel", page)
            self.assertIn("renderPersonalPaperMath", page)
            self.assertIn("公式已渲染", page)
            self.assertIn("Skill 安全蓝图", page)
            self.assertIn("draft_source", page)
            self.assertIn("preparePersonalDiversityRound", page)
            self.assertIn("diversity_round", page)
            self.assertIn("batch_size", page)
            self.assertIn("previousSlotQuestions", page)
            self.assertIn("previousCompatible", page)
            self.assertIn("recoveryBatchId", page)
            self.assertIn("recoveryRound<=2", page)
            self.assertIn("attemptStart=1,attemptEnd=5", page)
            self.assertIn("runSlots(rows.map((_,index)=>index),batchId,1,3)", page)
            self.assertIn(
                "runSlots(failedIndices,recoveryBatchId,3+recoveryRound,3+recoveryRound)",
                page,
            )
            self.assertIn("总预算不会重置", page)
            self.assertIn("defer_history_commit:resolvedBatchId.startsWith('batch-')", page)
            self.assertIn("commitVerifiedQuestion(activeBatchId,question)", page)
            self.assertIn("[question.question_id],1", page)
            self.assertIn("attempt<=3", page)
            self.assertIn("delivery_status:'sync_pending'", page)
            self.assertIn("retryStoredPendingCommits", page)
            self.assertIn("preserve_question_ids", page)
            self.assertIn("liveBatchSyncPendingQuestionIds.add(question.question_id)", page)
            self.assertIn("if(previousSlotQuestions[index])generatedStore[key]=previousSlotQuestions[index]", page)
            self.assertIn("已保留 ${kept}/${liveBatchState.total}", page)
            self.assertIn("道待补齐", page)
            self.assertIn("已停止生成；已保留", page)
            self.assertNotIn("batchRolledBack", page)
            self.assertNotIn("previousBatchComplete", page)
            self.assertNotIn("已撤回本轮结果", page)
            self.assertNotIn("生成结束：通过", page)
            self.assertIn("重新生成结构新题", page)
            self.assertIn("结构换题 · Cherry Studio", page)
            self.assertIn("last_regeneration_error", page)
            self.assertIn("上次换题失败，当前仍显示上一题", page)
            self.assertIn("失败时切换函数族", page)
            self.assertIn("personalReferenceResolution", page)
            self.assertIn("raw.length===ids.length", page)
            self.assertIn("refs.length===ids.length", page)
            self.assertIn("anchorKind==='skill_blueprint'", page)
            self.assertIn("一一对应原题", page)
            self.assertIn("查看后台学情证据", page)
            self.assertIn("provenance_validated_at", page)
            self.assertIn("liveGenerationController", page)
            self.assertIn("稳定补位", page)
            self.assertIn("每题总计最多", page)
            self.assertIn("batchDeadlineMs=Math.min(180000,90000+rows.length*20000)", page)
            self.assertIn("cancelAllGenerationBatches(cancellableLiveBatchIds())", page)
            self.assertIn("liveBatchState.pending", page)
            self.assertIn("待补位", page)
            self.assertIn("liveBatchBusy=false;liveGenerationController=null", page)
            self.assertIn("const hasRetained=rows.some", page)
            self.assertIn("setBatchButtonIdle(rows.length,hasRetained)", page)
            self.assertIn("loadLiveGenerationStatus(false).catch(()=>{})", page)
            self.assertIn("function saveGeneratedStore(){return safeLocalStorageSet", page)
            self.assertIn("function savePersonalConfigs(){return safeLocalStorageSet", page)
            self.assertIn("workspace.setAttribute('aria-busy'", page)
            self.assertIn("'rec-mode-knowledge','rec-mode-student','rec-student'", page)
            self.assertIn("control.disabled=liveBatchBusy", page)
            self.assertIn("removedStaleDraft", page)
            self.assertIn("personalQuestionTypeLabels", page)
            self.assertIn("single_choice:'单选题'", page)
            self.assertIn("multiple_choice:'多选题'", page)
            self.assertIn("智能匹配自动分配", page)
            self.assertIn("按教师选择", page)
            self.assertIn("<strong>实际题型：</strong>${esc(typeLabel)}", page)
            self.assertIn("personalQuestionTypeLabel(item.question.question_type)", page)
            self.assertIn("personalQuestionTypeSelectionLabel(item.question)", page)
            scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.DOTALL)
            if shutil.which("node"):
                syntax_file = bank / "inline-review-workbench.js"
                syntax_file.write_text("\n".join(scripts), encoding="utf-8")
                subprocess.run(["node", "--check", str(syntax_file)], check=True, capture_output=True, text=True)
                formatter_line = next(
                    line.strip() for line in page.splitlines() if "function formatMathNarrative(value)" in line
                )
                formatter_test = bank / "math-narrative-test.js"
                formatter_test.write_text(
                    formatter_line
                    + "\nconst raw=\"计算得 h(x) =[(x-1)(x^2+x+1)]/3 - ln x = (x^3-1)/3 - ln x，求导得 h'(x) = x^2 - 1/x = (x^3-1)/x，h(1)=0。当 0<x<1 时 h'(x)<0，x>1 时 h'(x)>0，故 x=1 为极小值点且 h(1)=0，即 h(x)≥0。已有 $f(x)=x^2$。\";"
                    + "\nconst result=formatMathNarrative(raw);"
                    + "\nif(!result.includes(\"$h(x) =[(x-1)(x^2+x+1)]/3 - \\\\ln x = (x^3-1)/3 - \\\\ln x$\"))process.exit(2);"
                    + "\nif(!result.includes(\"$h'(x) = x^2 - 1/x = (x^3-1)/x$\"))process.exit(3);"
                    + "\nif(!result.includes(\"$0<x<1$\")||!result.includes(\"$h'(x)<0$\")||!result.includes(\"$x>1$\")||!result.includes(\"$h(x)≥0$\"))process.exit(4);"
                    + "\nif(!result.includes(\"$f(x)=x^2$\"))process.exit(5);"
                    + "\nif(result.includes(\"$h(x)$ =\"))process.exit(6);",
                    encoding="utf-8",
                )
                subprocess.run(["node", str(formatter_test)], check=True, capture_output=True, text=True)

    def test_change_decision_updates_canonical_fields_and_marks_reviewed(self) -> None:
        tagged = [sample_question()]
        decision = {
            "question_id": "demo_q001",
            "decision": "change",
            "source_signature": tag_signature(tagged[0]["tags"]),
            "reviewer": "teacher",
            "reviewed_at": "2026-07-13T00:00:00Z",
            "review_notes": "主线是单调性",
            "teacher_tags": {
                "primary_knowledge": "导数应用·单调性·判断单调区间",
                "secondary_knowledge": [],
                "ability_tags": ["运算求解", "分类讨论"],
                "difficulty": 4,
            },
        }
        validated = validate_decisions(tagged, {"demo_q001": decision}, allow_stale=False)
        changes = apply_decisions(tagged, validated)
        tags = tagged[0]["tags"]

        self.assertEqual(len(changes), 1)
        self.assertEqual(tags["primary_knowledge"], "导数应用·单调性·判断单调区间")
        self.assertEqual(tags["knowledge_unit"], "U7.3 导数与函数单调性")
        self.assertEqual(tags["ability_tags"], ["运算求解", "分类讨论"])
        self.assertEqual(tags["difficulty"], 4)
        self.assertTrue(tags["tags_reviewed"])
        self.assertEqual(tags["teacher_review"]["reviewer"], "teacher")

    def test_reviewed_tag_suppresses_model_uncertainty_flags(self) -> None:
        question = sample_question()
        question["tags"]["tags_reviewed"] = True
        audit = _audit_question(question, threshold=0.8)
        self.assertEqual(audit["flags"], [])

    def test_apply_command_writes_backup_and_rebuilds_remaining_queue(self) -> None:
        question = sample_question()
        decision = {
            "schema_version": "teacher-review-v1",
            "question_id": "demo_q001",
            "display_id": "DEMO-Q001",
            "decision": "approve",
            "source_signature": tag_signature(question["tags"]),
            "reviewer": "teacher",
            "reviewed_at": "2026-07-13T00:00:00Z",
            "review_notes": "确认",
            "teacher_tags": {
                "primary_knowledge": question["tags"]["primary_knowledge"],
                "secondary_knowledge": [],
                "ability_tags": question["tags"]["ability_tags"],
                "difficulty": 3,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory) / "bank"
            (bank / "tags").mkdir(parents=True)
            (bank / "tags" / "all_question_tags.json").write_text(
                json.dumps([question], ensure_ascii=False), encoding="utf-8"
            )
            decisions = Path(directory) / "teacher_decisions.jsonl"
            decisions.write_text(json.dumps(decision, ensure_ascii=False) + "\n", encoding="utf-8")

            subprocess.run(
                [sys.executable, str(APPLY_SCRIPT), str(bank), str(decisions), "--no-audit"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            updated = json.loads((bank / "tags" / "all_question_tags.json").read_text(encoding="utf-8"))
            history = list((bank / "review" / "history").glob("*/teacher_decisions.jsonl"))
            workbench = (bank / "review" / "index.html").read_text(encoding="utf-8")

            self.assertTrue(updated[0]["tags"]["tags_reviewed"])
            self.assertEqual(len(history), 1)
            self.assertIn('"items":[]', workbench)

    def test_simulated_performance_has_question_and_student_level_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "tags").mkdir()
            (bank / "tags" / "all_question_tags.json").write_text(
                json.dumps([sample_question()], ensure_ascii=False), encoding="utf-8"
            )
            result = generate_performance(bank, student_count=12, seed=7)
            performance = json.loads((bank / "student" / "question_performance.json").read_text(encoding="utf-8"))
            records = [json.loads(line) for line in (bank / "student" / "simulated_student_records.jsonl").read_text(encoding="utf-8").splitlines()]
            stats = performance["questions"][0]

            self.assertEqual(result["student_count"], 12)
            self.assertEqual(len(records), 12)
            self.assertEqual(stats["attempts"] + stats["omitted"], 12)
            self.assertEqual(stats["correct"] + stats["wrong"], stats["attempts"])
            self.assertTrue(performance["simulated"])

    def test_local_server_autosaves_and_applies_review_without_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "tags").mkdir()
            question = sample_question()
            (bank / "tags" / "all_question_tags.json").write_text(
                json.dumps([question], ensure_ascii=False), encoding="utf-8"
            )
            build_workbench(bank)
            decision = {
                "schema_version": "teacher-review-v1",
                "question_id": "demo_q001",
                "display_id": "DEMO-Q001",
                "decision": "approve",
                "source_signature": tag_signature(question["tags"]),
                "reviewer": "teacher",
                "reviewed_at": "2026-07-13T00:00:00Z",
                "review_notes": "确认",
                "teacher_tags": {
                    "primary_knowledge": question["tags"]["primary_knowledge"],
                    "secondary_knowledge": [],
                    "ability_tags": question["tags"]["ability_tags"],
                    "difficulty": 3,
                },
            }

            def handler(*args, **kwargs):
                return WorkbenchHandler(*args, bank=bank, **kwargs)

            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                request = urllib.request.Request(
                    base + "/api/decision",
                    data=json.dumps(decision).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                apply_request = urllib.request.Request(
                    base + "/api/apply",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(apply_request) as response:
                    applied = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            updated = json.loads((bank / "tags" / "all_question_tags.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "saved")
            self.assertEqual(applied["applied"], 1)
            self.assertTrue(updated[0]["tags"]["tags_reviewed"])

    def test_local_server_disables_html_cache_for_workbench_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "review").mkdir()
            (bank / "review" / "index.html").write_text("<html>fresh</html>", encoding="utf-8")

            def handler(*args, **kwargs):
                return WorkbenchHandler(*args, bank=bank, **kwargs)

            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/review/index.html"
                ) as response:
                    self.assertIn("no-store", response.headers.get("Cache-Control", ""))
                    self.assertEqual(response.headers.get("Pragma"), "no-cache")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_local_server_exposes_live_personalized_generation_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "tags").mkdir()
            (bank / "tags" / "all_question_tags.json").write_text(
                json.dumps([sample_question()], ensure_ascii=False), encoding="utf-8"
            )
            generated = {
                "status": "generated",
                "mode": "cherry_studio_live",
                "question": {"question_id": "gen_live_001", "teacher_review": {"status": "pending"}},
                "references": [],
            }

            def handler(*args, **kwargs):
                return WorkbenchHandler(*args, bank=bank, **kwargs)

            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with (
                    patch(
                        "review_server.gateway_status",
                        return_value={"configured": True, "reachable": True, "authenticated": True, "model": "demo:model"},
                    ),
                    patch(
                        "review_server.generate_personalized_draft",
                        return_value={"status": "draft", "draft": {"stem_markdown": "新题草稿"}},
                    ) as create_draft,
                    patch(
                        "review_server.verify_personalized_draft",
                        return_value=generated,
                    ) as verify_draft,
                    patch("review_server.generate_personalized_question", return_value=generated) as generate,
                    patch(
                        "review_server.save_personalized_review",
                        return_value={"status": "saved", "question_id": "gen_live_001"},
                    ) as save_review,
                    patch(
                        "review_server.commit_personalized_batch",
                        return_value={"status": "committed", "question_count": 1},
                    ) as commit_batch,
                    patch(
                        "review_server.cancel_personalized_batch",
                        return_value={"status": "cancelled", "batch_id": "batch-live-001"},
                    ) as cancel_batch,
                ):
                    with urllib.request.urlopen(base + "/api/personalized-generation/status") as response:
                        status = json.loads(response.read().decode("utf-8"))
                    generate_request = urllib.request.Request(
                        base + "/api/personalized-generation/generate",
                        data=json.dumps({"student_id": "S001", "knowledge": "函数"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(generate_request) as response:
                        generated_response = json.loads(response.read().decode("utf-8"))
                    draft_request = urllib.request.Request(
                        base + "/api/personalized-generation/draft",
                        data=json.dumps({"mode": "knowledge", "knowledge": "函数"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(draft_request) as response:
                        draft_response = json.loads(response.read().decode("utf-8"))
                    verify_request = urllib.request.Request(
                        base + "/api/personalized-generation/verify",
                        data=json.dumps({"mode": "knowledge", "knowledge": "函数", "draft": {}}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(verify_request) as response:
                        verified_response = json.loads(response.read().decode("utf-8"))
                    commit_payload = {
                        "batch_ids": ["batch-live-001"],
                        "question_ids": ["gen_live_001"],
                        "expected_count": 1,
                    }
                    commit_request = urllib.request.Request(
                        base + "/api/personalized-generation/commit",
                        data=json.dumps(commit_payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(commit_request) as response:
                        commit_response = json.loads(response.read().decode("utf-8"))
                    cancel_payload = {
                        "batch_id": "batch-live-001",
                        "preserve_question_ids": ["gen_live_001"],
                    }
                    cancel_request = urllib.request.Request(
                        base + "/api/personalized-generation/cancel",
                        data=json.dumps(cancel_payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(cancel_request) as response:
                        cancel_response = json.loads(response.read().decode("utf-8"))
                    review_request = urllib.request.Request(
                        base + "/api/personalized-generation/review",
                        data=json.dumps({"question": generated["question"], "status": "approved"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(review_request) as response:
                        review_response = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertTrue(status["reachable"])
            self.assertEqual(generated_response["mode"], "cherry_studio_live")
            self.assertEqual(draft_response["status"], "draft")
            self.assertEqual(verified_response["mode"], "cherry_studio_live")
            self.assertEqual(commit_response["status"], "committed")
            self.assertEqual(cancel_response["status"], "cancelled")
            self.assertEqual(review_response["status"], "saved")
            create_draft.assert_called_once()
            verify_draft.assert_called_once()
            generate.assert_called_once()
            save_review.assert_called_once()
            commit_batch.assert_called_once_with(bank, commit_payload)
            cancel_batch.assert_called_once_with(cancel_payload, bank)


if __name__ == "__main__":
    unittest.main()
