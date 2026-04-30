from pathlib import Path

import pytest

from mint.extract import (
    ExtractionFailedError,
    analyze_layouts,
    extract_style,
    parse_theme,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestDetectFormat:
    def test_docx(self) -> None:
        result = extract_style(FIXTURES / "minimal_valid.docx")
        assert result["format"] == "docx"

    def test_pptx(self) -> None:
        result = extract_style(FIXTURES / "minimal_valid.pptx")
        assert result["format"] == "pptx"

    def test_non_ooxml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "test.xlsx"
        bad.write_text("not a zip")
        with pytest.raises(ExtractionFailedError, match="Unsupported"):
            extract_style(bad)


class TestExtractStyle:
    def test_docx_returns_tokens(self) -> None:
        result = extract_style(FIXTURES / "minimal_valid.docx")
        assert "colors" in result
        assert "typography" in result
        assert result["format"] == "docx"

    def test_pptx_returns_tokens(self) -> None:
        result = extract_style(FIXTURES / "minimal_valid.pptx")
        assert "colors" in result
        assert result["format"] == "pptx"

    def test_nonexistent_file_raises(self) -> None:
        with pytest.raises(ExtractionFailedError, match="not found"):
            extract_style(Path("/nonexistent/file.docx"))

    def test_invalid_zip_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.docx"
        bad.write_text("not a zip file")
        with pytest.raises(ExtractionFailedError, match="Invalid OOXML"):
            extract_style(bad)


class TestParseTheme:
    def test_docx_has_xml_sources(self) -> None:
        import zipfile

        with zipfile.ZipFile(FIXTURES / "minimal_valid.docx", "r") as zf:
            result = parse_theme("docx", zf)
        assert "xml_sources" in result
        assert isinstance(result["xml_sources"], list)

    def test_pptx_has_xml_sources(self) -> None:
        import zipfile

        with zipfile.ZipFile(FIXTURES / "minimal_valid.pptx", "r") as zf:
            result = parse_theme("pptx", zf)
        assert "xml_sources" in result


class TestAnalyzeLayouts:
    def test_docx_layouts(self) -> None:
        import zipfile

        with zipfile.ZipFile(FIXTURES / "minimal_valid.docx", "r") as zf:
            layouts = analyze_layouts("docx", zf)
        assert isinstance(layouts, list)

    def test_pptx_layouts(self) -> None:
        import zipfile

        with zipfile.ZipFile(FIXTURES / "minimal_valid.pptx", "r") as zf:
            layouts = analyze_layouts("pptx", zf)
        assert isinstance(layouts, list)
