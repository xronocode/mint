from pathlib import Path

import pytest

from mint.config import (
    ConfigInvalidError,
    ConfigMissingError,
    MintConfig,
    SeverityMode,
    Tier,
    config,
    load_config,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env_keys = [
        "LLM_BASE_URL",
        "LLM_MODEL",
        "MINT_MODEL_TIER",
        "MINT_SEVERITY_MODE",
        "MINT_SANDBOX_TIMEOUT",
        "MINT_ROOT",
        "MINT_RULES_DIR",
        "MINT_SKILLS_DIR",
        "MINT_TEMPLATES_DIR",
        "MINT_TOKENS_DIR",
    ]
    for key in env_keys:
        monkeypatch.delenv(key, raising=False)
    import mint.config as cfg

    monkeypatch.setattr(cfg, "_singleton", None)


class TestLoadConfigFromEnv:
    def test_reads_all_required_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        result = load_config()

        assert result.llm_base_url == "http://localhost:11434/v1"
        assert result.llm_model == "gpt-4o"
        assert result.model_tier == Tier.MEDIUM
        assert result.severity_mode == SeverityMode.AUDIT
        assert result.sandbox_timeout == 30

    def test_reads_tier_and_severity_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("MINT_MODEL_TIER", "frontier")
        monkeypatch.setenv("MINT_SEVERITY_MODE", "strict")

        result = load_config()

        assert result.model_tier == Tier.FRONTIER
        assert result.severity_mode == SeverityMode.STRICT

    def test_reads_custom_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("MINT_ROOT", "/tmp/mint-test")

        result = load_config()

        assert result.rules_dir == Path("/tmp/mint-test").resolve() / "rules"
        assert result.skills_dir == Path("/tmp/mint-test").resolve() / "skills"
        assert result.templates_dir == Path("/tmp/mint-test").resolve() / "templates"


class TestLoadConfigFromEnvFile:
    def test_reads_from_env_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_BASE_URL=http://api.example.com/v1\nLLM_MODEL=claude-3\n")

        result = load_config(env_file=env_file)

        assert result.llm_base_url == "http://api.example.com/v1"
        assert result.llm_model == "claude-3"

    def test_env_vars_take_precedence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://from-env/v1")
        monkeypatch.setenv("LLM_MODEL", "from-env-model")

        env_file = tmp_path / ".env"
        env_file.write_text("LLM_BASE_URL=http://from-file/v1\nLLM_MODEL=from-file-model\n")

        result = load_config(env_file=env_file)

        assert result.llm_base_url == "http://from-env/v1"
        assert result.llm_model == "from-env-model"


class TestConfigMissing:
    def test_missing_llm_base_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        with pytest.raises(ConfigMissingError, match="LLM_BASE_URL"):
            load_config()

    def test_missing_llm_model_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")

        with pytest.raises(ConfigMissingError, match="LLM_MODEL"):
            load_config()

    def test_missing_both_raises_with_url_in_message(self) -> None:
        with pytest.raises(ConfigMissingError, match="LLM_BASE_URL"):
            load_config()


class TestConfigInvalid:
    def test_invalid_tier_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("MINT_MODEL_TIER", "huge")

        with pytest.raises(ConfigInvalidError, match="MINT_MODEL_TIER"):
            load_config()

    def test_invalid_severity_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("MINT_SEVERITY_MODE", "ultra")

        with pytest.raises(ConfigInvalidError, match="MINT_SEVERITY_MODE"):
            load_config()


class TestConfigSingleton:
    def test_singleton_returns_same_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        c1 = config()
        c2 = config()

        assert c1 is c2

    def test_singleton_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        result = config()

        assert isinstance(result, MintConfig)
        assert result.llm_model == "gpt-4o"


class TestMintConfigFrozen:
    def test_config_is_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        cfg = load_config()

        with pytest.raises(AttributeError):
            cfg.llm_model = "other"  # type: ignore[misc]
