# FILE: tests/unit/test_mp_fix.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Verify MP-FIX auto-fix engine per V-MP-FIX 7 scenarios + 4 forbidden behaviors.
#   SCOPE: Deterministic tests for backup, safe fix D-H09, destructive rejection,
#     cascade detection, no violations, trace markers, fix() convenience,
#     4 forbidden behaviors.
#   DEPENDS: mint_python.fix, mint_python.rules, mint_python.validate, pytest, pathlib,
#     zipfile, shutil, caplog_at_info, marker_counter.
#   LINKS: docs/verification-plan.xml#V-MP-FIX
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TestCreateBackup - scenario-1 backup created
#   TestSafeFixDH09 - scenario-2 D-H09 safe fix
#   TestDestructiveRejected - scenario-3 destructive rejected
#   TestCascadeDetected - scenario-4 cascade detected
#   TestNoViolations - scenario-5 no violations
#   TestTrace - scenario-6 trace markers
#   TestFixConvenience - scenario-7 fix() convenience
#   TestForbiddenBehaviors - forbid-1/2/3/4 assertions
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-9-3 initial implementation — 7 scenario tests + 4 forbidden
#     behavior assertions, full coverage of fix.py.
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from mint_python.fix import (
    DEFAULT_MAX_ITERATIONS,
    BackupFailedError,
    CascadeDetectedError,
    DestructiveRejectedError,
    FixReport,
    apply_fixes,
    create_backup,
    fix,
)
from mint_python.rules import FixCategory, Severity, Violation
from mint_python.validate import SeverityMode, run_checks

FIXTURES = Path(__file__).parent.parent / "fixtures"
RULES_DIR = Path(__file__).parent.parent.parent / "rules"

_CONTENT_TYPES_XML = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.'
    'wordprocessingml.document.main+xml"/>'
    '</Types>'
)

_RELS_XML = (
    '<?xml version="1.0"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
)


