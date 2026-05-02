import json
from pathlib import Path

from mint.mcp_g2 import mint_create, mint_extract_style, mint_list_templates

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestMintCreate:
    def test_create_returns_result(self) -> None:
        code = (FIXTURES / "hello_world_docx.js").read_text()
        result = json.loads(
            mint_create(
                format="docx",
                prompt="hello world",
                tier="frontier",
                model_response_override=code,
            )
        )
        assert result["success"] is True
        assert result["execution_mode"] == "code"
        assert result["output_path"] is not None

    def test_create_with_invalid_tier(self) -> None:
        result = json.loads(
            mint_create(
                format="docx",
                prompt="test",
                tier="invalid",
            )
        )
        assert result["success"] is False


class TestMintExtractStyle:
    def test_extract_returns_tokens(self) -> None:
        result = json.loads(mint_extract_style(str(FIXTURES / "minimal_valid.docx")))
        assert "colors" in result
        assert result["format"] == "docx"

    def test_extract_pptx(self) -> None:
        result = json.loads(mint_extract_style(str(FIXTURES / "minimal_valid.pptx")))
        assert result["format"] == "pptx"


class TestMintListTemplates:
    def test_list_returns_array(self) -> None:
        result = json.loads(mint_list_templates())
        assert isinstance(result, list)
        assert len(result) >= 1
        names = [t["name"] for t in result]
        assert "business-memo" in names

    def test_list_entries_have_required_fields(self) -> None:
        result = json.loads(mint_list_templates())
        for t in result:
            assert "name" in t
            assert "format" in t
            assert "source" in t
