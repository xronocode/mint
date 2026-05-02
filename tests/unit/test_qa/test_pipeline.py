from pathlib import Path

from mint.qa import (
    L1Report,
    L2Report,
    QAReport,
    run_l1,
    run_l2,
    run_qa,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"
RULES_DIR = Path(__file__).parent.parent.parent.parent / "rules"


class TestRunL1:
    def test_valid_docx_passes(self) -> None:
        report = run_l1(FIXTURES / "minimal_valid.docx", rules_dir=RULES_DIR)
        assert isinstance(report, L1Report)
        assert report.passed is True
        assert report.duration_ms >= 0

    def test_bad_docx_fails(self) -> None:
        report = run_l1(FIXTURES / "bad_column_widths.docx", rules_dir=RULES_DIR)
        assert report.passed is False
        assert report.violations >= 1

    def test_fingerprint_populated(self) -> None:
        report = run_l1(FIXTURES / "minimal_valid.docx", rules_dir=RULES_DIR)
        assert len(report.fingerprint_hash) == 64

    def test_pptx_works(self) -> None:
        report = run_l1(FIXTURES / "minimal_valid.pptx", rules_dir=RULES_DIR)
        assert report.passed is True


class TestRunL2:
    def test_gotenberg_unavailable(self) -> None:
        report = run_l2(
            FIXTURES / "minimal_valid.docx",
            gotenberg_url="http://localhost:99999",
        )
        assert isinstance(report, L2Report)
        assert report.available is False
        assert report.error is not None


class TestRunQA:
    def test_full_qa_valid_docx(self) -> None:
        report = run_qa(
            FIXTURES / "minimal_valid.docx",
            rules_dir=RULES_DIR,
            gotenberg_url="http://localhost:99999",
        )
        assert isinstance(report, QAReport)
        assert 0.0 <= report.overall_confidence <= 1.0
        assert report.passed is True

    def test_full_qa_bad_docx(self) -> None:
        report = run_qa(
            FIXTURES / "bad_column_widths.docx",
            rules_dir=RULES_DIR,
            gotenberg_url="http://localhost:99999",
        )
        assert report.passed is False
