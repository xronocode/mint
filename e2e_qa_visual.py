"""CLI shim — visual QA scorer for MINT-generated DOCX.

The implementation lives in `mint_python.qa.visual.score_document`. This
file is preserved as a CLI entry point so existing operator runbooks
(`python e2e_qa_visual.py path/to/doc.docx [--pages 5] [--threshold 70]`)
keep working unchanged. Phase-15 Wave-15-2 retired the original one-shot
script in favor of the library module so the same code path can run as
the post-create_document hook (MP-VISUAL-QA-HOOK).

Usage:
    python e2e_qa_visual.py path/to/doc.docx [--pages 5] [--threshold 70]

Exit codes:
    0 - score >= threshold
    1 - score <  threshold
    2 - backend unavailable / scorer skipped (advisory)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from mint_python.qa.visual import score_document


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("docx", type=Path)
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--threshold", type=int, default=70,
                    help="exit 0 if score >= threshold")
    args = ap.parse_args()

    report = score_document(
        args.docx, preset_name="klawd",
        threshold=args.threshold, max_pages=args.pages,
    )
    if report is None:
        # MINT_SKIP_VISUAL_QA=1 path. Surface the env-skip explicitly.
        print(json.dumps({"skipped": True, "skip_reason": "env_skip"}, indent=2))
        return 2
    payload = asdict(report)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if report.skipped:
        return 2
    return 0 if report.score >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
