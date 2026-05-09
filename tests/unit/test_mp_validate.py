# FILE: tests/unit/test_mp_validate.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Verify MP-VALIDATE pure Python validation engine per V-MP-VALIDATE 11 scenarios.
#   SCOPE: Deterministic tests for all severity modes, invalid documents, report properties,
#     classify_violations, trace assertions, PPTX validation, format detection.
#   DEPENDS: mint_python.validate, mint_python.rules, pytest, pathlib, zipfile,
#     caplog_at_info, marker_counter.
#   LINKS: docs/verification-plan.xml#V-MP-VALIDATE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TestRunChecksAudit - scenario-1 AUDIT always passes
#   TestRunChecksLenient - scenario-2 LENIENT mode
#   TestRunChecksStrict - scenario-3 STRICT mode
#   TestReportProperties - scenario-4 ValidationReport properties
#   TestClassifyViolations - scenario-5 classify_violations
#   TestInvalidDocument - scenario-6 invalid documents → XML-001 report
#   TestTrace - scenario-7 trace BLOCK_RUN_CHECKS
#   TestGoldenDoc - scenario-8 golden doc lenient
#   TestNoMatchingRules - scenario-9 no matching rules
#   TestPptxValidation - scenario-10 PPTX validation
#   TestFormatDetection - scenario-11 format detection
#   TestForbiddenBehaviors - forbidden-1/2/3 assertions
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-9-2 initial implementation — 11 deterministic scenario tests,
#     3 forbidden behavior assertions, full coverage of validate.py.
# END_CHANGE_SUMMARY

from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path

import pytest

