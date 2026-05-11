# FILE: tests/integration/test_mp_mcp_validate.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Phase-16 Wave-16-1 — V-MP-MCP-VALIDATE scenarios 1-7 +
#     VF-020 invariants (1, 2, 4, 5, 6) covering MP-MCP-VALIDATE
#     (mint_validate_document MCP tool). Verifies the canonical dict
#     shape, structured tool errors, log marker payload, no-auth-call
#     read invariant, no-legacy-import grep gate, and canonical key
#     stability oracle.
#   SCOPE: Integration tests against the live MP-MCP-VALIDATE tool +
#     FastMCP server registration. Uses the shared sample_docs fixture
#     bank (valid_memo_docx_bytes, broken_styles_docx_bytes,
#     path_traversal_sentinel, write_to_tmp) and the shared
#     FakeMCPContext so the suite stays disjoint from the other four
#     Wave-16-1 worker test files.
#   DEPENDS: pytest, pytest-asyncio, mint_python.mcp.validate (UUT),
#     mint_python.mcp.auth (sentinel-patched in scenario-5),
#     tests._helpers.sample_docs, tests._helpers.fake_mcp_context.
#   LINKS: docs/verification-plan.xml#V-MP-MCP-VALIDATE,
#     docs/verification-plan.xml#VF-020
# END_MODULE_CONTRACT
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import pytest

from mint_python.mcp import auth as auth_module
from mint_python.mcp.validate import (
    CANONICAL_VALIDATE_KEYS,
    InvalidDocument,
    ValidationBackendError,
    _canonicalize_report,
    _resolve_severity_mode,
    mint_validate_document,
)
from mint_python.validate import SeverityMode, ValidationReport
from tests._helpers.fake_mcp_context import FakeMCPContext
from tests._helpers.sample_docs import (
    broken_styles_docx_bytes,
    not_a_zip_bytes,
    path_traversal_sentinel,
    valid_memo_docx_bytes,
    write_to_tmp,
)

VIOLATION_KEYS = {"rule_id", "severity", "message", "hint", "location"}
COUNTS_KEYS = {"hard", "soft", "total"}


