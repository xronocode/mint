# FILE: src/mint/config.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Central configuration for MINT runtime
#   SCOPE: Load config from env vars and .env file, validate, expose as typed dataclass
#   DEPENDS: none
#   LINKS: docs/knowledge-graph.xml#M-CONFIG, docs/verification-plan.xml#V-M-CONFIG
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   MintConfig - typed dataclass with all runtime settings
#   ConfigError - base exception for config errors
#   ConfigMissingError - raised when required config is absent
#   ConfigInvalidError - raised when config value is invalid
#   Tier - StrEnum of supported model tiers
#   SeverityMode - StrEnum of supported severity modes
#   Engine - StrEnum {JS, PYTHON} selecting the runtime engine (Phase-6)
#   load_config - load and validate config from env + .env file
#   config - module-level singleton config instance (lazy)
# END_MODULE_MAP

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - Phase-6: added Engine StrEnum + engine field on
#                MintConfig + MINT_ENGINE parsing
#   PREVIOUS:    v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

_LOG_PREFIX = "Config"
logger = logging.getLogger(__name__)

VALID_TIERS = ("small", "medium", "frontier")
VALID_SEVERITY_MODES = ("audit", "lenient", "strict")
VALID_ENGINES = ("python", "js")
DEFAULT_SEVERITY_MODE = "audit"
DEFAULT_ENGINE = "python"
DEFAULT_SANDBOX_TIMEOUT = 30


def _parse_int(raw: str, field_name: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise ConfigInvalidError(
            f"{field_name} must be an integer, got '{raw}'"
        ) from None



class ConfigError(Exception):
    """Base configuration error."""


class ConfigMissingError(ConfigError):
    """Raised when a required configuration value is absent."""


class ConfigInvalidError(ConfigError):
    """Raised when a configuration value is invalid."""


class Tier(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    FRONTIER = "frontier"


class SeverityMode(StrEnum):
    AUDIT = "audit"
    LENIENT = "lenient"
    STRICT = "strict"


class Engine(StrEnum):
    JS = "js"
    PYTHON = "python"


# START_CONTRACT: MintConfig
#   PURPOSE: Typed configuration dataclass holding all MINT runtime settings
#   INPUTS: { field values }
#   OUTPUTS: { MintConfig instance }
#   SIDE_EFFECTS: none
#   LINKS: docs/development-plan.xml#M-CONFIG
# END_CONTRACT: MintConfig
@dataclass(frozen=True)
class MintConfig:
    llm_base_url: str
    llm_model: str
    model_tier: Tier
    severity_mode: SeverityMode
    sandbox_timeout: int
    rules_dir: Path
    skills_dir: Path
    templates_dir: Path
    tokens_dir: Path
    engine: Engine = Engine.PYTHON
    output_dir: Path = field(default_factory=lambda: Path("output"))

    def __post_init__(self) -> None:
        if not self.llm_base_url:
            raise ConfigMissingError("LLM_BASE_URL is required")
        if not self.llm_model:
            raise ConfigMissingError("LLM_MODEL is required")


# START_CONTRACT: load_config
#   PURPOSE: Load and validate configuration from env vars and optional .env file
#   INPUTS: { env_file: Optional[Path] - path to .env file }
#   OUTPUTS: { MintConfig - validated configuration instance }
#   SIDE_EFFECTS: reads environment variables and .env file
#   LINKS: docs/verification-plan.xml#V-M-CONFIG
# END_CONTRACT: load_config
def load_config(env_file: Path | None = None) -> MintConfig:
    # START_BLOCK_LOAD_ENV
    if env_file is not None and env_file.exists():
        file_values = dotenv_values(env_file)
        for key, value in file_values.items():
            if key and value is not None and key not in os.environ:
                os.environ[key] = value
    # END_BLOCK_LOAD_ENV

    # START_BLOCK_VALIDATE_REQUIRED
    llm_base_url = os.environ.get("LLM_BASE_URL", "").strip()
    if not llm_base_url:
        raise ConfigMissingError(
            "LLM_BASE_URL is required but not set. "
            "Set it as an environment variable or in a .env file."
        )

    llm_model = os.environ.get("LLM_MODEL", "").strip()
    if not llm_model:
        raise ConfigMissingError(
            "LLM_MODEL is required but not set. "
            "Set it as an environment variable or in a .env file."
        )
    # END_BLOCK_VALIDATE_REQUIRED

    # START_BLOCK_VALIDATE_OPTIONS
    tier_str = os.environ.get("MINT_MODEL_TIER", "medium").strip().lower()
    if tier_str not in VALID_TIERS:
        raise ConfigInvalidError(
            f"MINT_MODEL_TIER must be one of {VALID_TIERS}, got '{tier_str}'"
        )

    severity_str = os.environ.get("MINT_SEVERITY_MODE", DEFAULT_SEVERITY_MODE).strip().lower()
    if severity_str not in VALID_SEVERITY_MODES:
        raise ConfigInvalidError(
            f"MINT_SEVERITY_MODE must be one of {VALID_SEVERITY_MODES}, got '{severity_str}'"
        )

    engine_str = os.environ.get("MINT_ENGINE", DEFAULT_ENGINE).strip().lower()
    if engine_str not in VALID_ENGINES:
        raise ConfigInvalidError(
            f"MINT_ENGINE must be one of {VALID_ENGINES} "
            f"(valid values: 'python', 'js'), got '{engine_str}'"
        )
    # END_BLOCK_VALIDATE_OPTIONS

    # START_BLOCK_BUILD_PATHS
    project_root = Path(os.environ.get("MINT_ROOT", ".")).resolve()
    rules_dir = project_root / os.environ.get("MINT_RULES_DIR", "rules")
    skills_dir = project_root / os.environ.get("MINT_SKILLS_DIR", "skills")
    templates_dir = project_root / os.environ.get("MINT_TEMPLATES_DIR", "templates")
    tokens_dir = project_root / os.environ.get("MINT_TOKENS_DIR", "tokens")
    # END_BLOCK_BUILD_PATHS

    cfg = MintConfig(
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        model_tier=Tier(tier_str),
        severity_mode=SeverityMode(severity_str),
        sandbox_timeout=_parse_int(
            os.environ.get("MINT_SANDBOX_TIMEOUT", str(DEFAULT_SANDBOX_TIMEOUT)),
            "MINT_SANDBOX_TIMEOUT",
        ),
        rules_dir=rules_dir,
        skills_dir=skills_dir,
        templates_dir=templates_dir,
        tokens_dir=tokens_dir,
        engine=Engine(engine_str),
    )

    # START_BLOCK_LOAD_CONFIG
    logger.info(
        f"[{_LOG_PREFIX}][load][BLOCK_LOAD_CONFIG] "
        "Config loaded: tier=%s severity=%s engine=%s sandbox_timeout=%d",
        cfg.model_tier.value,
        cfg.severity_mode.value,
        cfg.engine.value,
        cfg.sandbox_timeout,
    )
    # END_BLOCK_LOAD_CONFIG

    return cfg


_singleton: MintConfig | None = None
_singleton_lock = threading.Lock()


# START_CONTRACT: config
#   PURPOSE: Module-level singleton config instance (lazy-loaded)
#   INPUTS: { none - reads from env }
#   OUTPUTS: { MintConfig - singleton instance }
#   SIDE_EFFECTS: lazy-loads config on first access
#   LINKS: docs/knowledge-graph.xml#M-CONFIG
# END_CONTRACT: config
def config() -> MintConfig:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = load_config()
    return _singleton
