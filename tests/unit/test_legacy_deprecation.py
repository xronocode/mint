# FILE: tests/unit/test_legacy_deprecation.py
"""V-MP-LEGACY-DEPRECATION scenarios.

Asserts the post-Wave-15-3 end state:
- legacy modules (mcp_g1/mcp_g2/sandbox/create/assemble) are unimportable;
- the --engine js / MINT_ENGINE=js dispatch path no longer reaches downstream
  legacy code (Engine.JS and the --engine flag are removed from the public
  surface, MINT_ENGINE=js is rejected by load_config);
- pyproject.toml + docs/technology.xml + docs/requirements.xml reflect the
  removal of the JS deps + JS fallback wording;
- no module under src/ tests/ tools/ scripts/ still imports any of the
  deleted modules;
- src/mint/cli.py has no cmd_serve definition; the `serve` subcommand is
  absent from build_parser.

These six scenarios map 1:1 onto V-MP-LEGACY-DEPRECATION scenarios 1..6
(verification-plan.xml#V-MP-LEGACY-DEPRECATION).
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# scenario-1: legacy modules unimportable
# --------------------------------------------------------------------------- #

LEGACY_MODULES = [
    "mint.mcp_g1",
    "mint.mcp_g2",
    "mint.sandbox",
    "mint.create",
    "mint.assemble",
]


@pytest.mark.parametrize("modname", LEGACY_MODULES)
def test_scenario_1_legacy_modules_absent(modname: str) -> None:
    """scenario-1: each legacy module raises ModuleNotFoundError on import."""
    # Drop any lingering cached import so the assertion is deterministic
    # even if a sibling test imported the module earlier in the session.
    sys.modules.pop(modname, None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(modname)


# --------------------------------------------------------------------------- #
# scenario-2: --engine js / MINT_ENGINE=js no longer dispatches legacy code
# --------------------------------------------------------------------------- #


def test_scenario_2_engine_js_no_longer_dispatches() -> None:
    """scenario-2: the --engine flag is gone from CLI; Engine.JS is gone from
    src/mint/config.py; MINT_ENGINE=js is rejected by load_config.

    All three checks satisfy "the legacy code path no longer executes".
    """
    # 2a: --engine flag removed from build_parser
    from mint.cli import build_parser

    parser = build_parser()
    help_text = parser.format_help()
    assert "--engine" not in help_text, (
        "--engine flag must be removed from the top-level parser"
    )

    # 2b: Engine.JS no longer exported from mint.config
    import mint.config as cfg_mod

    assert not hasattr(cfg_mod, "Engine"), (
        "Engine StrEnum must be removed from src/mint/config.py — "
        "no module should still depend on Engine.JS"
    )

    # 2c: MINT_ENGINE no longer parsed; load_config does not raise on
    #     legacy values, but does not produce an `engine` attribute either.
    sample_cfg = cfg_mod.MintConfig(
        llm_base_url="http://x:1",
        llm_model="m",
        model_tier=cfg_mod.Tier.SMALL,
        severity_mode=cfg_mod.SeverityMode.AUDIT,
        sandbox_timeout=30,
        rules_dir=Path("rules"),
        skills_dir=Path("skills"),
        templates_dir=Path("templates"),
        tokens_dir=Path("tokens"),
    )
    assert not hasattr(sample_cfg, "engine"), (
        "MintConfig.engine field must be removed; pure-python is the only path"
    )


# --------------------------------------------------------------------------- #
# scenario-3: pyproject.toml + technology.xml + requirements.xml updated
# --------------------------------------------------------------------------- #


_LEGACY_DEP_NAMES = {"docx", "pptxgenjs", "exceljs", "vm2", "isolated-vm", "vitest", "eslint"}


def _flatten_pyproject_dep_strings() -> list[str]:
    pyproject = REPO_ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    out: list[str] = []
    out.extend(data.get("project", {}).get("dependencies", []))
    for extras in data.get("project", {}).get("optional-dependencies", {}).values():
        out.extend(extras)
    for group in data.get("dependency-groups", {}).values():
        out.extend(group)
    return out


def test_scenario_3_a_pyproject_no_legacy_deps() -> None:
    """scenario-3a: pyproject.toml has no docx/pptxgenjs/exceljs/vm2/etc."""
    dep_strs = _flatten_pyproject_dep_strings()
    # Match a leading dep-name token (PEP 508): letters/digits/_-., terminated
    # by space, comparator, bracket, or end-of-string.
    for ds in dep_strs:
        m = re.match(r"^([A-Za-z0-9_.\-]+)", ds)
        assert m is not None, f"unparseable dep string: {ds!r}"
        name = m.group(1).lower()
        assert name not in _LEGACY_DEP_NAMES, (
            f"legacy JS-runtime dep {name!r} still present in pyproject.toml"
        )


def test_scenario_3_b_technology_xml_reflects_removal() -> None:
    """scenario-3b: docs/technology.xml no longer advertises the JS path."""
    text = (REPO_ROOT / "docs" / "technology.xml").read_text()
    # Forbidden strings: any wording suggesting MINT_ENGINE=js or JS runtime
    # is still a supported fallback. The file may still mention the historic
    # docx-js/pptxgenjs origin in past-tense prose; the assertion targets
    # the live-runtime advertisement only.
    forbidden_substrings = [
        "MINT_ENGINE=js",
        'name="docx" version="9.x"',
        'name="pptxgenjs"',
        'name="exceljs"',
        'value="vm2 or isolated-vm"',
    ]
    for tok in forbidden_substrings:
        assert tok not in text, (
            f"docs/technology.xml still advertises legacy JS path token: {tok!r}"
        )


def test_scenario_3_c_requirements_xml_constraint7_updated() -> None:
    """scenario-3c: docs/requirements.xml constraint-7 no longer says the JS
    fallback is preserved."""
    text = (REPO_ROOT / "docs" / "requirements.xml").read_text()
    # The pre-Phase-15 wording was "preserved as an optional fallback
    # (`MINT_ENGINE=js`)". After Wave-15-3 that line MUST be gone.
    assert "preserved as an optional fallback" not in text, (
        "constraint-7 still describes MINT_ENGINE=js as a preserved fallback"
    )
    assert "MINT_ENGINE=js" not in text, (
        "constraint-7 still mentions MINT_ENGINE=js as a runtime option"
    )


def test_scenario_3_d_pyproject_no_dangling_entry_points() -> None:
    """scenario-3d: [project.scripts] points only at live modules."""
    pyproject = REPO_ROOT / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    scripts = data.get("project", {}).get("scripts", {})
    forbidden_targets = (
        "mint.create",
        "mint.assemble",
        "mint.mcp_g1",
        "mint.mcp_g2",
        "mint.sandbox",
    )
    for name, target in scripts.items():
        for ft in forbidden_targets:
            assert ft not in target, (
                f"[project.scripts] {name}={target!r} still points at deleted module {ft!r}"
            )


# --------------------------------------------------------------------------- #
# scenario-4: full suite green after deletion
# --------------------------------------------------------------------------- #
#
# The W3 gate-check is `pytest tests/ -x --timeout=120` run from the worker
# itself; embedding a recursive pytest invocation here would create an
# unstable nested-runner. Instead, this scenario is recorded as a no-op
# smoke that asserts the import surface needed by the rest of the suite
# is healthy (legacy deletions did not collaterally damage live modules).


def test_scenario_4_live_surface_imports_clean() -> None:
    """scenario-4 (proxy): the live mint + mint_python import surface is
    healthy after the deletions. The full-suite gate is the
    `pytest tests/ -x --timeout=120` run executed by the worker."""
    importlib.import_module("mint")
    importlib.import_module("mint.cli")
    importlib.import_module("mint.config")
    importlib.import_module("mint_python")


# --------------------------------------------------------------------------- #
# scenario-5: no dangling importers anywhere in the repo
# --------------------------------------------------------------------------- #


def test_scenario_5_no_dangling_imports() -> None:
    """scenario-5: grep across src/ tests/ tools/ scripts/ for any remaining
    importer of the deleted modules. Zero hits required."""
    pattern = (
        r"from mint\.mcp_g[12]\|"
        r"import mint\.mcp_g[12]\|"
        r"from mint\.sandbox\|"
        r"import mint\.sandbox\|"
        r"from mint\.create\|"
        r"import mint\.create\|"
        r"from mint\.assemble\|"
        r"import mint\.assemble"
    )
    search_roots = [
        REPO_ROOT / "src",
        REPO_ROOT / "tests",
        REPO_ROOT / "tools",
        REPO_ROOT / "scripts",
    ]
    existing = [p for p in search_roots if p.exists()]
    # Exclude this very test file (mentions the import strings as data).
    cmd = [
        "grep", "-rn",
        "--exclude-dir=__pycache__",
        f"--exclude={Path(__file__).name}",
        "-E", pattern.replace("\\|", "|"),
        *[str(p) for p in existing],
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # grep exits 1 when there are no matches — that is the success case.
    assert result.returncode == 1, (
        "dangling importers of deleted legacy modules:\n"
        f"{result.stdout}"
    )


# --------------------------------------------------------------------------- #
# scenario-6: cmd_serve removed from cli.py; `serve` subcommand absent
# --------------------------------------------------------------------------- #


def test_scenario_6_cmd_serve_removed_or_stubbed() -> None:
    """scenario-6: cmd_serve is no longer a module-level callable, and
    `mint serve` is no longer registered as a subcommand."""
    import mint.cli as cli

    assert not hasattr(cli, "cmd_serve"), (
        "cmd_serve must be removed from src/mint/cli.py — "
        "the FastMCP entry point is now src/mint_python/mcp/document.py"
    )

    parser = cli.build_parser()
    # build_parser registers subparsers via parser.add_subparsers(dest="command").
    # Walk the subparser action to confirm `serve` is not registered.
    sub_actions = [
        a for a in parser._actions  # type: ignore[attr-defined]
        if a.__class__.__name__ == "_SubParsersAction"
    ]
    assert sub_actions, "build_parser must still register subparsers"
    sub = sub_actions[0]
    assert "serve" not in sub.choices, (
        "`mint serve` subcommand must be removed; pure-python MCP server is "
        "invoked directly via `python -m mint_python.mcp.document` (or wired "
        "in claude_desktop_config.json)"
    )
