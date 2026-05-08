# FILE: tests/unit/test_engine_flag.py
"""V-MP-FLAG scenarios for the Phase-6 dual-engine flag.

Covers MINT_ENGINE env parsing, --engine CLI flag, the _select_engine
chokepoint, frozen-config invariant, .env precedence, and shape guards
on pyproject.toml + .env.example.
"""

# IMPORTANT: This file MUST NOT redefine `clean_env`. The single central
# autouse fixture is in tests/unit/test_config.py with MINT_ENGINE in its
# env_keys list. See V-M-CONFIG forbidden-4 (verification-plan.xml) and
# the swarm-fixture-1 contract in V-MP-FLAG. Local redefinition silently
# breaks env isolation between tests in the same process.

from __future__ import annotations

import dataclasses
import logging
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from mint.cli import _select_engine
from mint.config import ConfigInvalidError, Engine, load_config

# Reuse the central autouse `clean_env` fixture from test_config.py
# (V-M-CONFIG forbidden-4 single-chokepoint invariant). Direct import
# binds the autouse fixture in this module too, without redefining it.
from tests.unit.test_config import clean_env  # noqa: F401  -- reuse central fixture

# --------------------------------------------------------------------------- #
# Deterministic in-process scenarios
# --------------------------------------------------------------------------- #


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimal required env so load_config() succeeds."""
    monkeypatch.setenv("LLM_BASE_URL", "http://x:1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("MINT_MODEL_TIER", "small")


def test_v_mp_flag_1_default_engine_is_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scenario-1: MINT_ENGINE unset -> Engine.PYTHON."""
    _set_required_env(monkeypatch)
    cfg = load_config()
    assert cfg.engine == Engine.PYTHON


def test_v_mp_flag_2_env_override_js(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scenario-2: MINT_ENGINE=js -> Engine.JS."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MINT_ENGINE", "js")
    cfg = load_config()
    assert cfg.engine == Engine.JS


def test_v_mp_flag_3_garbage_raises_with_valid_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scenario-3: MINT_ENGINE=garbage -> ConfigInvalidError listing valid set."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MINT_ENGINE", "garbage")
    with pytest.raises(ConfigInvalidError, match="MINT_ENGINE") as exc:
        load_config()
    msg = str(exc.value)
    assert "python" in msg
    assert "js" in msg


def test_v_mp_flag_4_select_engine_js_returns_js(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scenario-4: _select_engine returns 'js' on Engine.JS."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MINT_ENGINE", "js")
    cfg = load_config()
    assert _select_engine(cfg) == "js"


def test_v_mp_flag_5_select_engine_python_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scenario-5: _select_engine raises NotImplementedError on Engine.PYTHON."""
    _set_required_env(monkeypatch)
    cfg = load_config()
    # safety: replace() works on frozen dataclasses
    cfg = dataclasses.replace(cfg, engine=Engine.PYTHON)
    with pytest.raises(NotImplementedError, match="MINT_ENGINE=python") as exc:
        _select_engine(cfg)
    assert "Phase 0" in str(exc.value)


def test_v_mp_flag_7_frozen_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scenario-7: MintConfig is frozen — engine cannot be reassigned."""
    _set_required_env(monkeypatch)
    cfg = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.engine = Engine.JS  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# scenario-11: env-isolation regression check
#
# The central `clean_env` autouse fixture must scrub MINT_ENGINE between
# tests, even when a test writes directly to os.environ (bypassing
# monkeypatch). Pytest collects tests in definition order by default, so
# we exploit that: part-A writes the env, part-B (next in file) asserts
# the env is clean. If part-B fails, the autouse fixture is broken.
# --------------------------------------------------------------------------- #


def test_v_mp_flag_11_a_writes_env_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scenario-11a: write MINT_ENGINE via os.environ (NOT monkeypatch)."""
    _set_required_env(monkeypatch)
    os.environ["MINT_ENGINE"] = "js"
    cfg = load_config()
    assert cfg.engine == Engine.JS


def test_v_mp_flag_11_b_observes_clean_env() -> None:
    """scenario-11b: clean_env autouse fixture must have scrubbed MINT_ENGINE."""
    assert "MINT_ENGINE" not in os.environ, (
        "central clean_env autouse fixture failed to scrub MINT_ENGINE "
        "between tests — V-M-CONFIG forbidden-4 invariant violated"
    )


# --------------------------------------------------------------------------- #
# scenario-12: .env-file precedence
# --------------------------------------------------------------------------- #


def test_v_mp_flag_12_env_var_beats_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """scenario-12: os.environ wins over .env file."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MINT_ENGINE", "js")
    env_file = tmp_path / ".env"
    env_file.write_text("MINT_ENGINE=python\n")
    cfg = load_config(env_file=env_file)
    assert cfg.engine == Engine.JS


def test_v_mp_flag_12_dotenv_used_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """scenario-12 (inverse): .env value used when MINT_ENGINE not in env."""
    _set_required_env(monkeypatch)
    # MINT_ENGINE is scrubbed by clean_env; do not set it.
    env_file = tmp_path / ".env"
    env_file.write_text("MINT_ENGINE=python\n")
    cfg = load_config(env_file=env_file)
    assert cfg.engine == Engine.PYTHON


# --------------------------------------------------------------------------- #
# scenario-13: pyproject.toml shape guard
# --------------------------------------------------------------------------- #


def test_v_mp_flag_13_pyproject_packages_include_mint_python() -> None:
    """scenario-13: wheel packages include both src/mint and src/mint_python."""
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    packages = data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["src/mint", "src/mint_python"]