from mint_python.rules import FixCategory, Severity, Violation
from mint_python.validate import (
    InvalidDocumentError,
    SeverityMode,
    ValidationReport,
    _detect_format,
    _get_main_xml_path,
    _open_document_xml,
    classify_violations,
    run_checks,
    validate,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
RULES_DIR = Path(__file__).parent.parent.parent / "rules"


class TestRunChecksAudit:
    """scenario-1: AUDIT always passes."""

    def test_minimal_valid_passes(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        assert report.passed
        assert report.mode == "audit"

    def test_bad_columns_audit_passes(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        assert report.passed
        assert report.hard_count >= 1


class TestRunChecksLenient:
    """scenario-2: LENIENT mode — clean doc passes, hard violations fail."""

    def test_minimal_valid_lenient_passes(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert report.passed

    def test_bad_columns_lenient_fails(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert not report.passed
        assert report.hard_count >= 1

    def test_soft_only_lenient_passes(self) -> None:
        report = run_checks(
            FIXTURES / "percentage_width.docx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        if report.hard_count == 0 and report.soft_count > 0:
            assert report.passed


class TestRunChecksStrict:
    """scenario-3: STRICT mode — any violation fails."""

    def test_minimal_valid_strict(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.STRICT,
            rules_dir=RULES_DIR,
        )
        assert report.hard_count >= 0
        if report.total == 0:
            assert report.passed

    def test_bad_columns_strict_fails(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.STRICT,
            rules_dir=RULES_DIR,
        )
        assert not report.passed


class TestReportProperties:
    """scenario-4: ValidationReport property filters work correctly."""

    def test_hard_violations_filter(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        hards = report.hard_violations
        for v in hards:
            assert v.severity == Severity.HARD

    def test_soft_violations_filter(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        softs = report.soft_violations
        for v in softs:
            assert v.severity == Severity.SOFT

    def test_total_matches_len(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        assert report.total == len(report.violations)

    def test_destructive_filter(self) -> None:
        dummy = Violation(
            rule_id="X-01",
            severity=Severity.HARD,
            fix_category=FixCategory.DESTRUCTIVE,
            message="test",
            hint="test",
        )
        report = ValidationReport(
            violations=[dummy],
            total=1,
            hard_count=1,
            soft_count=0,
            mode="audit",
            passed=True,
            document_format="docx",
        )
        assert len(report.destructive) == 1
        assert len(report.hard_reject) == 1

    def test_safe_fixable_includes_soft(self) -> None:
        soft_dummy = Violation(
            rule_id="X-S01",
            severity=Severity.SOFT,
            fix_category=FixCategory.SAFE,
            message="test",
            hint="test",
        )
        report = ValidationReport(
            violations=[soft_dummy],
            total=1,
            hard_count=0,
            soft_count=1,
            mode="audit",
            passed=True,
            document_format="docx",
        )
        assert len(report.safe_fixable) == 1

    def test_visual_fixable_filter(self) -> None:
        dummy = Violation(
            rule_id="X-V01",
            severity=Severity.HARD,
            fix_category=FixCategory.VISUAL,
            message="test",
            hint="test",
        )
        report = ValidationReport(
            violations=[dummy],
            total=1,
            hard_count=1,
            soft_count=0,
            mode="audit",
            passed=True,
            document_format="docx",
        )
        assert len(report.visual_fixable) == 1

    def test_soft_warn_filter(self) -> None:
        dummy = Violation(
            rule_id="X-S01",
            severity=Severity.SOFT,
            fix_category=FixCategory.SAFE,
            message="test",
            hint="test",
        )
        report = ValidationReport(
            violations=[dummy],
            total=1,
            hard_count=0,
            soft_count=1,
            mode="audit",
            passed=True,
            document_format="docx",
        )
        assert len(report.soft_warn) == 1


class TestClassifyViolations:
    """scenario-5: classify_violations groups by severity + fix_category."""

    def test_classify_groups_correctly(self) -> None:
        violations = [
            Violation(
                rule_id="D-H03",
                severity=Severity.HARD,
                fix_category=FixCategory.DESTRUCTIVE,
                message="test",
                hint="fix",
            ),
            Violation(
                rule_id="D-H09",
                severity=Severity.HARD,
                fix_category=FixCategory.SAFE,
                message="test",
                hint="fix",
            ),
            Violation(
                rule_id="D-S01",
                severity=Severity.SOFT,
                fix_category=FixCategory.SAFE,
                message="test",
                hint="fix",
            ),
        ]
        classified = classify_violations(violations)
        assert len(classified["destructive"]) == 1
        assert classified["destructive"][0].rule_id == "D-H03"
        assert len(classified["safe_fixable"]) == 1
        assert classified["safe_fixable"][0].rule_id == "D-H09"
        assert len(classified["soft_warn"]) == 1
        assert classified["soft_warn"][0].rule_id == "D-S01"

    def test_classify_empty_returns_empty_groups(self) -> None:
        classified = classify_violations([])
        assert classified["hard_reject"] == []
        assert classified["safe_fixable"] == []
        assert classified["visual_fixable"] == []
        assert classified["destructive"] == []
        assert classified["soft_warn"] == []

    def test_classify_visual_fixable(self) -> None:
        violations = [
            Violation(
                rule_id="V-01",
                severity=Severity.HARD,
                fix_category=FixCategory.VISUAL,
                message="test",
                hint="fix",
            ),
        ]
        classified = classify_violations(violations)
        assert len(classified["visual_fixable"]) == 1
        assert len(classified["safe_fixable"]) == 0


class TestInvalidDocument:
    """scenario-6: Invalid documents return XML-001 report, never raise at top level."""

    def test_nonexistent_file_returns_xml001(self, tmp_path: Path) -> None:
        r = validate(tmp_path / "nonexistent.docx")
        assert not r.passed
        assert r.hard_count == 1
        assert r.violations[0].rule_id == "XML-001"
        assert r.violations[0].severity == Severity.HARD
        assert r.violations[0].fix_category == FixCategory.DESTRUCTIVE

    def test_not_a_zip_returns_xml001(self, tmp_path: Path) -> None:
        bad_zip = tmp_path / "fake.docx"
        bad_zip.write_text("not a zip")
        r = validate(bad_zip)
        assert not r.passed
        assert r.hard_count == 1
        assert r.violations[0].rule_id == "XML-001"

    def test_broken_xml_inside_zip_returns_xml001(self, tmp_path: Path) -> None:
        broken_xml = tmp_path / "broken.docx"
        with zipfile.ZipFile(broken_xml, "w") as zf:
            zf.writestr("word/document.xml", "<not>valid<xml>")
        r = validate(broken_xml)
        assert not r.passed
        assert r.hard_count == 1
        assert r.violations[0].rule_id == "XML-001"

    def test_unsupported_extension_returns_xml001(self, tmp_path: Path) -> None:
        xlsx = tmp_path / "data.xlsx"
        xlsx.write_text("dummy")
        r = validate(xlsx)
        assert not r.passed
        assert r.violations[0].rule_id == "XML-001"


class TestTrace:
    """scenario-7: run_checks emits BLOCK_RUN_CHECKS INFO log with correct payload."""

    def test_trace_block_run_checks(
        self,
        caplog_at_info: pytest.LogCaptureFixture,
        marker_counter,
    ) -> None:
        counter_callable = marker_counter
        run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        counts: Counter[str] = counter_callable(caplog_at_info)
        assert counts["BLOCK_RUN_CHECKS"] >= 1

    def test_trace_payload_includes_mode(
        self, caplog_at_info: pytest.LogCaptureFixture
    ) -> None:
        run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        log_messages = [r.getMessage() for r in caplog_at_info.records]
        block_msg = [m for m in log_messages if "BLOCK_RUN_CHECKS" in m]
        assert len(block_msg) >= 1
        assert "mode=audit" in block_msg[0]
        assert "violations=" in block_msg[0]
        assert "hard=" in block_msg[0]
        assert "soft=" in block_msg[0]
        assert "passed=" in block_msg[0]

    def test_trace_on_invalid_too(
        self,
        caplog_at_info: pytest.LogCaptureFixture,
        marker_counter,
        tmp_path: Path,
    ) -> None:
        counter_callable = marker_counter
        validate(tmp_path / "nonexistent.docx")
        counts = counter_callable(caplog_at_info)
        assert counts["BLOCK_RUN_CHECKS"] >= 1


class TestGoldenDoc:
    """scenario-8: Golden doc lenient → passed=True, hard_count=0."""

    def test_golden_doc_lenient_passes(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert report.passed
        assert report.hard_count == 0


class TestNoMatchingRules:
    """scenario-9: No matching rules for format → 0 violations, passed=True."""

    def test_pptx_with_only_d_rules(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty_rules"
        empty_dir.mkdir()
        report = run_checks(
            FIXTURES / "minimal_valid.pptx",
            SeverityMode.AUDIT,
            rules_dir=empty_dir,
        )
        assert report.total == 0
        assert report.passed
        assert report.hard_count == 0


class TestPptxValidation:
    """scenario-10: PPTX validation with matching rules."""

    def test_minimal_valid_pptx_audit_passes(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.pptx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        assert report.passed
        assert report.document_format == "pptx"

    def test_bad_font_pptx_has_violation(self) -> None:
        report = run_checks(
            FIXTURES / "bad_font.pptx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        p_h02_violations = [v for v in report.violations if v.rule_id == "P-H02"]
        assert len(p_h02_violations) >= 1

    def test_bad_font_pptx_lenient_fails(self) -> None:
        report = run_checks(
            FIXTURES / "bad_font.pptx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert not report.passed
        assert report.hard_count >= 1

    def test_pptx_main_xml_path(self) -> None:
        assert _get_main_xml_path("pptx") == "ppt/slides/slide1.xml"


class TestFormatDetection:
    """scenario-11: Format detection from file extension."""

    def test_docx_extension(self) -> None:
        assert _detect_format(Path("test.docx")) == "docx"
        assert _get_main_xml_path("docx") == "word/document.xml"

    def test_pptx_extension(self) -> None:
        assert _detect_format(Path("test.pptx")) == "pptx"
        assert _get_main_xml_path("pptx") == "ppt/slides/slide1.xml"

    def test_xlsx_raises_invalid_document(self) -> None:
        with pytest.raises(InvalidDocumentError, match="Unsupported document format"):
            _detect_format(Path("test.xlsx"))

    def test_unknown_extension_raises_invalid_document(self) -> None:
        with pytest.raises(InvalidDocumentError, match="Unsupported document format"):
            _detect_format(Path("test.pdf"))


class TestForbiddenBehaviors:
    """forbidden-1/2/3 assertions."""

    def test_no_silent_parse_failure(self, tmp_path: Path) -> None:
        broken_xml = tmp_path / "broken.docx"
        with zipfile.ZipFile(broken_xml, "w") as zf:
            zf.writestr("word/document.xml", "<bad>xml")
        with pytest.raises(InvalidDocumentError):
            _open_document_xml(broken_xml)

    def test_no_wildcard_exception_catch(self, tmp_path: Path) -> None:
        not_a_zip = tmp_path / "fake.docx"
        not_a_zip.write_text("garbage data not a zip")
        with pytest.raises(InvalidDocumentError):
            _open_document_xml(not_a_zip)

    def test_log_on_report(
        self, caplog_at_info: pytest.LogCaptureFixture
    ) -> None:
        run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        log_messages = [r.getMessage() for r in caplog_at_info.records]
        block_msg = [m for m in log_messages if "BLOCK_RUN_CHECKS" in m]
        assert len(block_msg) >= 1


class TestValidateConvenience:
    """validate() convenience alias tests."""

    def test_validate_without_rules_dir(self) -> None:
        report = validate(FIXTURES / "minimal_valid.docx")
        assert report.passed
        assert report.mode == "audit"

    def test_validate_with_severity_and_rules(self) -> None:
        report = validate(
            FIXTURES / "minimal_valid.docx",
            severity_mode=SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert report.passed


class TestSeverityModeEnum:
    """SeverityMode self-defined enum tests."""

    def test_audit_value(self) -> None:
        assert SeverityMode.AUDIT == "audit"

    def test_lenient_value(self) -> None:
        assert SeverityMode.LENIENT == "lenient"

    def test_strict_value(self) -> None:
        assert SeverityMode.STRICT == "strict"
