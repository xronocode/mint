# FILE: tests/unit/test_mp_phase9_coverage_gaps.py
"""Targeted tests for branches not exercised by V-MP-VALIDATE/V-MP-FIX/V-MP-DOCUMENT
scenario suites.

Phase-9 closeout shipped at 99% coverage despite claimed 100% — this file
restores the Gate-Phase-9 cov-fail-under=100 contract by covering the
documented error paths in `_resolve_severity_mode`, `_apply_simple_fix`, and
`apply_fixes`. Mirrors the `test_mp_coverage_gaps.py` pattern from Phase-7.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mint_python.core.document import _resolve_severity_mode
from mint_python.fix import (
    BackupFailedError,
    apply_fixes,
)
from mint_python.fix import _apply_simple_fix as apply_simple_fix
from mint_python.rules import FixCategory, Severity, Violation
from mint_python.validate import SeverityMode

# --------------------------------------------------------------------------- #
# document.py:140-142 — _resolve_severity_mode
# --------------------------------------------------------------------------- #


def test_resolve_severity_mode_strict_returns_strict():
    """document.py:140-141 — 'strict' string maps to SeverityMode.STRICT."""
    assert _resolve_severity_mode("strict") is SeverityMode.STRICT


def test_resolve_severity_mode_audit_returns_audit():
    """document.py:142 — 'audit' falls through to SeverityMode.AUDIT."""
    assert _resolve_severity_mode("audit") is SeverityMode.AUDIT


def test_resolve_severity_mode_unknown_falls_through_to_audit():
    """document.py:142 — unknown level string also falls through to AUDIT (defensive default)."""
    assert _resolve_severity_mode("zzz-unknown") is SeverityMode.AUDIT


# --------------------------------------------------------------------------- #
# fix.py:108 — _apply_simple_fix DESTRUCTIVE early return
# --------------------------------------------------------------------------- #


def test_apply_simple_fix_destructive_returns_false(tmp_path: Path):
    """fix.py:108 — DESTRUCTIVE violations short-circuit before any zip work."""
    # Doc path doesn't matter — we never reach the zipfile open.
    doc_path = tmp_path / "x.docx"
    doc_path.write_bytes(b"")
    v = Violation(
        rule_id="D-X99",
        severity=Severity.HARD,
        fix_category=FixCategory.DESTRUCTIVE,
        message="m",
        hint="h",
    )
    assert apply_simple_fix(doc_path, v) is False


# --------------------------------------------------------------------------- #
# fix.py:135-137 — _apply_simple_fix corrupted-zip except block
# --------------------------------------------------------------------------- #


def test_apply_simple_fix_corrupted_zip_returns_false(tmp_path: Path):
    """fix.py:135-137 — BadZipFile inside the simple-fix path is caught and returns False."""
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip at all")
    v = Violation(
        rule_id="D-H09",  # match the only rule the simple-fix path attempts
        severity=Severity.HARD,
        fix_category=FixCategory.SAFE,
        message="m",
        hint="h",
    )
    assert apply_simple_fix(bad, v) is False


# --------------------------------------------------------------------------- #
# fix.py:167-169 — apply_fixes BackupFailedError re-raise
# --------------------------------------------------------------------------- #


def test_apply_fixes_backup_failure_reraises(monkeypatch, tmp_path: Path):
    """fix.py:167-169 — apply_fixes catches BackupFailedError and re-raises."""
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(b"placeholder")

    import mint_python.fix as fix_mod

    def boom(_: Path) -> Path:
        raise BackupFailedError("simulated permission denial")

    monkeypatch.setattr(fix_mod, "create_backup", boom)
    v = Violation(
        rule_id="D-H09",
        severity=Severity.HARD,
        fix_category=FixCategory.SAFE,
        message="m",
        hint="h",
    )
    with pytest.raises(BackupFailedError, match="simulated"):
        apply_fixes(doc_path, [v], rules_dir=tmp_path)


# --------------------------------------------------------------------------- #
# fix.py:206 — apply_fixes early-break when post-validate yields no fixables
# --------------------------------------------------------------------------- #


def test_apply_fixes_breaks_when_post_validate_clears_fixables(
    monkeypatch, tmp_path: Path
):
    """fix.py:206 — after a successful fix iteration, if re-validation reports no
    SAFE/VISUAL violations remaining, apply_fixes breaks out of the loop.
    """
    doc_path = tmp_path / "doc.docx"
    # Build a minimal valid zip so create_backup + _compute_file_hash succeed.
    with zipfile.ZipFile(doc_path, "w") as zf:
        zf.writestr("placeholder", b"")

    import mint_python.fix as fix_mod
    from mint_python.validate import ValidationReport

    # First iteration: _apply_simple_fix returns True so iteration_fixes
    # is non-empty; we then re-run run_checks which reports zero violations.
    monkeypatch.setattr(fix_mod, "_apply_simple_fix", lambda _p, _v: True)
    monkeypatch.setattr(
        fix_mod,
        "run_checks",
        lambda *_a, **_kw: ValidationReport(
            violations=[],
            total=0,
            hard_count=0,
            soft_count=0,
            mode="lenient",
            passed=True,
        ),
    )

    v = Violation(
        rule_id="D-H09",
        severity=Severity.HARD,
        fix_category=FixCategory.SAFE,
        message="m",
        hint="h",
    )
    report = apply_fixes(doc_path, [v], rules_dir=tmp_path)
    assert report.applied_fixes == ["D-H09"]
    assert report.remaining_violations == []
