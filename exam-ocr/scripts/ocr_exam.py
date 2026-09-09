#!/usr/bin/env python3
"""Convert exam PDFs/Word files to Markdown and HTML with MinerU.

The script intentionally uses only Python's standard library for the MinerU
API calls so the skill has no OCR/runtime dependency beyond a valid token.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable


DEFAULT_API_BASE = "https://mineru.net/api/v4"
DONE_STATES = {"done", "failed"}
WORD_SUFFIXES = {".doc", ".docx"}
PDF_SUFFIXES = {".pdf"}
MAX_ZIP_MEMBERS = 10000
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


class MinerUError(RuntimeError):
    """Raised when MinerU returns an unrecoverable error."""


def normalize_token(token: str | None) -> str:
    if not token:
        raise MinerUError("MINERU_TOKEN is required. Set it in the environment or pass --token.")
    token = token.strip()
    if token.startswith("mineru:"):
        token = token.split(":", 1)[1]
    if not token:
        raise MinerUError("MinerU token is empty after normalization.")
    return token


def normalize_api_base(value: str | None) -> str:
    raw = str(value or DEFAULT_API_BASE).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise MinerUError("MinerU API base must use HTTPS (HTTP is allowed only for local tests).")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MinerUError("MinerU API base is invalid or contains credentials/query data.")
    return raw


def safe_name(value: str) -> str:
    value = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", value, flags=re.UNICODE)
    return value.strip("._") or "document"


def data_id_for(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("._-")
    stem = stem[:80] or "pdf"
    return f"{stem}_{digest}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def json_request(method: str, url: str, token: str, payload: object | None = None, timeout: int = 60) -> dict:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise MinerUError(f"{method} {url} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise MinerUError(f"{method} {url} failed: {exc}") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise MinerUError(f"{method} {url} returned non-JSON response: {body[:300]!r}") from exc


def put_file(upload_url: str, path: Path, timeout: int = 300) -> int:
    """Upload with a bare PUT because MinerU OSS signatures are header-sensitive."""
    data = path.read_bytes()
    parsed = urllib.parse.urlsplit(upload_url)
    target = parsed.path
    if parsed.query:
        target = f"{target}?{parsed.query}"

    conn_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_class(parsed.hostname, parsed.port, timeout=timeout)
    try:
        conn.request("PUT", target, body=data, headers={"Content-Length": str(len(data))})
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()

    if response.status >= 400:
        detail = body.decode("utf-8", errors="replace")
        raise MinerUError(f"upload failed for {path.name}: HTTP {response.status}: {detail}")
    return response.status


def upload_files(pdfs: list[Path], upload_urls: list[str], workers: int) -> None:
    max_workers = max(1, min(workers, len(pdfs)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(put_file, upload_url, pdf): pdf
            for pdf, upload_url in zip(pdfs, upload_urls)
        }
        for future in as_completed(futures):
            pdf = futures[future]
            status = future.result()
            print(f"[upload] {pdf.name}: HTTP {status}", flush=True)


def download(url: str, target: Path, timeout: int = 300) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with target.open("wb") as file:
                shutil.copyfileobj(response, file)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise MinerUError(f"download failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise MinerUError(f"download failed: {exc}") from exc


def extract_zip(zip_path: Path, extract_dir: Path) -> None:
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise MinerUError(f"MinerU ZIP contains too many files: {len(members)}")
        total_size = sum(max(0, member.file_size) for member in members)
        if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise MinerUError(f"MinerU ZIP expands to an unsafe size: {total_size} bytes")
        root = extract_dir.resolve()
        for member in members:
            target = (extract_dir / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise MinerUError(f"MinerU ZIP contains an unsafe path: {member.filename}") from exc
            unix_mode = (member.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise MinerUError(f"MinerU ZIP contains a symbolic link: {member.filename}")
        archive.extractall(extract_dir)


def first_match(root: Path, patterns: Iterable[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(root.rglob(pattern))
        if matches:
            return matches[0]
    return None


def copy_images(images_dir: Path, output_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not images_dir.exists():
        return mapping

    target_dir = output_dir / "images"
    target_dir.mkdir(parents=True, exist_ok=True)

    for source in sorted(images_dir.iterdir()):
        if not source.is_file():
            continue
        target = target_dir / source.name
        if target.exists() and target.read_bytes() != source.read_bytes():
            target = target_dir / f"{source.stem}_{hashlib.sha256(source.read_bytes()).hexdigest()[:8]}{source.suffix}"
        shutil.copy2(source, target)
        mapping[f"images/{source.name}"] = f"images/{target.name}"
    return mapping


def rewrite_markdown_image_paths(markdown: str, image_mapping: dict[str, str]) -> str:
    if not image_mapping:
        return markdown
    for old, new in image_mapping.items():
        if old != new:
            markdown = markdown.replace(f"]({old})", f"]({new})")
            markdown = markdown.replace(f"]({old.replace('/', os.sep)})", f"]({new})")
    return markdown


def copy_result_files(extract_dir: Path, output_dir: Path, original_path: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = original_path.stem
    copied: dict[str, str] = {}

    md_path = first_match(extract_dir, ("full.md", "*.md"))
    html_path = first_match(extract_dir, ("full.html", "main.html", "*.html"))
    image_mapping = copy_images(extract_dir / "images", output_dir)

    if md_path:
        target = output_dir / f"{stem}.md"
        markdown = md_path.read_text(encoding="utf-8")
        markdown = rewrite_markdown_image_paths(markdown, image_mapping)
        target.write_text(markdown, encoding="utf-8")
        copied["md"] = str(target.resolve())

    if html_path:
        target = output_dir / f"{stem}.html"
        shutil.copy2(html_path, target)
        copied["html"] = str(target.resolve())

    if image_mapping:
        copied["images"] = str((output_dir / "images").resolve())

    return copied


def markdown_image_refs(markdown_path: Path) -> list[str]:
    text = markdown_path.read_text(encoding="utf-8")
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)


def verify_markdown_images(markdown_paths: Iterable[Path]) -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for md_path in sorted(markdown_paths):
        for ref in markdown_image_refs(md_path):
            if ref.startswith(("http://", "https://", "data:")):
                continue
            if not (md_path.parent / ref).exists():
                missing.append((str(md_path), ref))
    return missing


def existing_outputs_for_inputs(
    output_dir: Path,
    original_by_pdf: dict[Path, Path],
) -> dict[str, dict[str, str]] | None:
    outputs: dict[str, dict[str, str]] = {}
    for pdf, original in original_by_pdf.items():
        stem = original.stem
        markdown_path = output_dir / f"{stem}.md"
        html_path = output_dir / f"{stem}.html"
        if not markdown_path.exists() or not html_path.exists():
            return None
        newest_source_mtime = max(original.stat().st_mtime, pdf.stat().st_mtime)
        oldest_output_mtime = min(markdown_path.stat().st_mtime, html_path.stat().st_mtime)
        if oldest_output_mtime < newest_source_mtime:
            return None
        outputs[pdf.name] = {
            "md": str(markdown_path.resolve()),
            "html": str(html_path.resolve()),
        }
        images_dir = output_dir / "images"
        if images_dir.exists():
            outputs[pdf.name]["images"] = str(images_dir.resolve())
    return outputs


def cached_extract_for_pdf(cache_root: Path, original: Path, pdf: Path) -> Path | None:
    if not cache_root.exists():
        return None
    expected_dir_name = safe_name(original.stem)
    expected_data_id = data_id_for(pdf)
    candidates: list[Path] = []
    for payload_path in cache_root.rglob("submit_payload.json"):
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        files = payload.get("files", [])
        if not any(item.get("data_id") == expected_data_id for item in files):
            continue
        extract_dir = payload_path.parent / "extracted" / expected_dir_name
        if not (extract_dir / "full.md").exists() or not (extract_dir / "full.html").exists():
            continue
        candidates.append(extract_dir)
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path / "full.html").stat().st_mtime)


def restore_outputs_from_cache(
    output_dir: Path,
    cache_root: Path,
    original_by_pdf: dict[Path, Path],
) -> dict[str, dict[str, str]] | None:
    restored: dict[str, dict[str, str]] = {}
    for pdf, original in original_by_pdf.items():
        extract_dir = cached_extract_for_pdf(cache_root, original, pdf)
        if extract_dir is None:
            return None
        restored[pdf.name] = copy_result_files(extract_dir, output_dir, original)
    return restored


def convert_word_to_pdf(input_path: Path, converted_dir: Path) -> Path:
    converted_dir.mkdir(parents=True, exist_ok=True)
    output_path = converted_dir / f"{input_path.stem}.pdf"
    script_path = Path(__file__).resolve().parent / "convert_to_pdf.py"
    command = [sys.executable, str(script_path), str(input_path), "-o", str(output_path)]
    print(f"[convert] {input_path.name} -> {output_path}", flush=True)
    subprocess.run(command, check=True)
    if not output_path.exists():
        raise MinerUError(f"Word conversion did not create: {output_path}")
    return output_path


def prepare_inputs(inputs: list[Path], cache_root: Path) -> tuple[list[Path], dict[Path, Path]]:
    pdfs: list[Path] = []
    original_by_pdf: dict[Path, Path] = {}
    converted_dir = cache_root / "_converted"

    for raw in inputs:
        path = raw.resolve()
        if not path.exists():
            raise MinerUError(f"input not found: {path}")
        suffix = path.suffix.lower()
        if suffix in PDF_SUFFIXES:
            pdf = path
        elif suffix in WORD_SUFFIXES:
            pdf = convert_word_to_pdf(path, converted_dir)
        else:
            raise MinerUError(f"unsupported input format: {path.suffix} ({path})")
        pdfs.append(pdf)
        original_by_pdf[pdf] = path

    return pdfs, original_by_pdf


def submit_batch(
    pdfs: list[Path],
    token: str,
    model_version: str,
    language: str,
    is_ocr: bool,
    api_base: str = DEFAULT_API_BASE,
) -> tuple[str, dict, list[dict]]:
    files = [
        {
            "name": path.name,
            "is_ocr": is_ocr,
            "data_id": data_id_for(path),
        }
        for path in pdfs
    ]
    payload = {
        "files": files,
        "model_version": model_version,
        "language": language,
        "enable_formula": True,
        "enable_table": True,
        "extra_formats": ["html"],
    }
    response = json_request("POST", f"{api_base}/file-urls/batch", token, payload, timeout=60)
    if response.get("code") != 0:
        raise MinerUError(f"submit failed: {response}")
    return response["data"]["batch_id"], response, files


def poll_until_done(
    batch_id: str,
    token: str,
    batch_dir: Path,
    interval: int,
    timeout: int,
    api_base: str = DEFAULT_API_BASE,
) -> dict:
    started = time.monotonic()
    poll_dir = batch_dir / "polls"
    poll_dir.mkdir(parents=True, exist_ok=True)
    result_url = f"{api_base}/extract-results/batch/{batch_id}"
    attempt = 0

    while True:
        attempt += 1
        result = json_request("GET", result_url, token, timeout=60)
        write_json(poll_dir / f"{attempt:03d}.json", result)
        if result.get("code") != 0:
            raise MinerUError(f"query failed: {result}")

        items = result.get("data", {}).get("extract_result", [])
        states = [item.get("state", "") for item in items]
        print(f"[poll {attempt}] {states}", flush=True)

        if items and all(state in DONE_STATES for state in states):
            return result
        if time.monotonic() - started > timeout:
            raise MinerUError(f"timed out after {timeout}s waiting for batch {batch_id}")
        time.sleep(interval)


def process_results(
    result: dict,
    files: list[dict],
    pdfs: list[Path],
    original_by_pdf: dict[Path, Path],
    batch_dir: Path,
    output_dir: Path,
) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    id_to_pdf = {file_info["data_id"]: pdf for file_info, pdf in zip(files, pdfs)}
    name_to_pdf = {pdf.name: pdf for pdf in pdfs}
    outputs: dict[str, dict[str, str]] = {}
    failures: list[dict[str, str]] = []

    for item in result.get("data", {}).get("extract_result", []):
        file_name = item.get("file_name", "unknown")
        state = item.get("state")
        pdf = id_to_pdf.get(item.get("data_id")) or name_to_pdf.get(file_name)
        original = original_by_pdf.get(pdf, pdf or Path(file_name))

        if state != "done":
            failures.append({"file_name": file_name, "state": str(state), "err_msg": str(item.get("err_msg", ""))})
            continue
        if not item.get("full_zip_url"):
            failures.append({"file_name": file_name, "state": str(state), "err_msg": "missing full_zip_url"})
            continue

        result_stem = safe_name(original.stem)
        zip_path = batch_dir / "zips" / f"{result_stem}.zip"
        extract_dir = batch_dir / "extracted" / result_stem
        print(f"[download] {file_name}", flush=True)
        download(item["full_zip_url"], zip_path)
        extract_zip(zip_path, extract_dir)
        outputs[file_name] = copy_result_files(extract_dir, output_dir, original)
        print(f"[cached] {zip_path}", flush=True)

    return outputs, failures


def default_paths(output_arg: str | None, cache_arg: str | None) -> tuple[Path, Path]:
    if output_arg:
        output_dir = Path(output_arg)
        default_cache = output_dir / "mineru_cache"
    else:
        output_dir = Path("mineru_outputs")
        default_cache = Path("mineru_cache")
    cache_dir = Path(cache_arg) if cache_arg else default_cache
    return output_dir.resolve(), cache_dir.resolve()


def polish_outputs(output_dir: Path, refresh_backup: bool = True) -> None:
    script_path = Path(__file__).resolve().parent / "polish_outputs.py"
    command = [
        sys.executable,
        str(script_path),
        str(output_dir),
        "--backup-dir",
        str(output_dir / "_raw_mineru"),
    ]
    if refresh_backup:
        command.append("--refresh-backup")
    print(f"[polish] {output_dir}", flush=True)
    subprocess.run(command, check=True)


def audit_outputs(output_dir: Path, cache_root: Path, report_path: Path) -> Path:
    script_path = Path(__file__).resolve().parent / "audit_outputs.py"
    command = [
        sys.executable,
        str(script_path),
        str(output_dir),
        "--raw-dir",
        str(output_dir / "_raw_mineru"),
        "--cache-dir",
        str(cache_root),
        "--json-out",
        str(report_path),
    ]
    print(f"[audit] {output_dir}", flush=True)
    subprocess.run(command, check=True)
    return report_path


def generate_quality_report(output_dir: Path, audit_report_path: Path | None) -> dict[str, str]:
    script_path = Path(__file__).resolve().parent / "quality_report.py"
    report_dir = output_dir / "_reports"
    json_out = report_dir / "quality_report.json"
    html_out = report_dir / "quality_report.html"
    command = [
        sys.executable,
        str(script_path),
        str(output_dir),
        "--json-out",
        str(json_out),
        "--html-out",
        str(html_out),
    ]
    if audit_report_path:
        command.extend(["--audit-report", str(audit_report_path)])
    print(f"[quality] {output_dir}", flush=True)
    subprocess.run(command, check=True)
    return {"json": str(json_out.resolve()), "html": str(html_out.resolve())}


def reuse_existing_outputs(
    output_dir: Path,
    cache_root: Path,
    outputs: dict[str, dict[str, str]],
    no_polish: bool,
    no_audit: bool,
    no_report: bool,
) -> int:
    audit_report_path = None
    if not no_polish:
        polish_outputs(output_dir, refresh_backup=False)
        raw_dir = output_dir / "_raw_mineru"
        if not no_audit and raw_dir.exists():
            audit_report_path = audit_outputs(output_dir, cache_root, output_dir / "_reports" / "audit_report.json")
    quality_report = None
    if not no_report:
        quality_report = generate_quality_report(output_dir, audit_report_path)

    markdown_paths = [
        Path(file_outputs["md"])
        for file_outputs in outputs.values()
        if file_outputs.get("md")
    ]
    missing_images = verify_markdown_images(markdown_paths)
    summary = {
        "reused_existing": True,
        "api_called": False,
        "output_dir": str(output_dir),
        "cache_dir": str(cache_root),
        "outputs": outputs,
        "missing_image_refs": missing_images,
        "polished": not no_polish,
        "audit_report": str(audit_report_path) if audit_report_path else None,
        "quality_report": quality_report,
    }
    report_dir = output_dir / "_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "local_reuse_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 1 if missing_images else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse exam PDFs/Word files with MinerU and export Markdown + HTML.",
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="Input .pdf, .docx, or .doc files")
    parser.add_argument("-o", "--output-dir", help="Directory for final .md/.html outputs")
    parser.add_argument("--cache-dir", help="Directory for MinerU ZIPs, extracted packages, and poll JSON")
    parser.add_argument("--token", help="MinerU token. Defaults to MINERU_TOKEN environment variable")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("MINERU_API_BASE") or DEFAULT_API_BASE,
        help="MinerU API base. Defaults to MINERU_API_BASE or the official endpoint.",
    )
    parser.add_argument("--model-version", default="vlm", choices=("vlm", "pipeline"))
    parser.add_argument("--language", default="ch", help="MinerU language code, e.g. ch, en")
    parser.add_argument("--interval", type=int, default=3, help="Polling interval in seconds")
    parser.add_argument("--timeout", type=int, default=1800, help="Polling timeout in seconds")
    parser.add_argument("--upload-workers", type=int, default=4, help="Parallel upload workers")
    parser.add_argument("--no-ocr", action="store_true", help="Set is_ocr=false for MinerU input files")
    parser.add_argument("--no-polish", action="store_true", help="Keep raw MinerU Markdown/HTML without local layout polishing")
    parser.add_argument("--no-audit", action="store_true", help="Skip post-polish audit against raw MinerU/cache outputs")
    parser.add_argument("--no-report", action="store_true", help="Skip local _reports/quality_report.json/html generation")
    parser.add_argument("--force", action="store_true", help="Call MinerU even if reusable local Markdown/HTML outputs already exist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output_dir, cache_root = default_paths(args.output_dir, args.cache_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_root.mkdir(parents=True, exist_ok=True)

        pdfs, original_by_pdf = prepare_inputs(args.inputs, cache_root)
        if not args.force:
            existing_outputs = existing_outputs_for_inputs(output_dir, original_by_pdf)
            if existing_outputs:
                print("[reuse] local Markdown/HTML outputs are newer than inputs; skipping MinerU. Use --force to rescan.", flush=True)
                return reuse_existing_outputs(
                    output_dir=output_dir,
                    cache_root=cache_root,
                    outputs=existing_outputs,
                    no_polish=args.no_polish,
                    no_audit=args.no_audit,
                    no_report=args.no_report,
                )
            restored_outputs = restore_outputs_from_cache(output_dir, cache_root, original_by_pdf)
            if restored_outputs:
                print("[cache] restored MinerU extracted results from local cache; skipping upload. Use --force to rescan.", flush=True)
                return reuse_existing_outputs(
                    output_dir=output_dir,
                    cache_root=cache_root,
                    outputs=restored_outputs,
                    no_polish=args.no_polish,
                    no_audit=args.no_audit,
                    no_report=args.no_report,
                )

        token = normalize_token(args.token or os.environ.get("MINERU_TOKEN"))
        api_base = normalize_api_base(args.api_base)
        print(f"[submit] {len(pdfs)} file(s) -> MinerU", flush=True)
        batch_id, submit_response, files = submit_batch(
            pdfs=pdfs,
            token=token,
            model_version=args.model_version,
            language=args.language,
            is_ocr=not args.no_ocr,
            api_base=api_base,
        )

        batch_dir = cache_root / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        write_json(batch_dir / "submit_response.json", submit_response)
        write_json(
            batch_dir / "submit_payload.json",
            {
                "files": files,
                "model_version": args.model_version,
                "language": args.language,
                "enable_formula": True,
                "enable_table": True,
                "extra_formats": ["html"],
            },
        )
        print(f"[batch] {batch_id}", flush=True)

        upload_urls = submit_response["data"]["file_urls"]
        if len(upload_urls) != len(pdfs):
            raise MinerUError(f"MinerU returned {len(upload_urls)} upload URLs for {len(pdfs)} files.")

        upload_files(pdfs, upload_urls, args.upload_workers)

        result = poll_until_done(batch_id, token, batch_dir, args.interval, args.timeout, api_base)
        write_json(batch_dir / "final_result.json", result)

        outputs, failures = process_results(result, files, pdfs, original_by_pdf, batch_dir, output_dir)
        audit_report_path = None
        if not args.no_polish:
            polish_outputs(output_dir)
            if not args.no_audit:
                audit_report_path = audit_outputs(output_dir, cache_root, batch_dir / "audit_report.json")
        quality_report = None
        if not args.no_report:
            quality_report = generate_quality_report(output_dir, audit_report_path)
        markdown_paths = [
            Path(file_outputs["md"])
            for file_outputs in outputs.values()
            if file_outputs.get("md")
        ]
        missing_images = verify_markdown_images(markdown_paths)
        summary = {
            "batch_id": batch_id,
            "output_dir": str(output_dir),
            "cache_dir": str(batch_dir),
            "outputs": outputs,
            "failed": failures,
            "missing_image_refs": missing_images,
            "polished": not args.no_polish,
            "audit_report": str(audit_report_path) if audit_report_path else None,
            "quality_report": quality_report,
        }
        write_json(batch_dir / "local_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

        if failures or missing_images:
            return 1
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Error: command failed with exit code {exc.returncode}: {exc.cmd}", file=sys.stderr)
        return exc.returncode or 1
    except MinerUError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