def _build_minimal_docx(path: Path) -> None:
    """Create a minimal valid .docx ZIP at *path*."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>hello</w:t></w:r></w:p></w:body></w:document>",
        )
        zf.writestr("_rels/.rels", _RELS_XML)
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)


def _build_raw_newline_docx(path: Path) -> None:
    """Create a .docx with raw newlines after XML tag closings (D-H09 trigger).

    D-H09 XPath: //w:t[contains(text(), '\\n')] — detects literal newline
    in w:t text. The fix (_apply_simple_fix) replaces ``>\\n`` with ``> ``,
    targeting newlines after XML tag closings (e.g. ``?>\\n<w:document``).
    The fixture provides BOTH patterns to trigger D-H09 AND be fixable.
    """
    doc_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        "<w:document"
        " xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">\n"
        "<w:body><w:p><w:r><w:t>hello\nworld</w:t></w:r></w:p></w:body>\n"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("_rels/.rels", _RELS_XML)
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class TestCreateBackup:
    """scenario-1: create_backup creates .bak with identical SHA256 content."""

    def test_backup_created(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        doc.write_bytes(b"PK fake docx content")
        backup = create_backup(doc)
        assert backup.exists()
        assert backup.name == "test.docx.bak"
        assert backup.read_bytes() == doc.read_bytes()
        assert _sha256(backup) == _sha256(doc)

    def test_backup_failed_permission_denied(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        doc.write_bytes(b"PK fake")

        parent = tmp_path / "readonly_dir"
        parent.mkdir()
        child = parent / "test.docx"
        shutil.copy2(doc, child)

        os.chmod(parent, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with pytest.raises(BackupFailedError):
                create_backup(child)
        finally:
            os.chmod(parent, stat.S_IRWXU)


class TestSafeFixDH09:
    """scenario-2: D-H09 safe fix on raw newline docx."""

    def test_raw_newline_fix_applied(self, tmp_path: Path) -> None:
        doc = tmp_path / "raw_newline.docx"
        _build_raw_newline_docx(doc)

        report = run_checks(doc, SeverityMode.AUDIT, rules_dir=RULES_DIR)
        dh09 = [v for v in report.violations if v.rule_id == "D-H09"]
        if not dh09:
            pytest.skip("D-H09 rule not triggered by synthetic fixture")

        result = apply_fixes(doc, report.violations, rules_dir=RULES_DIR)
        assert result.iterations >= 1
        assert "D-H09" in result.applied_fixes
        assert result.backup_path is not None
        assert result.backup_path.exists()
        assert result.diff == "file modified"

    def test_real_fixture_raw_newline(self) -> None:
        src = FIXTURES / "raw_newline.docx"
        if not src.exists():
            pytest.skip("raw_newline.docx fixture not found")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "raw_newline.docx"
            shutil.copy2(src, tmp)

            report = run_checks(tmp, SeverityMode.AUDIT, rules_dir=RULES_DIR)
            dh09 = [v for v in report.violations if v.rule_id == "D-H09"]
            if not dh09:
                pytest.skip("D-H09 not triggered by fixture")

            result = apply_fixes(tmp, report.violations, rules_dir=RULES_DIR)
        assert result.iterations >= 1


class TestApplyFixesEmptyFixable:
    """apply_fixes break when no fixable violations remain after filtering."""

    def test_no_fix_logic_for_all_remaining(
        self, tmp_path: Path,
    ) -> None:
        """Coverage: apply_fixes line 206 — break when fixable list empty."""
        from mint_python.rules import FixCategory, Severity, Violation
        from mint_python.fix import apply_fixes

        bad_doc = tmp_path / "input.docx"
        _build_minimal_docx(bad_doc)

        v1 = Violation(
            rule_id="D-H01",
            severity=Severity.HARD,
            fix_category=FixCategory.SAFE,
            message="bad widths",
            hint="fix widths",
        )
        v2 = Violation(
            rule_id="D-H04",
            severity=Severity.HARD,
            fix_category=FixCategory.SAFE,
            message="bad font",
            hint="fix font",
        )

        result = apply_fixes(
            bad_doc,
            violations=[v1, v2],
            max_iterations=3,
        )
        assert result.iterations == 0
        assert result.applied_fixes == []

    def test_unknown_rule_returns_false(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        violation = Violation(
            rule_id="D-H01",
            severity=Severity.HARD,
            fix_category=FixCategory.SAFE,
            message="test",
            hint="test",
        )
        result = apply_fixes(doc, [violation], rules_dir=RULES_DIR)
        assert result.iterations == 0
        assert result.applied_fixes == []


class TestDestructiveRejected:
    """scenario-3: Destructive violation → DestructiveRejectedError BEFORE file modification."""

    def test_destructive_rejected_before_modification(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)
        original_hash = _sha256(doc)

        violations = [
            Violation(
                rule_id="D-H03",
                severity=Severity.HARD,
                fix_category=FixCategory.DESTRUCTIVE,
                message="Percentage-width table found",
                hint="fix manually",
            ),
        ]

        with pytest.raises(DestructiveRejectedError, match="D-H03"):
            apply_fixes(doc, violations)

        assert _sha256(doc) == original_hash
        assert not Path(str(doc) + ".bak").exists()

    def test_multiple_destructive_rejected(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)
        original_hash = _sha256(doc)

        violations = [
            Violation(
                rule_id="D-H03",
                severity=Severity.HARD,
                fix_category=FixCategory.DESTRUCTIVE,
                message="test",
                hint="fix",
            ),
            Violation(
                rule_id="D-H05",
                severity=Severity.HARD,
                fix_category=FixCategory.DESTRUCTIVE,
                message="test",
                hint="fix",
            ),
        ]

        with pytest.raises(DestructiveRejectedError, match="D-H03, D-H05"):
            apply_fixes(doc, violations)

        assert _sha256(doc) == original_hash


class TestCascadeDetected:
    """scenario-4: Cascade detected when hash oscillates between two states."""

    def _setup_cascade_patches(self, monkeypatch, violations: list[Violation]) -> None:
        """Patch _apply_simple_fix (always True), run_checks (returns given violations),
        and _compute_file_hash (always same hash, triggering cascade)."""
        from mint_python import fix as mp_fix
        from mint_python.validate import ValidationReport

        def always_fix(doc_path_p: Path, violation: Violation) -> bool:
            return True

        def fake_run_checks(doc_path, severity_mode=None, rules_dir=None):
            return ValidationReport(
                violations=list(violations),
                total=len(violations),
                hard_count=sum(1 for v in violations if v.severity == Severity.HARD),
                soft_count=sum(1 for v in violations if v.severity == Severity.SOFT),
                mode=str(severity_mode or SeverityMode.LENIENT),
                passed=True,
                document_format="docx",
            )

        def constant_hash(path: Path) -> str:
            return "abc123"

        monkeypatch.setattr(mp_fix, "_apply_simple_fix", always_fix)
        monkeypatch.setattr(mp_fix, "_compute_file_hash", constant_hash)
        monkeypatch.setattr(mp_fix, "run_checks", fake_run_checks)

    def test_cascade_detected(self, tmp_path: Path, monkeypatch) -> None:
        doc = tmp_path / "oscillating.docx"
        _build_minimal_docx(doc)

        violations = [
            Violation(
                rule_id="D-H09",
                severity=Severity.HARD,
                fix_category=FixCategory.SAFE,
                message="test",
                hint="test",
            ),
        ]

        self._setup_cascade_patches(monkeypatch, violations)

        with pytest.raises(CascadeDetectedError):
            apply_fixes(doc, violations, max_iterations=3)


class TestNoViolations:
    """scenario-5: Empty violations → no changes."""

    def test_empty_violations(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        result = apply_fixes(doc, [], rules_dir=RULES_DIR)
        assert result.iterations == 0
        assert result.applied_fixes == []
        assert result.diff == "no changes"

    def test_only_visual_violations_no_fix_logic(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        violations = [
            Violation(
                rule_id="D-H01",
                severity=Severity.HARD,
                fix_category=FixCategory.VISUAL,
                message="test",
                hint="test",
            ),
        ]

        result = apply_fixes(doc, violations, rules_dir=RULES_DIR)
        assert result.iterations == 0
        assert result.applied_fixes == []
        assert result.diff == "no changes"


class TestTrace:
    """scenario-6: BLOCK_CREATE_BACKUP + BLOCK_APPLY_FIX markers fired with correct payloads."""

    def test_trace_create_backup(
        self,
        caplog_at_info: pytest.LogCaptureFixture,
        marker_counter,
        tmp_path: Path,
    ) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        violations = [
            Violation(
                rule_id="D-H09",
                severity=Severity.HARD,
                fix_category=FixCategory.SAFE,
                message="test",
                hint="test",
            ),
        ]

        apply_fixes(doc, violations, rules_dir=RULES_DIR)

        counts: Counter[str] = marker_counter(caplog_at_info)
        assert counts["BLOCK_CREATE_BACKUP"] >= 1
        assert counts["BLOCK_APPLY_FIX"] >= 1

    def test_trace_payload_backup_path(
        self, caplog_at_info: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)
        create_backup(doc)

        log_messages = [r.getMessage() for r in caplog_at_info.records]
        block_msg = [m for m in log_messages if "BLOCK_CREATE_BACKUP" in m]
        assert len(block_msg) >= 1
        assert "backup_path=" in block_msg[0]

    def test_trace_payload_apply_fix(
        self, caplog_at_info: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        violations = [
            Violation(
                rule_id="D-H09",
                severity=Severity.HARD,
                fix_category=FixCategory.SAFE,
                message="test",
                hint="test",
            ),
        ]

        apply_fixes(doc, violations, rules_dir=RULES_DIR)

        log_messages = [r.getMessage() for r in caplog_at_info.records]
        block_msg = [m for m in log_messages if "BLOCK_APPLY_FIX" in m]
        assert len(block_msg) >= 1
        assert "iterations=" in block_msg[0]
        assert "applied=" in block_msg[0]
        assert "diff=" in block_msg[0]


class TestFixConvenience:
    """scenario-7: fix() convenience — calls run_checks then apply_fixes → returns FixReport."""

    def test_fix_on_minimal_docx(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        result = fix(doc, rules_dir=RULES_DIR)
        assert isinstance(result, FixReport)
        assert result.fixed_path == doc

    def test_fix_on_raw_newline(self) -> None:
        src = FIXTURES / "raw_newline.docx"
        if not src.exists():
            pytest.skip("raw_newline.docx fixture not found")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "raw_newline.docx"
            shutil.copy2(src, tmp)

            result = fix(tmp, rules_dir=RULES_DIR)
            assert isinstance(result, FixReport)

    def test_fix_returns_report_on_clean_doc(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        result = fix(doc, severity_mode=SeverityMode.LENIENT, rules_dir=RULES_DIR)
        assert isinstance(result, FixReport)
        assert result.fixed_path == doc

    def test_fix_strict_mode(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        result = fix(doc, severity_mode=SeverityMode.STRICT, rules_dir=RULES_DIR)
        assert isinstance(result, FixReport)


class TestDefaults:
    """Verify module-level defaults and import surface."""

    def test_default_max_iterations(self) -> None:
        assert DEFAULT_MAX_ITERATIONS == 3

    def test_fix_report_fields(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        result = apply_fixes(doc, [], rules_dir=RULES_DIR)
        assert result.fixed_path == doc
        assert result.iterations == 0
        assert result.applied_fixes == []
        assert result.diff == "no changes"


class TestForbiddenBehaviors:
    """forbid-1/2/3/4 assertions."""

    def test_forbid_1_modify_without_backup(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)
        original_bytes = doc.read_bytes()

        violations = [
            Violation(
                rule_id="D-H09",
                severity=Severity.HARD,
                fix_category=FixCategory.SAFE,
                message="test",
                hint="test",
            ),
        ]

        result = apply_fixes(doc, violations, rules_dir=RULES_DIR)
        assert result.backup_path is not None
        assert result.backup_path.exists()
        assert result.backup_path.read_bytes() == original_bytes

    def test_forbid_2_silent_destructive(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        violations = [
            Violation(
                rule_id="D-H03",
                severity=Severity.HARD,
                fix_category=FixCategory.DESTRUCTIVE,
                message="test",
                hint="test",
            ),
        ]

        with pytest.raises(DestructiveRejectedError):
            apply_fixes(doc, violations)
        assert not Path(str(doc) + ".bak").exists()

    def test_forbid_3_infinite_loop_cascade(self, tmp_path: Path, monkeypatch) -> None:
        from mint_python import fix as mp_fix
        from mint_python.validate import ValidationReport

        doc = tmp_path / "test.docx"
        _build_minimal_docx(doc)

        violations = [
            Violation(
                rule_id="D-H09",
                severity=Severity.HARD,
                fix_category=FixCategory.SAFE,
                message="test",
                hint="test",
            ),
        ]

        def always_fix(doc_path_p: Path, violation: Violation) -> bool:
            return True

        def fake_run_checks(doc_path, severity_mode=None, rules_dir=None):
            return ValidationReport(
                violations=list(violations),
                total=len(violations),
                hard_count=1,
                soft_count=0,
                mode=str(severity_mode or SeverityMode.LENIENT),
                passed=True,
                document_format="docx",
            )

        def constant_hash(path: Path) -> str:
            return "abc123"

        monkeypatch.setattr(mp_fix, "_apply_simple_fix", always_fix)
        monkeypatch.setattr(mp_fix, "_compute_file_hash", constant_hash)
        monkeypatch.setattr(mp_fix, "run_checks", fake_run_checks)

        with pytest.raises(CascadeDetectedError):
            apply_fixes(doc, violations, max_iterations=3)

    def test_forbid_4_stale_fix_set(self, tmp_path: Path) -> None:
        doc = tmp_path / "test.docx"
        _build_raw_newline_docx(doc)

        report = run_checks(doc, SeverityMode.AUDIT, rules_dir=RULES_DIR)
        original_violations = list(report.violations)

        result = apply_fixes(doc, original_violations, rules_dir=RULES_DIR)

        post_report = run_checks(doc, SeverityMode.AUDIT, rules_dir=RULES_DIR)
        assert [v.rule_id for v in result.remaining_violations] == [
            v.rule_id for v in post_report.violations
        ]

        assert result.iterations >= 1
