from pathlib import Path

from mint.fingerprint import (
    DriftStatus,
    FingerprintResult,
    compare,
    compute,
    fingerprint,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestCompute:
    def test_deterministic_hash(self) -> None:
        result1 = compute(FIXTURES / "minimal_valid.docx")
        result2 = compute(FIXTURES / "minimal_valid.docx")
        assert result1.hash == result2.hash
        assert len(result1.hash) == 64

    def test_different_docs_different_hash(self) -> None:
        h1 = compute(FIXTURES / "minimal_valid.docx")
        h2 = compute(FIXTURES / "bad_column_widths.docx")
        assert h1.hash != h2.hash

    def test_pptx_fingerprint(self) -> None:
        result = compute(FIXTURES / "minimal_valid.pptx")
        assert result.format == "pptx"
        assert len(result.hash) == 64

    def test_xml_sources_populated(self) -> None:
        result = compute(FIXTURES / "minimal_valid.docx")
        assert len(result.xml_sources) > 0


class TestCompare:
    def test_match(self) -> None:
        assert compare("abc123", "abc123") == DriftStatus.MATCH

    def test_drift(self) -> None:
        assert compare("abc123", "def456") == DriftStatus.DRIFT

    def test_baseline_missing_a(self) -> None:
        assert compare(None, "abc123") == DriftStatus.BASELINE_MISSING

    def test_baseline_missing_b(self) -> None:
        assert compare("abc123", None) == DriftStatus.BASELINE_MISSING


class TestFingerprintEntryPoint:
    def test_fingerprint_returns_result(self) -> None:
        result = fingerprint(FIXTURES / "minimal_valid.docx")
        assert isinstance(result, FingerprintResult)
        assert result.hash