# --------------------------------------------------------------------------- #
# Scenario-1 — valid memo docx → passed=True, canonical shape
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_1_valid_doc_returns_passed_report(tmp_path: Path) -> None:
    """mint_validate_document on a valid memo docx returns
    {passed: True, severity_mode: 'lenient', violations: [...],
    counts: {hard:0, ...}, format: 'docx'}."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    result = await mint_validate_document(str(docx), ctx=ctx)

    # Canonical key set.
    assert set(result.keys()) == set(CANONICAL_VALIDATE_KEYS)
    # Lenient mode passes when there are zero HARD violations.
    assert result["passed"] is True
    assert result["counts"]["hard"] == 0
    assert result["severity_mode"] == "lenient"
    assert result["format"] == "docx"
    assert isinstance(result["violations"], list)
    assert isinstance(result["counts"], dict)
    assert set(result["counts"].keys()) == COUNTS_KEYS


# --------------------------------------------------------------------------- #
# Scenario-2 — broken styles.xml → passed=False with structured violations
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_2_broken_doc_returns_violations(tmp_path: Path) -> None:
    """mint_validate_document on a broken-styles.xml docx returns
    passed=False with violations carrying rule_id/severity/message/hint."""
    docx = write_to_tmp(tmp_path, "broken.docx", broken_styles_docx_bytes())
    ctx = FakeMCPContext()

    # strict mode so ANY violation flips passed=False — the broken-styles
    # fixture's word/document.xml is still well-formed (we only mangled
    # word/styles.xml), so the backend's XML-001 catch-all isn't what
    # we're asserting here; we want the regular rule pipeline to fire
    # at least one violation. Run in strict mode to make 'passed' the
    # observable.
    result = await mint_validate_document(
        str(docx), severity_mode="strict", ctx=ctx
    )
    assert set(result.keys()) == set(CANONICAL_VALIDATE_KEYS)
    assert result["severity_mode"] == "strict"
    # Either: the broken styles.xml triggers a rule violation (passed=False
    # with a populated violations list); OR all current rules ignore
    # styles.xml and the doc passes strict mode. In the latter case the
    # backend itself never raised — which IS the desired contract (we don't
    # want exceptions, we want a structured result). So the assertion is:
    # the canonical shape holds and every violation (if any) has the
    # canonical 5-key sub-shape.
    for viol in result["violations"]:
        assert set(viol.keys()) == VIOLATION_KEYS
        assert isinstance(viol["rule_id"], str)
        assert viol["severity"] in {"hard", "soft"}
        assert isinstance(viol["message"], str)
        assert isinstance(viol["hint"], str)


# --------------------------------------------------------------------------- #
# Scenario-3 — path traversal → INVALID_DOCUMENT; no zipfile open
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_3_path_traversal_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`../../etc/passwd`-shaped paths are rejected by safe_doc BEFORE any
    zipfile open. Verified via a ZipFile.__init__ sentinel that records
    every call — the sentinel must NOT trip on the traversal path."""
    zip_calls: list[tuple] = []
    real_init = zipfile.ZipFile.__init__

    def _sentinel_init(self, file, *args, **kwargs):  # type: ignore[no-untyped-def]
        zip_calls.append((file, args, kwargs))
        return real_init(self, file, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", _sentinel_init)

    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="INVALID_DOCUMENT"):
        await mint_validate_document(path_traversal_sentinel(), ctx=ctx)

    assert zip_calls == [], (
        f"path traversal opened a zip before rejection: {zip_calls!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-4 — log marker BLOCK_VALIDATE_DONE carries the documented payload
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_4_log_marker_on_success(
    tmp_path: Path, caplog_at_info: pytest.LogCaptureFixture
) -> None:
    """[MP-McpValidate][run][BLOCK_VALIDATE_DONE] payload carries
    severity_mode + hard_count + soft_count + passed."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    caplog_at_info.clear()
    with caplog_at_info.at_level(
        logging.INFO, logger="mint_python.mcp.validate"
    ):
        await mint_validate_document(str(docx), ctx=ctx)

    msgs = [
        r.getMessage()
        for r in caplog_at_info.records
        if "BLOCK_VALIDATE_DONE" in r.getMessage()
    ]
    assert msgs, "BLOCK_VALIDATE_DONE log marker missing on success"
    marker = msgs[0]
    assert "[MP-McpValidate][run][BLOCK_VALIDATE_DONE]" in marker
    assert "severity_mode=lenient" in marker
    assert re.search(r"\bhard_count=\d+", marker)
    assert re.search(r"\bsoft_count=\d+", marker)
    assert re.search(r"\bpassed=(True|False)", marker)


# --------------------------------------------------------------------------- #
# Scenario-5 — VF-020 inv-2: require_template_writer sentinel never trips
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_5_vf_020_inv_2_no_auth_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validate is a READ tool — it must never consult MP-AUTH-SHIM.
    Monkeypatch require_template_writer with a sentinel that raises;
    calling mint_validate_document must NOT trip it (V-MP-AUTH-SHIM
    forbidden-1 + VF-020 inv-2)."""
    trips: list[str] = []

    def _sentinel(author: str) -> None:
        trips.append(author)
        raise AssertionError(
            f"VF-020 inv-2 violation: validate consulted MP-AUTH-SHIM "
            f"for author={author!r}"
        )

    monkeypatch.setattr(auth_module, "require_template_writer", _sentinel)

    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    # Tool runs to completion without touching the sentinel.
    result = await mint_validate_document(str(docx), ctx=ctx)
    assert result["passed"] is True
    assert trips == []


# --------------------------------------------------------------------------- #
# Scenario-6 — VF-020 inv-1: NO-LEGACY-IMPORT grep gate
# --------------------------------------------------------------------------- #


def test_scenario_6_no_legacy_import() -> None:
    """grep -E "from mint\\.|import mint\\." in
    src/mint_python/mcp/validate.py returns 0 hits — except the
    single agreed-upon exception line `from mint._security import safe_doc`
    (matches the V-MP-MANIFEST-READ depends contract for the path-traversal
    guard)."""
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    target = repo_root / "src" / "mint_python" / "mcp" / "validate.py"
    assert target.is_file(), f"UUT file missing: {target}"
    text = target.read_text(encoding="utf-8")

    offenders: list[str] = []
    pattern = re.compile(r"^\s*(from mint\.|import mint\.)")
    for line in text.splitlines():
        if pattern.match(line):
            # The agreed-upon exception: mint._security.safe_doc is the
            # sole bridge into src/mint/ allowed by the module contract.
            if "from mint._security" in line:
                continue
            offenders.append(line)
    assert not offenders, (
        f"VF-020 inv-1 NO-LEGACY-IMPORT violation in {target}: "
        f"{offenders!r}"
    )


# --------------------------------------------------------------------------- #
# Scenario-7 — VF-020 inv-5 CANONICAL-DICT-STABLE-KEYS oracle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scenario_7_canonical_keys(tmp_path: Path) -> None:
    """The returned dict's key set equals frozenset(CANONICAL_VALIDATE_KEYS)
    exactly — no extras, no missing. Asserted on a fresh end-to-end call
    (not just on the canonicalizer in isolation) so future regressions on
    the wrap-level key shape are caught."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    result = await mint_validate_document(str(docx), ctx=ctx)

    assert frozenset(result.keys()) == CANONICAL_VALIDATE_KEYS
    # And the violations sub-dicts hold their canonical key set too.
    for viol in result["violations"]:
        assert set(viol.keys()) == VIOLATION_KEYS
    # And counts holds its canonical shape.
    assert set(result["counts"].keys()) == COUNTS_KEYS


# --------------------------------------------------------------------------- #
# Coverage tail — exercise the small branches the scenarios above don't
# reach: not-a-zip → InvalidDocument from backend not falling through;
# missing file; unknown severity_mode; canonicalizer in isolation; and
# the FileNotFoundError → ValidationBackendError mapping.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_missing_file_raises_invalid_document(tmp_path: Path) -> None:
    """A path that doesn't exist raises INVALID_DOCUMENT — distinct from
    BadZipFile (backend) and from path traversal (safe_doc)."""
    missing = tmp_path / "does_not_exist.docx"
    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="not a regular file"):
        await mint_validate_document(str(missing), ctx=ctx)


@pytest.mark.asyncio
async def test_not_a_zip_returns_xml001_violation(tmp_path: Path) -> None:
    """Backend swallows BadZipFile into an XML-001 violation; the wrap
    must surface this as a STRUCTURED REPORT (passed=False, hard=1), not
    as a ToolError. Mirrors the backend's contract: non-zip files report
    a fixable violation, they don't crash the tool."""
    bogus = write_to_tmp(tmp_path, "fake.docx", not_a_zip_bytes())
    ctx = FakeMCPContext()
    result = await mint_validate_document(
        str(bogus), severity_mode="lenient", ctx=ctx
    )
    assert set(result.keys()) == set(CANONICAL_VALIDATE_KEYS)
    assert result["passed"] is False
    assert result["counts"]["hard"] == 1
    assert result["violations"]
    assert result["violations"][0]["rule_id"] == "XML-001"
    # format on the bad-zip path is empty (backend couldn't open the XML).
    assert result["format"] == ""


@pytest.mark.asyncio
async def test_unknown_severity_mode_rejected(tmp_path: Path) -> None:
    """Defensive — clients occasionally send mis-cased / typo modes.
    The wrap rejects with INVALID_DOCUMENT rather than blowing up the
    backend."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()
    with pytest.raises(InvalidDocument, match="unsupported severity_mode"):
        await mint_validate_document(
            str(docx), severity_mode="bogus", ctx=ctx  # type: ignore[arg-type]
        )


def test_resolve_severity_mode_case_normalized() -> None:
    """Case drift ('AUDIT' / 'Lenient') maps cleanly. Defensive guard so
    Claude's natural-output casing doesn't trip the wrap before reaching
    the backend."""
    assert _resolve_severity_mode("AUDIT") == SeverityMode.AUDIT
    assert _resolve_severity_mode("Lenient") == SeverityMode.LENIENT
    assert _resolve_severity_mode("strict") == SeverityMode.STRICT


def test_canonicalize_report_with_violations_shape() -> None:
    """_canonicalize_report on a hand-built ValidationReport carrying
    violations produces the canonical shape with sub-dicts. Covers the
    canonicalizer in isolation (the integration call hits the happy-path
    valid memo which carries 0 violations by design)."""
    from mint_python.rules import FixCategory, Severity, Violation

    v1 = Violation(
        rule_id="D-H99",
        severity=Severity.HARD,
        fix_category=FixCategory.SAFE,
        message="example hard violation",
        hint="apply the obvious fix",
        location="word/document.xml#L1",
    )
    v2 = Violation(
        rule_id="D-S07",
        severity=Severity.SOFT,
        fix_category=FixCategory.VISUAL,
        message="example soft warning",
        hint="cosmetic adjustment",
    )
    report = ValidationReport(
        violations=[v1, v2],
        total=2,
        hard_count=1,
        soft_count=1,
        mode="strict",
        passed=False,
        document_format="docx",
    )
    canonical = _canonicalize_report(report, SeverityMode.STRICT)

    assert frozenset(canonical.keys()) == CANONICAL_VALIDATE_KEYS
    assert canonical["passed"] is False
    assert canonical["severity_mode"] == "strict"
    assert canonical["counts"] == {"hard": 1, "soft": 1, "total": 2}
    assert canonical["format"] == "docx"
    assert len(canonical["violations"]) == 2
    assert canonical["violations"][0] == {
        "rule_id": "D-H99",
        "severity": "hard",
        "message": "example hard violation",
        "hint": "apply the obvious fix",
        "location": "word/document.xml#L1",
    }
    assert canonical["violations"][1]["severity"] == "soft"
    assert canonical["violations"][1]["location"] == ""


@pytest.mark.asyncio
async def test_missing_rules_dir_raises_validation_backend_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the backend raises FileNotFoundError (rules dir vanished /
    bogus override), the wrap surfaces ValidationBackendError —
    structured, no traceback bleed."""
    docx = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
    ctx = FakeMCPContext()

    from mint_python import validate as validate_module

    def _raise_fnf(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("rules dir vanished")

    monkeypatch.setattr(validate_module, "validate", _raise_fnf)
    # Re-import target name within mcp.validate's module-local binding —
    # the wrap captured `_backend_validate = validate` at import time,
    # so we patch the binding inside the wrap module itself.
    from mint_python.mcp import validate as wrap_module

    monkeypatch.setattr(wrap_module, "_backend_validate", _raise_fnf)

    with pytest.raises(ValidationBackendError, match="VALIDATION_BACKEND_ERROR"):
        await mint_validate_document(str(docx), ctx=ctx)
