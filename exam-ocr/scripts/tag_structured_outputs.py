#!/usr/bin/env python3
"""Backward-compatible proxy to smart-question-tagger's structured-bank entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path


TAGGER_SCRIPTS = Path(__file__).resolve().parents[2] / "smart-question-tagger" / "scripts"
if str(TAGGER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(TAGGER_SCRIPTS))

from tag_structured_bank import (  # noqa: E402,F401
    BENIGN_QUALITY_FLAGS,
    BLOCKING_TAG_FLAGS,
    blocking_tag_questions,
    enrich_tagged,
    load_tagger,
    main,
    parse_args,
    read_jsonl,
    write_summary,
)


if __name__ == "__main__":
    raise SystemExit(main())
