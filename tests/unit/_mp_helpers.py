# FILE: tests/unit/_mp_helpers.py
# START_MODULE_CONTRACT
#   PURPOSE: Pure helper functions for Phase-7 MP-* tests.
#     NOT a pytest fixture file (leading underscore signals "infra"; pytest will
#     not auto-collect this).
#   SCOPE: 6 helpers per docs/verification-plan.xml#SwarmFixtures/helpers-spec.
#     build_golden_document is stubbed until Wave-7-5 lands mint_python.sdk.
#   DEPENDS: pathlib, json, re, os, typing; lazy mint_python.sdk import in
#     build_golden_document (stub raises NotImplementedError pre-Wave-7-5).
#   LINKS: docs/verification-plan.xml#SwarmFixtures, docs/verification-plan.xml#VF-013
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   build_golden_document - VF-013 golden doc single source of truth (Wave-7-5)
#   extract_marker - regex parser [Module][fn][BLOCK_*] -> BLOCK_NAME
#   assert_marker_sequence - ordered marker assertion with strict|loose mode
#   assert_no_legacy_markers - VF-013 forbidden-5 guard
#   load_audit_baseline - reads tests/fixtures/mp_e2e_baseline.json
#   write_audit_baseline - gated by MP_E2E_WRITE_BASELINE=1
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Phase-7 pre-Wave-7-1: initial provisioning per SwarmFixtures/helpers-spec
# END_CHANGE_SUMMARY
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, cast

import pytest

_BASELINE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "mp_e2e_baseline.json"

_MARKER_RE = re.compile(r"^\[[^\]]+\]\[[^\]]+\]\[(BLOCK_[A-Z_]+)\]")

_LEGACY_MARKER_PREFIXES: tuple[str, ...] = (
    "[Sandbox]",
    "[Validate]",
    "[Edit]",
    "[OOXML]",
    "[Create]",
    "[Plan]",
    "[Section]",
)
_LEGACY_BLOCK_NAMES: tuple[str, ...] = (
    "BLOCK_RUN_CHECKS",
    "BLOCK_ORCHESTRATE",
    "BLOCK_EXECUTE_CODE",
    "BLOCK_OOXML_UNPACK",
)


def build_golden_document(out_path: Path) -> Path:
    """VF-013 golden document builder — single source of truth.

    Constructs the Phase-7 acceptance fixture per VF-013 golden-document-spec
    in docs/verification-plan.xml: alga_corporate preset, cover (Q2 Memo /
    Phase-7 acceptance fixture), TOC max_level=2, two sections (Summary,
    Details) with paragraphs + tables. Saves to out_path and returns the path.

    Reproducibility: this function is the SINGLE source of truth for the
    golden doc. Inlining the construction in test bodies is forbidden by
    VF-013 reproducibility invariant.
    """
    from mint_python.core.content import Paragraph
    from mint_python.core.document import Document
    from mint_python.core.section import Section
    from mint_python.core.table import Table

    doc = Document(format="docx", title="VF-013 Golden Document").with_style_preset(
        "alga_corporate"
    )
    doc.add_cover(title="Q2 Memo", subtitle="Phase-7 acceptance fixture")
    doc.add_toc(max_level=2)

    summary = Section("Summary", level=1)
    summary.add_paragraph(
        Paragraph(
            "This is the Phase-7 e2e fixture covering paragraph + table "
            "rendering through MP-CONTENT and MP-TABLE."
        )
    )
    summary.add_table(
        Table.from_list(
            [
                ["Quarter", "Revenue"],
                ["Q1", "$1.0M"],
                ["Q2", "$1.3M"],
            ],
            header=True,
        )
    )
    doc.add_section(summary)

    details = Section("Details", level=1)
    details.add_paragraph(Paragraph("Detail body line one."))
    details.add_paragraph(Paragraph("Detail body line two."))
    details.add_table(
        Table.from_list_of_dicts(
            [
                {"Region": "NA", "Share": "60%"},
                {"Region": "EU", "Share": "30%"},
                {"Region": "APAC", "Share": "10%"},
            ]
        )
    )
    doc.add_section(details)

    return doc.save(out_path)


def extract_marker(msg: str) -> str | None:
    """Parse `[Module][fn][BLOCK_NAME] payload` and return BLOCK_NAME, or None."""
    m = _MARKER_RE.match(msg)
    return m.group(1) if m else None


def assert_marker_sequence(
    caplog: pytest.LogCaptureFixture, expected: list[str], strict: bool = True
) -> None:
    """Assert BLOCK_NAME sequence appears in caplog records in order.

    strict=True (default): captured marker subsequence MUST equal expected exactly.
    strict=False: captured may interleave extras among the expected items, but
                  the relative ordering of expected items must hold.
    """
    captured = [m for m in (extract_marker(r.getMessage()) for r in caplog.records) if m]
    if strict:
        if captured != expected:
            raise AssertionError(
                f"marker sequence mismatch (strict)\n"
                f"  expected: {expected}\n"
                f"  captured: {captured}\n"
            )
        return

    # Loose mode: check that expected appears as a subsequence of captured.
    i = 0
    for marker in captured:
        if i < len(expected) and marker == expected[i]:
            i += 1
    if i != len(expected):
        first_missing = expected[i] if i < len(expected) else "<all matched>"
        raise AssertionError(
            f"marker sequence mismatch (loose)\n"
            f"  expected (in order): {expected}\n"
            f"  captured: {captured}\n"
            f"  diverged at expected[{i}]: {first_missing}\n"
        )


def assert_no_legacy_markers(caplog: pytest.LogCaptureFixture) -> None:
    """Assert no legacy js-engine markers appear in caplog records.

    See docs/verification-plan.xml#SwarmFixtures/helpers-spec/helper-4 for the
    exception policy: callers that explicitly invoke M-VALIDATE on a saved file
    MUST partition caplog (capture markers up-to-and-including BLOCK_SAVE_DOCX
    with this helper, then start a fresh caplog scope for M-VALIDATE).
    """
    bad: list[str] = []
    for record in caplog.records:
        msg = record.getMessage()
        for prefix in _LEGACY_MARKER_PREFIXES:
            if msg.startswith(prefix):
                bad.append(msg)
                break
        else:
            for block in _LEGACY_BLOCK_NAMES:
                if block in msg:
                    bad.append(msg)
                    break
    if bad:
        raise AssertionError(
            "legacy js-engine markers found on a Phase-7 path (VF-013 forbidden-5):\n"
            + "\n".join(f"  - {m}" for m in bad)
        )


def load_audit_baseline() -> dict[str, Any]:
    """Read tests/fixtures/mp_e2e_baseline.json; raise with rotation hint if missing."""
    if not _BASELINE_PATH.exists():
        raise FileNotFoundError(
            f"audit baseline not found at {_BASELINE_PATH}. "
            "Set MP_E2E_WRITE_BASELINE=1 on the first run to create it. "
            "See docs/verification-plan.xml#V-MP-DOCUMENT/baseline-update-protocol."
        )
    return cast(dict[str, Any], json.loads(_BASELINE_PATH.read_text()))


def write_audit_baseline(data: dict[str, Any]) -> None:
    """Write tests/fixtures/mp_e2e_baseline.json — only when MP_E2E_WRITE_BASELINE=1."""
    if os.environ.get("MP_E2E_WRITE_BASELINE") != "1":
        raise RuntimeError(
            "refusing to write audit baseline without MP_E2E_WRITE_BASELINE=1. "
            "See docs/verification-plan.xml#V-MP-DOCUMENT/baseline-update-protocol "
            "for the rotation rules."
        )
    _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BASELINE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
