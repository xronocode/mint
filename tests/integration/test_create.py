from pathlib import Path

from mint.create import CreateRequest, CreateResult, create

FIXTURES = Path(__file__).parent.parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
RULES_DIR = PROJECT_ROOT / "rules"


class TestCreateCodeMode:
    def test_code_mode_with_hello_world_docx(self) -> None:
        code = (FIXTURES / "hello_world_docx.js").read_text()
        req = CreateRequest(
            format="docx",
            tier="frontier",
            prompt="Create a hello world document",
            model_response_override=code,
        )
        result = create(
            req,
            skills_dir=SKILLS_DIR,
            templates_dir=TEMPLATES_DIR,
            rules_dir=RULES_DIR,
        )
        assert isinstance(result, CreateResult)
        assert result.execution_mode == "code"
        assert result.success is True
        assert result.output_path is not None
        assert result.output_path.exists()

    def test_code_mode_with_sandbox_violation(self) -> None:
        code = (FIXTURES / "malicious_fs.js").read_text()
        req = CreateRequest(
            format="docx",
            tier="frontier",
            prompt="Malicious test",
            model_response_override=code,
        )
        result = create(
            req,
            skills_dir=SKILLS_DIR,
            templates_dir=TEMPLATES_DIR,
            rules_dir=RULES_DIR,
        )
        assert result.success is False
        assert "SANDBOX" in result.error or "Sandbox" in result.error

    def test_code_mode_no_response_no_llm_returns_error(self) -> None:
        req = CreateRequest(
            format="docx",
            tier="frontier",
            prompt="Test",
            llm_base_url="http://localhost:99999",
            llm_model="test",
        )
        result = create(
            req,
            skills_dir=SKILLS_DIR,
            templates_dir=TEMPLATES_DIR,
            rules_dir=RULES_DIR,
        )
        assert result.success is False
        assert result.error is not None
        assert "LLM" in result.error or "model" in result.error.lower()


class TestCreateTemplateMode:
    def test_template_mode_with_json_content(self) -> None:
        content_json = (
            '{"title": "Test Doc", '
            '"sections": [{"heading": "Intro", "paragraphs": ["Hello"]}]}'
        )
        req = CreateRequest(
            format="docx",
            tier="small",
            prompt="Create a business memo",
            model_response_override=content_json,
            template_name="business-memo",
        )
        result = create(
            req,
            skills_dir=SKILLS_DIR,
            templates_dir=TEMPLATES_DIR,
            rules_dir=RULES_DIR,
        )
        assert isinstance(result, CreateResult)
        assert result.execution_mode == "template"
        assert result.output_path is not None
        assert result.output_path.exists()

    def test_template_mode_invalid_json(self) -> None:
        req = CreateRequest(
            format="docx",
            tier="small",
            prompt="Test",
            model_response_override="not json",
        )
        result = create(
            req,
            skills_dir=SKILLS_DIR,
            templates_dir=TEMPLATES_DIR,
            rules_dir=RULES_DIR,
        )
        assert result.success is False
        assert "JSON" in result.error


class TestCreateEdgeCases:
    def test_invalid_tier_returns_error(self) -> None:
        req = CreateRequest(
            format="docx",
            tier="invalid",
            prompt="Test",
        )
        result = create(
            req,
            skills_dir=SKILLS_DIR,
            templates_dir=TEMPLATES_DIR,
            rules_dir=RULES_DIR,
        )
        assert result.success is False
        assert "tier" in result.error.lower()

    def test_duration_ms_populated(self) -> None:
        req = CreateRequest(
            format="docx",
            tier="frontier",
            prompt="Test",
            llm_base_url="http://localhost:99999",
            llm_model="test",
        )
        result = create(
            req,
            skills_dir=SKILLS_DIR,
            templates_dir=TEMPLATES_DIR,
            rules_dir=RULES_DIR,
        )
        assert result.duration_ms >= 0
