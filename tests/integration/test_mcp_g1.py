import json
import shutil
import tempfile
from pathlib import Path

from mint.mcp_g1 import mint_fingerprint, mint_fix, mint_validate

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestMintValidate:
    def test_valid_docx_returns_passed(self) -> None:
        result = json.loads(mint_validate(str(FIXTURES / "minimal_valid.docx")))
        assert result["passed"] is True
        assert isinstance(result["violations"], list)

    def test_bad_columns_returns_violations(self) -> None:
        result = json.loads(
            mint_validate(str(FIXTURES / "bad_column_widths.docx"), "lenient")
        )
        assert result["passed"] is False
        assert any(v["rule_id"] == "D-H01" for v in result["violations"])

    def test_audit_mode_passes(self) -> None:
        result = json.loads(
            mint_validate(str(FIXTURES / "bad_column_widths.docx"), "audit")
        )
        assert result["passed"] is True
        assert result["hard_count"] >= 1


class TestMintFix:
    def test_fix_returns_report(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "test.docx"
        shutil.copy2(FIXTURES / "minimal_valid.docx", tmp)

        result = json.loads(mint_fix(str(tmp)))
        assert "iterations" in result
        assert "applied_fixes" in result
        assert "backup_path" in result


class TestMintFingerprint:
    def test_returns_hash(self) -> None:
        result = json.loads(mint_fingerprint(str(FIXTURES / "minimal_valid.docx")))
        assert len(result["hash"]) == 64
        assert result["format"] == "docx"

    def test_pptx_returns_hash(self) -> None:
        result = json.loads(mint_fingerprint(str(FIXTURES / "minimal_valid.pptx")))
        assert len(result["hash"]) == 64
        assert result["format"] == "pptx"
