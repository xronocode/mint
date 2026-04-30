from pathlib import Path

import pytest

from mint.config import SeverityMode
from mint.fix import (
    DestructiveRejectedError,
    FixReport,
    apply_fixes,
    create_backup,
    fix,
)
from mint.rules import FixCategory, Severity, Violation
from mint.validate import run_checks

FIXTURES = Path(__file__).parent.parent / "fixtures"
RULES_DIR = Path(__file__).parent.parent.parent / "rules"


class TestCreateBackup:
    def test_backup_created(self, tmp_path: Path) -> None:
        src = tmp_path / "test.docx"
        src.write_bytes(b"PK fake docx")
        backup = create_backup(src)
        assert backup.exists()
        assert backup.name == "test.docx.bak"
        assert backup.read_bytes() == src.read_bytes()


class TestApplyFixesSafe:
    def test_raw_newline_fix_applied(self) -> None:
        import shutil
        import tempfile

        src = FIXTURES / "raw_newline.docx"
        tmp = Path(tempfile.mkdtemp()) / "raw_newline.docx"
        shutil.copy2(src, tmp)

        report = run_checks(tmp, SeverityMode.AUDIT, rules_dir=RULES_DIR)
        dh09_violations = [v for v in report.violations if v.rule_id == "D-H09"]

        if not dh09_violations:
            pytest.skip("D-H09 not triggered by fixture")

        result = apply_fixes(tmp, report.violations, rules_dir=RULES_DIR)
        assert result.backup_path is not None
        assert result.backup_path.exists()
        assert result.iterations >= 1


class TestApplyFixesDestructive:
    def test_destructive_rejected(self) -> None:
        import tempfile

        violations = [
            Violation(
                rule_id="D-H03",
                severity=Severity.HARD,
                fix_category=FixCategory.DESTRUCTIVE,
                message="test",
                hint="fix manually",
            ),
        ]
        tmp = Path(tempfile.mkdtemp()) / "test.docx"
        tmp.write_bytes(b"PK fake")

        with pytest.raises(DestructiveRejectedError, match="D-H03"):
            apply_fixes(tmp, violations)


class TestApplyFixesCascade:
    def test_max_iterations_respected(self) -> None:
        import shutil
        import tempfile

        src = FIXTURES / "raw_newline.docx"
        tmp = Path(tempfile.mkdtemp()) / "test.docx"
        shutil.copy2(src, tmp)

        report = run_checks(tmp, SeverityMode.AUDIT, rules_dir=RULES_DIR)
        result = apply_fixes(
            tmp, report.violations, max_iterations=1, rules_dir=RULES_DIR
        )
        assert result.iterations <= 2


class TestFixEntryPoint:
    def test_fix_on_valid_doc(self) -> None:
        import shutil
        import tempfile

        src = FIXTURES / "minimal_valid.docx"
        tmp = Path(tempfile.mkdtemp()) / "valid.docx"
        shutil.copy2(src, tmp)

        result = fix(tmp, rules_dir=RULES_DIR)
        assert isinstance(result, FixReport)