# --------------------------------------------------------------------------- #
# scenario-14: .env.example documentation guard
# --------------------------------------------------------------------------- #


def test_v_mp_flag_14_env_example_documents_mint_engine() -> None:
    """scenario-14: .env.example has exactly one commented MINT_ENGINE=js line."""
    env_example_path = Path(__file__).resolve().parents[2] / ".env.example"
    text = env_example_path.read_text()
    matches = re.findall(r"^# *MINT_ENGINE=js", text, flags=re.MULTILINE)
    assert len(matches) == 1, (
        f"expected exactly one commented MINT_ENGINE=js line, got {len(matches)}"
    )


# --------------------------------------------------------------------------- #
# Subprocess scenarios (6, 8) — exit-code / argparse behavior only.
#
# Scenarios 9 and 10 (TRACE assertions on log markers) were DOWNGRADED to
# in-process caplog tests because the runtime does not configure logging
# at INFO level by default — `python -m mint.cli` produces no log output
# without explicit config, so subprocess stderr capture would be unreliable.
# caplog gives deterministic log-record visibility and is functionally
# equivalent for verifying that BLOCK_LOAD_CONFIG / BLOCK_SELECT_ENGINE
# fired with the expected engine= value.
# --------------------------------------------------------------------------- #


def _cli(env_overrides: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run `python -m mint.cli <args>` with explicit, non-inherited env.

    V-MP-FLAG forbidden-5: never inherit os.environ wholesale; pass an
    explicit base env containing only what mint needs to import + run.
    """
    base_env = {
        "LLM_BASE_URL": "http://x:1",
        "LLM_MODEL": "m",
        "MINT_MODEL_TIER": "small",
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }
    base_env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "mint.cli", *args],
        env=base_env,
        capture_output=True,
        text=True,
    )


def test_v_mp_flag_6_help_with_engine_js() -> None:
    """scenario-6: --engine js --help exits 0 and mentions --engine."""
    result = _cli({"MINT_ENGINE": "python"}, "--engine", "js", "--help")
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "--engine" in result.stdout


def test_v_mp_flag_8_engine_after_subcommand_rejected() -> None:
    """scenario-8: --engine after the subcommand is rejected by argparse.

    V-M-CLI scenario-14: the flag is registered on the top-level parser
    BEFORE add_subparsers, so post-subcommand placement must error out.
    """
    result = _cli({}, "validate", "--engine", "js", "fixture.docx")
    assert result.returncode != 0, "argparse should reject post-subcommand --engine"
    # argparse usage errors land on stderr with 'error:' prefix
    assert "error:" in result.stderr.lower() or "unrecognized" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# Scenarios 9 & 10 — caplog (in-process) variants
# --------------------------------------------------------------------------- #


def test_v_mp_flag_9_cli_override_precedence_trace(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """scenario-9: --engine js overrides MINT_ENGINE=python via os.environ write.

    Asserts BOTH log markers fire with engine=js (proves os.environ write
    happened BEFORE config() loaded).
    """
    caplog.set_level(logging.INFO)
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MINT_ENGINE", "python")
    monkeypatch.setattr(sys, "argv", ["mint", "--engine", "js", "validate", "/tmp/no.docx"])

    from mint.cli import main

    # validate /tmp/no.docx will fail (file missing); we only care about
    # engine resolution + log markers, so any exit/exception is acceptable.
    try:
        main()
    except SystemExit:
        pass
    except FileNotFoundError:
        pass

    messages = [r.getMessage() for r in caplog.records]
    joined = "\n".join(messages)
    assert "BLOCK_LOAD_CONFIG" in joined
    assert "BLOCK_SELECT_ENGINE" in joined
    # Both markers must show engine=js
    load_msgs = [m for m in messages if "BLOCK_LOAD_CONFIG" in m]
    sel_msgs = [m for m in messages if "BLOCK_SELECT_ENGINE" in m]
    assert load_msgs and all("engine=js" in m for m in load_msgs), load_msgs
    assert sel_msgs and all("engine=js" in m for m in sel_msgs), sel_msgs
    # Neither marker should ever show engine=python in this scenario
    for m in load_msgs + sel_msgs:
        assert "engine=python" not in m, m


def test_v_mp_flag_10_default_path_negative_trace(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """scenario-10: default path (no MINT_ENGINE, no --engine) must NOT
    enter any downstream block before _select_engine raises Phase-0 error.
    """
    caplog.set_level(logging.INFO)
    _set_required_env(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["mint", "validate", "/tmp/no.docx"])

    from mint.cli import main

    with pytest.raises(NotImplementedError) as exc:
        main()
    err = str(exc.value)
    assert "MINT_ENGINE=python" in err
    assert "Phase 0" in err

    messages = [r.getMessage() for r in caplog.records]
    joined = "\n".join(messages)
    assert "BLOCK_LOAD_CONFIG" in joined
    assert "BLOCK_SELECT_ENGINE" in joined

    forbidden = [
        "[Validate]",
        "[Create]",
        "[Sandbox]",
        "[OOXML]",
        "[Edit]",
        "BLOCK_DISPATCH",
        "BLOCK_RUN_CHECKS",
        "BLOCK_ORCHESTRATE",
        "BLOCK_EXECUTE_CODE",
        "BLOCK_OOXML_UNPACK",
    ]
    for token in forbidden:
        assert token not in joined, (
            f"forbidden downstream marker {token!r} appeared on the default "
            f"engine=python path — _select_engine should short-circuit before any work"
        )
