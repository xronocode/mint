from pathlib import Path

import pytest

from mint.config import SeverityMode
from mint.validate import (
    InvalidDocumentError,
    classify_violations,
    run_checks,
    validate,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
RULES_DIR = Path(__file__).parent.parent.parent / "rules"


class TestRunChecksAudit:
    def test_minimal_valid_passes(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        assert report.passed
        assert report.mode == "audit"
        assert isinstance(report.violations, list)

    def test_bad_columns_audit_passes_but_counts(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        assert report.passed
        assert report.hard_count >= 1


class TestRunChecksLenient:
    def test_minimal_valid_passes(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert report.passed

    def test_bad_columns_fails(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert not report.passed
        assert report.hard_count >= 1


class TestRunChecksStrict:
    def test_minimal_valid_passes(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.docx",
            SeverityMode.STRICT,
            rules_dir=RULES_DIR,
        )
        hard_violations = report.hard_violations
        assert len(hard_violations) == 0

    def test_bad_columns_strict_fails(self) -> None:
        report = run_checks(
            FIXTURES / "bad_column_widths.docx",
            SeverityMode.STRICT,
            rules_dir=RULES_DIR,
        )
        assert not report.passed


class TestRunChecksErrors:
    def test_nonexistent_file_raises(self) -> None:
        with pytest.raises(InvalidDocumentError):
            run_checks(
                FIXTURES / "nonexistent.docx",
                SeverityMode.AUDIT,
                rules_dir=RULES_DIR,
            )


class TestClassifyViolations:
    def test_classify_groups_correctly(self) -> None:
        from mint.rules import FixCategory, Severity, Violation

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
        assert len(classified["soft_warn"]) == 1


class TestValidateEntryPoint:
    def test_validate_without_config(self) -> None:
        report = validate(FIXTURES / "minimal_valid.docx")
        assert report.passed

    def test_pptx_format(self) -> None:
        report = run_checks(
            FIXTURES / "minimal_valid.pptx",
            SeverityMode.AUDIT,
            rules_dir=RULES_DIR,
        )
        assert report.passed

    def test_bad_font_pptx(self) -> None:
        report = run_checks(
            FIXTURES / "bad_font.pptx",
            SeverityMode.LENIENT,
            rules_dir=RULES_DIR,
        )
        assert not report.passed
        assert report.hard_count >= 1
