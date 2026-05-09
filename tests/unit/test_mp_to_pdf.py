# FILE: tests/unit/test_mp_to_pdf.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Verify Document.to_pdf Gotenberg integration — mock-based tests
#     covering successful conversion, error responses, unreachable server,
#     custom/default output paths, file existence, temp cleanup, absence of
#     BLOCK_PHASE_GUARD after unstub, and BLOCK_RENDER_PDF trace emission.
#   SCOPE: All 9 scenarios from Wave-11-2 specification. Uses
#     unittest.mock.patch("httpx.post") since pytest_httpx is unavailable.
#   DEPENDS: pytest, unittest.mock, httpx, mint_python.core.document
#   LINKS: docs/verification-plan.xml#V-MP-DOCUMENT,
#     docs/development-plan.xml#Wave-11-2
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_to_pdf_successful_conversion
#   test_to_pdf_gotenberg_returns_error
#   test_to_pdf_gotenberg_unreachable
#   test_to_pdf_custom_output_path
#   test_to_pdf_default_output_path
#   test_to_pdf_output_exists_and_nonempty
#   test_to_pdf_temp_docx_cleaned_up
#   test_to_pdf_no_phase_guard_emitted
#   test_to_pdf_block_render_pdf_emitted
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-11-2 — initial creation: 9 scenarios for to_pdf Gotenberg integration
# END_CHANGE_SUMMARY
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mint_python.core.document import Document, GotenbergError
from mint_python.core.section import Section

PDF_BYTES = b"%PDF-1.4 mock pdf content"
TMP_DIR_HEX4 = Path(f"/tmp/mint_pdf_output_")


# ---------------------------------------------------------------------------
# Test 1: Successful conversion — mock Gotenberg returns 200 + PDF bytes
# ---------------------------------------------------------------------------


def test_to_pdf_successful_conversion(tmp_path: Path, caplog_at_info) -> None:
    output_path = tmp_path / "out.pdf"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = PDF_BYTES

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = doc.to_pdf(output_path=output_path)

    assert result == output_path
    assert output_path.exists()
    assert output_path.read_bytes() == PDF_BYTES
    mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: Gotenberg returns error (500)
# ---------------------------------------------------------------------------


def test_to_pdf_gotenberg_returns_error(tmp_path: Path) -> None:
    output_path = tmp_path / "out.pdf"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", return_value=mock_response):
        with pytest.raises(GotenbergError) as exc_info:
            doc.to_pdf(output_path=output_path)

    assert "500" in str(exc_info.value)
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# Test 3: Gotenberg unreachable — httpx.ConnectError → GotenbergError
# ---------------------------------------------------------------------------


def test_to_pdf_gotenberg_unreachable(tmp_path: Path) -> None:
    output_path = tmp_path / "out.pdf"

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")):
        with pytest.raises((GotenbergError, httpx.ConnectError)) as exc_info:
            doc.to_pdf(output_path=output_path)

    assert not output_path.exists()


# ---------------------------------------------------------------------------
# Test 4: Custom output_path — verify PDF saved to specified path
# ---------------------------------------------------------------------------


def test_to_pdf_custom_output_path(tmp_path: Path) -> None:
    custom_path = tmp_path / "custom" / "report.pdf"
    custom_path.parent.mkdir(parents=True, exist_ok=True)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = PDF_BYTES

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", return_value=mock_response):
        result = doc.to_pdf(output_path=custom_path)

    assert result == custom_path
    assert custom_path.exists()
    assert custom_path.read_bytes() == PDF_BYTES


# ---------------------------------------------------------------------------
# Test 5: Default output path — verify PDF created when output_path is None
# ---------------------------------------------------------------------------


def test_to_pdf_default_output_path() -> None:
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = PDF_BYTES

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", return_value=mock_response):
        result = doc.to_pdf()

    assert result.exists()
    assert result.match("mint_pdf_output_*.pdf")
    assert result.read_bytes() == PDF_BYTES
    assert str(result).startswith("/tmp/mint_pdf_output_")
    result.unlink()


# ---------------------------------------------------------------------------
# Test 6: PDF output exists and non-empty
# ---------------------------------------------------------------------------


def test_to_pdf_output_exists_and_nonempty(tmp_path: Path) -> None:
    output_path = tmp_path / "out.pdf"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = PDF_BYTES

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", return_value=mock_response):
        doc.to_pdf(output_path=output_path)

    assert output_path.exists()
    assert output_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Test 7: Temp docx cleaned up after to_pdf
# ---------------------------------------------------------------------------


def test_to_pdf_temp_docx_cleaned_up(tmp_path: Path) -> None:
    output_path = tmp_path / "out.pdf"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = PDF_BYTES

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    temp_dir = Path(tempfile.gettempdir())
    before_files = set(temp_dir.glob("tmp*.docx"))

    with patch("httpx.post", return_value=mock_response):
        doc.to_pdf(output_path=output_path)

    after_files = set(temp_dir.glob("tmp*.docx"))
    new_files = after_files - before_files
    assert len(new_files) == 0, f"Leftover temp .docx: {new_files}"


# ---------------------------------------------------------------------------
# Test 8: BLOCK_PHASE_GUARD absent from to_pdf path after unstub
# ---------------------------------------------------------------------------


def test_to_pdf_no_phase_guard_emitted(tmp_path: Path, caplog_at_info) -> None:
    output_path = tmp_path / "out.pdf"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = PDF_BYTES

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", return_value=mock_response):
        doc.to_pdf(output_path=output_path)

    guard_records = [
        r for r in caplog_at_info.records if "BLOCK_PHASE_GUARD" in r.getMessage()
    ]
    assert len(guard_records) == 0, "BLOCK_PHASE_GUARD must not be emitted after unstub"


# ---------------------------------------------------------------------------
# Test 9: trace marker — BLOCK_RENDER_PDF emitted with output + size
# ---------------------------------------------------------------------------


def test_to_pdf_block_render_pdf_emitted(tmp_path: Path, caplog_at_info) -> None:
    output_path = tmp_path / "out.pdf"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = PDF_BYTES

    doc = (
        Document(format="docx", title="Test")
        .with_style_preset("alga_corporate")
        .add_section(Section("S1", level=1).add_paragraph("Content"))
    )

    with patch("httpx.post", return_value=mock_response):
        doc.to_pdf(output_path=output_path)

    render_records = [
        r for r in caplog_at_info.records if "BLOCK_RENDER_PDF" in r.getMessage()
    ]
    assert len(render_records) == 1
    payload = render_records[0].getMessage()
    assert "[MP-Document]" in payload
    assert "[to_pdf]" in payload
    assert "Rendered PDF: output=" in payload
    assert f"output={output_path}" in payload
    assert f"size={len(PDF_BYTES)} bytes" in payload
