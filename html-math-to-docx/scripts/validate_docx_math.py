#!/usr/bin/env python3
"""Count native Word OMML equations inside a DOCX file."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path


OMATH_RE = re.compile(rb"<m:oMath(?:\s|>)")
OMATH_PARA_RE = re.compile(rb"<m:oMathPara(?:\s|>)")


def count_omml(docx_file: Path) -> dict[str, int | str]:
    if not docx_file.exists():
        raise FileNotFoundError(docx_file)

    total_omath = 0
    total_omath_para = 0
    scanned_xml_files = 0

    with zipfile.ZipFile(docx_file) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            scanned_xml_files += 1
            data = archive.read(name)
            total_omath += len(OMATH_RE.findall(data))
            total_omath_para += len(OMATH_PARA_RE.findall(data))

    return {
        "file": str(docx_file),
        "word_xml_files_scanned": scanned_xml_files,
        "omath_count": total_omath,
        "omath_para_count": total_omath_para,
        "native_word_formula_count_estimate": total_omath,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx_file", type=Path)
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    report = count_omml(args.docx_file)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["omath_count"]:
            print("OK: native Word OMML formulas were found.")
        else:
            print("WARNING: no native Word OMML formulas were found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
