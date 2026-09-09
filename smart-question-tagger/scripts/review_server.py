#!/usr/bin/env python3
"""Serve the teacher workbench locally with autosave and one-click apply APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from apply_teacher_review import VALID_DECISIONS, apply_review_file, read_decisions


RECOMMENDER_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "smart-question-recommender-v3"
    / "scripts"
)
if str(RECOMMENDER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RECOMMENDER_SCRIPTS))

from live_personalized_generation import (  # noqa: E402
    GatewayConfigurationError,
    GenerationCancelledError,
    LiveGenerationError,
    cancel_personalized_batch,
    commit_personalized_batch,
    gateway_status,
    generate_personalized_draft,
    generate_personalized_question,
    save_personalized_review,
    verify_personalized_draft,
)


MAX_BODY = 5 * 1024 * 1024


def _bank_id(bank: Path) -> str:
    return hashlib.sha256(str(bank.resolve()).casefold().encode("utf-8")).hexdigest()[:16]


def _default_port() -> int:
    try:
        value = int(os.environ.get("TEACHER_WORKBENCH_PORT") or "8765")
    except ValueError as exc:
        raise SystemExit("TEACHER_WORKBENCH_PORT must be an integer") from exc
    if not 1 <= value <= 65535:
        raise SystemExit("TEACHER_WORKBENCH_PORT must be between 1 and 65535")
    return value


def _atomic_write_decisions(path: Path, decisions: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for question_id in sorted(decisions):
            handle.write(json.dumps(decisions[question_id], ensure_ascii=False) + "\n")
    temporary.replace(path)


def _draft_path(bank: Path) -> Path:
    return bank / "review" / "teacher_review_draft.jsonl"


def _load_draft(bank: Path) -> dict[str, dict[str, Any]]:
    path = _draft_path(bank)
    return read_decisions(path) if path.exists() and path.stat().st_size else {}


def _known_question_ids(bank: Path) -> set[str]:
    path = bank / "tags" / "all_question_tags.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row.get("question_id") for row in rows}


class WorkbenchHandler(SimpleHTTPRequestHandler):
    server_version = "TeacherWorkbench/1.0"

    def __init__(self, *args, bank: Path, **kwargs):
        self.bank = bank
        super().__init__(*args, directory=str(bank), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def end_headers(self) -> None:
        route = urlparse(self.path).path
        if not route.startswith("/api/") and (route.endswith(".html") or route.endswith("/review/")):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def _json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # The browser may have reached its own batch deadline.  The worker
            # still finishes cleanup, but a closed socket must not create a
            # second 500-response path or noisy traceback.
            return

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length < 1 or length > MAX_BODY:
            raise ValueError("请求内容为空或过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/health":
            self._json({"status": "ok", "bank": self.bank.name, "bank_id": _bank_id(self.bank)})
            return
        if route == "/api/decisions":
            self._json({"decisions": list(_load_draft(self.bank).values())})
            return
        if route == "/api/personalized-generation/status":
            self._json(gateway_status())
            return
        if route == "/api/backup":
            path = _draft_path(self.bank)
            body = path.read_bytes() if path.exists() else b""
            filename = f"review_backup_{datetime.now():%Y%m%d}.teacher-review"
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            if route == "/api/decision":
                self._save_one(self._read_json())
                return
            if route == "/api/decisions":
                payload = self._read_json()
                rows = payload.get("decisions") if isinstance(payload, dict) else payload
                if not isinstance(rows, list):
                    raise ValueError("审核记录格式不正确")
                for row in rows:
                    self._save_one(row, respond=False)
                self._json({"status": "saved", "count": len(rows)})
                return
            if route == "/api/apply":
                self._apply_reviews()
                return
            if route == "/api/personalized-generation/generate":
                self._json(generate_personalized_question(self.bank, self._read_json()))
                return
            if route == "/api/personalized-generation/draft":
                self._json(generate_personalized_draft(self.bank, self._read_json()))
                return
            if route == "/api/personalized-generation/verify":
                self._json(verify_personalized_draft(self.bank, self._read_json()))
                return
            if route == "/api/personalized-generation/cancel":
                self._json(cancel_personalized_batch(self._read_json(), self.bank))
                return
            if route == "/api/personalized-generation/commit":
                self._json(commit_personalized_batch(self.bank, self._read_json()))
                return
            if route == "/api/personalized-generation/review":
                self._json(save_personalized_review(self.bank, self._read_json()))
                return
            if route == "/api/shutdown":
                self._json({"status": "closing"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._json({"error": "未找到该操作"}, status=404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, status=400)
        except SystemExit as exc:
            self._json({"error": str(exc)}, status=409)
        except LiveGenerationError as exc:
            message = str(exc)
            selection_error = any(
                marker in message
                for marker in (
                    "没有所选题型的安全参考题",
                    "没有找到该知识点对应的安全生题槽位",
                    "没有找到该学生与知识点对应的安全生题槽位",
                    "安全诊断证据只有",
                    "诊断证据数必须",
                    "按学情生成时必须选择学生",
                    "题型参数不正确",
                    "生成方式不正确",
                    "缺少知识点",
                )
            )
            stage = (
                "draft"
                if route.endswith("/draft")
                else "verify"
                if route.endswith("/verify")
                else "generate"
            )
            error_code = (
                "gateway_not_configured"
                if isinstance(exc, GatewayConfigurationError)
                else "generation_cancelled"
                if isinstance(exc, GenerationCancelledError)
                else "gateway_request_failed"
                if exc.status_code == 502
                else "invalid_generation_selection"
                if selection_error
                else "question_rejected"
            )
            self._json(
                {
                    "error": message,
                    "status": "generation_failed",
                    "stage": stage,
                    "error_code": error_code,
                    "retryable": exc.status_code == 502 or (exc.status_code == 422 and not selection_error),
                },
                status=exc.status_code,
            )
        except Exception as exc:
            self._json({"error": f"操作失败：{exc}"}, status=500)

    def _save_one(self, row: dict[str, Any], respond: bool = True) -> None:
        if not isinstance(row, dict):
            raise ValueError("审核记录格式不正确")
        question_id = row.get("question_id")
        if question_id not in _known_question_ids(self.bank):
            raise ValueError("题目不存在或已更新，请刷新页面")
        decisions = _load_draft(self.bank)
        if row.get("decision") == "clear":
            decisions.pop(question_id, None)
        else:
            if row.get("decision") not in VALID_DECISIONS:
                raise ValueError("请选择通过、修改或暂缓")
            decisions[question_id] = row
        _atomic_write_decisions(_draft_path(self.bank), decisions)
        if respond:
            self._json({"status": "saved", "question_id": question_id, "count": len(decisions)})

    def _apply_reviews(self) -> None:
        path = _draft_path(self.bank)
        decisions = _load_draft(self.bank)
        actionable = {key: value for key, value in decisions.items() if value.get("decision") in {"approve", "change"}}
        if not actionable:
            raise ValueError("还没有可应用的审核结果")
        apply_path = path.with_name("teacher_review_apply_pending.jsonl")
        _atomic_write_decisions(apply_path, decisions)
        try:
            result = apply_review_file(self.bank, apply_path)
        finally:
            if apply_path.exists():
                apply_path.unlink()
        deferred = {key: value for key, value in decisions.items() if value.get("decision") == "defer"}
        _atomic_write_decisions(path, deferred)
        self._json(
            {
                "status": "applied",
                "applied": result.get("applied", 0),
                "deferred": len(deferred),
                "remaining_queue": (result.get("outputs") or {}).get("review_queue"),
            }
        )


def serve(bank: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> int:
    bank = bank.resolve()
    if not (bank / "review" / "index.html").exists():
        raise SystemExit("教师工作台尚未生成，请先运行打标流程。")

    def handler(*args, **kwargs):
        return WorkbenchHandler(*args, bank=bank, **kwargs)

    requested_url = f"http://{host}:{port}/review/index.html"
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError:
        try:
            with urlopen(f"http://{host}:{port}/api/health", timeout=1) as response:
                health = json.loads(response.read().decode("utf-8"))
            if health.get("bank_id") != _bank_id(bank):
                raise SystemExit("工作台端口已被另一个题库占用。")
            if open_browser:
                webbrowser.open(requested_url)
            return 0
        except SystemExit:
            raise
        except Exception as exc:
            raise SystemExit("教师工作台启动失败，请稍后重试。") from exc
    url = f"http://{host}:{server.server_port}/review/index.html"
    print(f"教师工作台：{url}")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Start the local teacher workbench.")
    parser.add_argument("structured_dir", type=Path)
    parser.add_argument("--host", default=os.environ.get("TEACHER_WORKBENCH_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=_default_port())
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("For safety, the teacher workbench only binds to this computer.")
    return serve(args.structured_dir, args.host, args.port, args.open_browser)


if __name__ == "__main__":
    raise SystemExit(main())
