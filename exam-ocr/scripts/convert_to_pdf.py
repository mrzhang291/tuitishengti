#!/usr/bin/env python3
"""Convert Word documents (.docx/.doc) to PDF.

Uses docx2pdf (Windows, requires MS Word) as the primary method and falls
back to LibreOffice headless mode. The script is intentionally standalone so
Word files can be pre-converted before MinerU upload without requiring an API
token.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def convert_with_docx2pdf(input_path: Path, output_path: Path, auto_install: bool) -> bool:
    """Convert using docx2pdf (Windows + MS Word)."""
    try:
        from docx2pdf import convert
        print(f"[docx2pdf] Converting: {input_path}")
        convert(str(input_path), str(output_path))
        return output_path.exists()
    except ImportError:
        if not auto_install:
            print("[docx2pdf] Not installed")
            return False
        print("[docx2pdf] Not installed, trying: pip install docx2pdf")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "docx2pdf", "-q"])
        from docx2pdf import convert
        convert(str(input_path), str(output_path))
        return output_path.exists()
    except Exception as e:
        print(f"[docx2pdf] Failed: {e}")
        return False


def libreoffice_candidates() -> list[str]:
    candidates: list[str] = []

    env_path = os.environ.get("LIBREOFFICE_PATH")
    if env_path:
        candidates.append(env_path)

    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if not base:
                continue
            candidates.extend(
                [
                    str(Path(base) / "LibreOffice" / "program" / "soffice.exe"),
                    str(Path(base) / "LibreOffice" / "program" / "libreoffice.exe"),
                ]
            )

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in seen and Path(candidate).exists():
            seen.add(candidate)
            unique.append(candidate)
    return unique


def convert_with_libreoffice(input_path: Path, output_dir: Path, timeout: int) -> bool:
    """Convert using LibreOffice headless mode."""
    candidates = libreoffice_candidates()
    if not candidates:
        print("[libreoffice] LibreOffice/soffice not found on this system")
        return False

    last_error = ""
    for executable in candidates:
        cmd = [
            executable, "--headless", "--convert-to", "pdf",
            "--outdir", str(output_dir), str(input_path)
        ]
        print(f"[libreoffice] Running: {' '.join(cmd)}")
        try:
            completed = subprocess.run(cmd, check=False, timeout=timeout, capture_output=True, text=True)
        except Exception as exc:
            last_error = str(exc)
            print(f"[libreoffice] Failed with {executable}: {exc}")
            continue

        expected = output_dir / f"{input_path.stem}.pdf"
        if completed.returncode == 0 and expected.exists():
            return True
        last_error = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
        print(f"[libreoffice] Failed with {executable}: {last_error}")

    if last_error:
        print(f"[libreoffice] Last error: {last_error}")
    return False


def main():
    parser = argparse.ArgumentParser(description="Convert Word to PDF")
    parser.add_argument("input", type=str, help="Input .docx or .doc file")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output PDF path (default: same name as input)")
    parser.add_argument("--prefer", choices=("auto", "docx2pdf", "libreoffice"), default="auto",
                        help="Conversion backend preference")
    parser.add_argument("--timeout", type=int, default=180,
                        help="LibreOffice conversion timeout in seconds")
    parser.add_argument("--no-install", action="store_true",
                        help="Do not try to pip install docx2pdf when missing")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    if input_path.suffix.lower() not in (".docx", ".doc"):
        print(f"Error: Unsupported format: {input_path.suffix}")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = input_path.with_suffix(".pdf")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.suffix.lower() != ".pdf":
        output_path = output_path.with_suffix(".pdf")

    # Try docx2pdf first by default (Windows + Word), then LibreOffice.
    if args.prefer in ("auto", "docx2pdf") and convert_with_docx2pdf(
        input_path,
        output_path,
        auto_install=not args.no_install,
    ):
        print(f"[OK] PDF saved to: {output_path}")
        print(output_path)  # Print path for scripting
        return

    output_dir = output_path.parent
    if args.prefer in ("auto", "libreoffice") and convert_with_libreoffice(input_path, output_dir, args.timeout):
        # LibreOffice names the output as input_stem.pdf
        generated = output_dir / f"{input_path.stem}.pdf"
        if generated != output_path:
            generated.rename(output_path)
        print(f"[OK] PDF saved to: {output_path}")
        print(output_path)
        return

    print("Error: All conversion methods failed.")
    print("Install Microsoft Word or LibreOffice, or try: pip install docx2pdf")
    sys.exit(1)


if __name__ == "__main__":
    main()
