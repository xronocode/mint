from pathlib import Path

import pytest

from mint.templates import (
    FillResult,
    TemplateEngine,
    TemplateNotFoundError,
)

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"


class TestListTemplates:
    def test_list_finds_builtin(self) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        templates = engine.list_templates()
        assert len(templates) >= 1
        names = [t.name for t in templates]
        assert "business-memo" in names

    def test_list_empty_dir(self, tmp_path: Path) -> None:
        engine = TemplateEngine(tmp_path / "nonexistent")
        assert engine.list_templates() == []

    def test_list_ignores_non_ooxml(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        (builtin / "readme.txt").write_text("not a template")
        engine = TemplateEngine(tmp_path)
        assert engine.list_templates() == []


class TestFindTemplate:
    def test_find_by_name(self) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        meta = engine.find_template("business-memo")
        assert meta.name == "business-memo"
        assert meta.format == "docx"
        assert meta.path.is_file()

    def test_find_by_name_and_format(self) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        meta = engine.find_template("business-memo", fmt="docx")
        assert meta.format == "docx"

    def test_find_nonexistent_raises(self) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        with pytest.raises(TemplateNotFoundError, match="not found"):
            engine.find_template("nonexistent-template")


class TestFill:
    def test_fill_replaces_placeholders(self) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        meta = engine.find_template("business-memo")
        content = {
            "title": "Quarterly Report",
            "sections": [
                {
                    "heading": "Executive Summary",
                    "paragraphs": ["This is the summary paragraph."],
                }
            ],
        }
        result = engine.fill(meta, content)
        assert isinstance(result, FillResult)
        assert result.output_path.is_file()
        assert len(result.placeholders_replaced) >= 1
        assert "title" in result.placeholders_replaced

    def test_fill_output_is_valid_zip(self) -> None:
        import zipfile

        engine = TemplateEngine(TEMPLATES_DIR)
        meta = engine.find_template("business-memo")
        content = {"title": "Test", "sections": [{"heading": "H", "paragraphs": ["P"]}]}
        result = engine.fill(meta, content)
        assert zipfile.is_zipfile(result.output_path)

    def test_fill_with_explicit_output_path(self, tmp_path: Path) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        meta = engine.find_template("business-memo")
        output = tmp_path / "output.docx"
        content = {"title": "Explicit Output", "sections": [{"heading": "H", "paragraphs": ["P"]}]}
        result = engine.fill(meta, content, output_path=output)
        assert result.output_path == output
        assert output.is_file()

    def test_fill_with_design_tokens(self) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        meta = engine.find_template("business-memo")
        content = {"title": "Test", "sections": [{"heading": "H", "paragraphs": ["P"]}]}
        tokens = {"colors": {"primary": "#FF0000"}}
        result = engine.fill(meta, content, design_tokens=tokens)
        assert result.tokens_applied is True

    def test_fill_no_tokens(self) -> None:
        engine = TemplateEngine(TEMPLATES_DIR)
        meta = engine.find_template("business-memo")
        content = {"title": "No Tokens", "sections": [{"heading": "H", "paragraphs": ["P"]}]}
        result = engine.fill(meta, content, design_tokens=None)
        assert result.tokens_applied is False
