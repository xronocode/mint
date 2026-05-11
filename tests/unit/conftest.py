# FILE: tests/unit/conftest.py
# START_MODULE_CONTRACT
#   PURPOSE: Shared pytest fixtures for Phase-7 (Pure Python Edition Phase 1) MP-* tests.
#     Controller-owned per docs/verification-plan.xml SwarmFixtures/ownership.
#   SCOPE: Provides mp_clean_env autouse + 6 opt-in fixtures consumed by V-MP-* tests
#     and the VF-013 e2e harness. Does NOT redefine the central clean_env fixture in
#     tests/unit/test_config.py — V-M-CONFIG forbidden-4 keeps that as the single
#     chokepoint for required-LLM env scrubbing. (Phase-15 W3 removed MINT_ENGINE.)
#   DEPENDS: pytest, mint_python.sdk (lazy; only the fixtures that touch presets
#     import it; absence is tolerated until Wave-7-1 lands MP-STYLE).
#   LINKS: docs/verification-plan.xml#SwarmFixtures, docs/verification-plan.xml#V-MP-STYLE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   mp_clean_env - autouse: scrub MP_E2E_WRITE_BASELINE + restore presets registry
#   tmp_docx_path - tmp_path / "out.docx"
#   mp_minimal_config - frozen MintConfig(severity=LENIENT) — pure-python only
#   caplog_at_info - caplog wrapper with set_level(INFO)
#   marker_counter - callable: caplog -> Counter[BLOCK_NAME -> count]
#   golden_doc_builder - returns _mp_helpers.build_golden_document
#   schema_violation_factory - parametrized broken-preset producer for V-MP-STYLE
#   mpl_figure_cleanup - autouse: matplotlib rcParams snapshot/leak guard (Phase-8)
#   chart_baseline_path - tests/fixtures/mp_chart_e2e_baseline.json (Phase-8)
#   clean_writers_config - clear MP-AUTH-SHIM cache + scrub MINT_TEMPLATE_WRITERS env (Phase-15)
#   zip_byte_snapshot - sha256 round-trip helper for VF-018/VF-019 read-only invariants (Phase-15)
#   tempdir_snapshot - tempdir entry diff for VF-019 inv-6 TEMP-FILE-CLEANUP (Phase-15)
#   backend_probe_patcher - monkeypatch shutil.which + urllib.request for VF-019 inv-4 (Phase-15)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Phase-15 pre-Wave-15-1: provision controller pre-flight fixtures
#     (clean_writers_config, zip_byte_snapshot, tempdir_snapshot, backend_probe_patcher)
#     per docs/verification-plan.xml SwarmExecutionReadiness/target-15/controller-pre-flight.
#   PRIOR: Phase-8 - mpl_figure_cleanup + chart_baseline_path additions for VF-014.
#   PRIOR: Phase-7 pre-Wave-7-1: initial provisioning per SwarmFixtures/conftest-spec.
# END_CHANGE_SUMMARY
from __future__ import annotations

import logging
import sys
from collections import Counter
from pathlib import Path

import pytest


# START_BLOCK_MP_CLEAN_ENV
@pytest.fixture(autouse=True)
def mp_clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub Phase-7 env vars + snapshot/restore mint_python.sdk.presets registry.

    Complementary to (NOT replacing) clean_env in tests/unit/test_config.py.
    See docs/verification-plan.xml#SwarmFixtures/conftest-spec/fixture-1.
    """
    monkeypatch.delenv("MP_E2E_WRITE_BASELINE", raising=False)

    # Snapshot the presets registry if mint_python.sdk has been imported.
    # Restore at teardown — guards V-MP-STYLE forbidden-3 (no registry mutation
    # by load_preset(path=...)) even if a regression slips in.
    sdk_module = sys.modules.get("mint_python.sdk")
    if sdk_module is not None and hasattr(sdk_module, "presets"):
        snapshot = dict(sdk_module.presets)
        yield
        # Phase-7 Wave-7-5: presets is a MappingProxyType alias to
        # BUILTIN_PRESETS (V-MP-SDK scenario-2 mandates read-only). It cannot
        # be cleared/updated. The snapshot/restore is a no-op safety net that
        # only meaningfully runs if a future regression replaces presets with
        # a mutable Mapping. Verify identity preservation as the actual guard.
        if hasattr(sdk_module.presets, "clear") and hasattr(
            sdk_module.presets, "update"
        ):
            sdk_module.presets.clear()
            sdk_module.presets.update(snapshot)
        else:
            assert dict(sdk_module.presets) == snapshot, (
                "mint_python.sdk.presets mutated during test (read-only contract)"
            )
    else:
        yield
# END_BLOCK_MP_CLEAN_ENV


# START_BLOCK_TMP_DOCX_PATH
@pytest.fixture
def tmp_docx_path(tmp_path: Path) -> Path:
    """Standard tmp_path / 'out.docx' — V-MP-DOCUMENT forbidden-3 mandates tmp_path."""
    return tmp_path / "out.docx"
# END_BLOCK_TMP_DOCX_PATH


# START_BLOCK_MP_MINIMAL_CONFIG
@pytest.fixture
def mp_minimal_config(tmp_path: Path):
    """Frozen MintConfig with severity=LENIENT + sentinel LLM fields.

    Used by VF-013 e2e harness to invoke mint.validate.validate(saved, config)
    in-process without going through M-CLI. Phase-15 Wave-15-3 removed the
    Engine StrEnum and MintConfig.engine field along with the legacy JS
    runtime path; pure-python is now the only execution surface.
    """
    from mint.config import MintConfig, SeverityMode, Tier

    return MintConfig(
        llm_base_url="http://x:1",
        llm_model="m",
        model_tier=Tier.SMALL,
        severity_mode=SeverityMode.LENIENT,
        sandbox_timeout=30,
        rules_dir=tmp_path / "rules",
        skills_dir=tmp_path / "skills",
        templates_dir=tmp_path / "templates",
        tokens_dir=tmp_path / "tokens",
    )
# END_BLOCK_MP_MINIMAL_CONFIG


# START_BLOCK_CAPLOG_AT_INFO
@pytest.fixture
def caplog_at_info(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """caplog wrapper pre-configured at INFO level so MP-* markers are captured."""
    caplog.set_level(logging.INFO)
    return caplog
# END_BLOCK_CAPLOG_AT_INFO


# START_BLOCK_MARKER_COUNTER
@pytest.fixture
def marker_counter():
    """Returns a callable: count(caplog) -> Counter[BLOCK_NAME -> count].

    Encapsulates the caplog -> marker-name extraction so tests assert
    against a Counter rather than parsing log strings inline.
    """
    from tests.unit._mp_helpers import extract_marker

    def _count(caplog: pytest.LogCaptureFixture) -> Counter[str]:
        return Counter(
            m for m in (extract_marker(r.getMessage()) for r in caplog.records) if m
        )

    return _count
# END_BLOCK_MARKER_COUNTER


# START_BLOCK_GOLDEN_DOC_BUILDER
@pytest.fixture
def golden_doc_builder():
    """Returns _mp_helpers.build_golden_document — VF-013 single source of truth."""
    from tests.unit._mp_helpers import build_golden_document

    return build_golden_document
# END_BLOCK_GOLDEN_DOC_BUILDER


# START_BLOCK_SCHEMA_VIOLATION_FACTORY
@pytest.fixture
def schema_violation_factory():
    """Returns a callable producing minimally-broken preset dicts for V-MP-STYLE scenario-6."""
    base = {
        "$schema": "https://mint.dev/schema/style-preset-1.0.json",
        "name": "broken_test",
        "version": "1.0",
        "description": "intentionally broken for schema-violation tests",
        "color_palette": {
            "primary": "#0F4C81",
            "secondary": "#5B8DBE",
            "accent": "#FFB400",
            "text": "#1A1A1A",
            "text_muted": "#6E6E6E",
            "background": "#FFFFFF",
            "border": "#D4D4D4",
        },
        "typography": {
            "heading1": {"font": "Inter", "size_pt": 24, "color": "#0F4C81"},
            "heading2": {"font": "Inter", "size_pt": 18, "color": "#0F4C81"},
            "heading3": {"font": "Inter", "size_pt": 14, "color": "#0F4C81"},
            "body": {"font": "Inter", "size_pt": 11, "color": "#1A1A1A"},
            "table_header": {"font": "Inter", "size_pt": 11, "color": "#FFFFFF", "bold": True},
            "caption": {"font": "Inter", "size_pt": 9, "color": "#6E6E6E", "italic": True},
        },
        "spacing": {
            "paragraph_default_before_pt": 0,
            "paragraph_default_after_pt": 6,
            "default_line_height": 1.15,
            "table_cell_padding_pt": 4,
        },
    }

    def _factory(violation_kind: str) -> dict:
        import copy

        d = copy.deepcopy(base)
        if violation_kind == "missing-color-palette-primary":
            del d["color_palette"]["primary"]
        elif violation_kind == "bad-hex-format":
            d["color_palette"]["primary"] = "rgb(15, 76, 129)"
        elif violation_kind == "size-pt-as-string":
            d["typography"]["heading1"]["size_pt"] = "24"
        elif violation_kind == "alignment-out-of-enum":
            d["typography"]["body"]["alignment"] = "diagonal"
        elif violation_kind == "dangling-palette-token":
            d["typography"]["heading1"]["color"] = "@nonexistent"
        elif violation_kind == "version-mismatch-major":
            d["version"] = "2.0"
        else:
            raise ValueError(f"unknown violation_kind: {violation_kind}")
        return d

    return _factory
# END_BLOCK_SCHEMA_VIOLATION_FACTORY


# START_BLOCK_MPL_FIGURE_CLEANUP
@pytest.fixture(autouse=True)
def mpl_figure_cleanup():
    """Force-close all matplotlib Figures after each test (Phase-8).

    matplotlib retains Figure refs in pyplot's internal registry until
    explicit close — accumulation under long pytest runs grows memory.
    Lazy-import: tests that don't touch matplotlib pay zero cost.
    """
    yield
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    plt.close("all")
# END_BLOCK_MPL_FIGURE_CLEANUP


# START_BLOCK_MPL_RCPARAMS_SNAPSHOT
@pytest.fixture
def mpl_rcparams_snapshot():
    """Snapshot rcParams at entry; STRICT assert no leak at teardown (Phase-8).

    Used by V-MP-CHART theming-assertion tests to verify corporate rcParams
    modifications happen WITHIN a chart construction without polluting global
    state. V-MP-CHART forbidden-5 invariant.
    """
    try:
        import matplotlib
    except ImportError:
        pytest.skip("matplotlib not installed")
    snapshot = dict(matplotlib.rcParams)
    yield
    leaked = {k: v for k, v in matplotlib.rcParams.items() if snapshot.get(k) != v}
    if leaked:
        # Restore so subsequent tests aren't affected, then fail.
        matplotlib.rcParams.update(snapshot)
        raise AssertionError(
            f"rcParams leak detected (V-MP-CHART forbidden-5): {sorted(leaked)}"
        )
# END_BLOCK_MPL_RCPARAMS_SNAPSHOT


# START_BLOCK_CHART_BASELINE_PATH
@pytest.fixture(scope="session")
def chart_baseline_path() -> Path:
    """Path to tests/fixtures/mp_chart_e2e_baseline.json (Phase-8).

    READ-ONLY: tests must NOT write to baselines unless
    MP_CHART_E2E_WRITE_BASELINE=1 is set (gated by mp_clean_env autouse).
    Symmetric to load_audit_baseline path resolution in _mp_helpers.
    """
    return Path(__file__).resolve().parent.parent / "fixtures" / "mp_chart_e2e_baseline.json"
# END_BLOCK_CHART_BASELINE_PATH


# START_BLOCK_CLEAN_WRITERS_CONFIG
@pytest.fixture
def clean_writers_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase-15 / V-MP-AUTH-SHIM: clear the MP-AUTH-SHIM WritersConfig
    process-cache + scrub MINT_TEMPLATE_WRITERS env between tests.

    The shim caches load_writers_config() to satisfy VF-017 inv-2
    OPEN-MODE-WARNS-ONCE + inv-4 CACHE-INVARIANT. Tests that exercise
    different env or file states need a fresh cache. Lazy-imports
    mint_python.mcp.auth so the fixture is usable before Wave-15-1 lands.
    """
    monkeypatch.delenv("MINT_TEMPLATE_WRITERS", raising=False)
    auth_module = sys.modules.get("mint_python.mcp.auth")
    if auth_module is not None:
        if hasattr(auth_module, "_reset_for_tests"):
            auth_module._reset_for_tests()
        elif hasattr(auth_module, "load_writers_config") and hasattr(
            auth_module.load_writers_config, "cache_clear"
        ):
            auth_module.load_writers_config.cache_clear()
    yield
    auth_module = sys.modules.get("mint_python.mcp.auth")
    if auth_module is not None:
        if hasattr(auth_module, "_reset_for_tests"):
            auth_module._reset_for_tests()
        elif hasattr(auth_module, "load_writers_config") and hasattr(
            auth_module.load_writers_config, "cache_clear"
        ):
            auth_module.load_writers_config.cache_clear()
# END_BLOCK_CLEAN_WRITERS_CONFIG


# START_BLOCK_ZIP_BYTE_SNAPSHOT
@pytest.fixture
def zip_byte_snapshot():
    """Phase-15 / VF-018 inv-2 READ-ONLY + VF-019 inv-5 NO-DOC-MUTATION:
    return a callable that snapshots a file's sha256 and asserts equality
    on a second call. Use as `snap = zip_byte_snapshot(path); ...; snap()`.

    The returned callable raises AssertionError if the file's bytes
    changed between the snapshot point and the assertion point.
    """
    import hashlib

    def _snapshot(path: Path) -> callable:
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()

        def _assert_unchanged() -> None:
            after = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            assert before == after, (
                f"File bytes changed during read-only operation: {path} "
                f"({before[:12]}... -> {after[:12]}...)"
            )

        return _assert_unchanged

    return _snapshot
# END_BLOCK_ZIP_BYTE_SNAPSHOT


# START_BLOCK_TEMPDIR_SNAPSHOT
@pytest.fixture
def tempdir_snapshot():
    """Phase-15 / VF-019 inv-6 TEMP-FILE-CLEANUP: snapshot tempdir entries
    matching a glob, return a callable that asserts no leftover entries.

    Use as `snap = tempdir_snapshot('mint_qa_*'); run_hook(); snap()`.
    The fixture pins to tempfile.gettempdir() and matches via glob; this
    catches PDF/PNG temp leaks from MP-VISUAL-QA-HOOK without requiring
    the hook to expose its tempfile names. Mirrors VF-016 inv-1
    TEMP-FILE-CLEANUP pattern.
    """
    import tempfile
    from pathlib import Path as _P

    def _snapshot(glob_pattern: str = "mint_qa_*") -> callable:
        tmpdir = _P(tempfile.gettempdir())
        before = set(tmpdir.glob(glob_pattern))

        def _assert_no_leak() -> None:
            after = set(tmpdir.glob(glob_pattern))
            leaked = after - before
            assert not leaked, (
                f"Tempfile leak detected (glob={glob_pattern!r}): "
                f"{sorted(p.name for p in leaked)}"
            )

        return _assert_no_leak

    return _snapshot
# END_BLOCK_TEMPDIR_SNAPSHOT


# START_BLOCK_BACKEND_PROBE_PATCHER
@pytest.fixture
def backend_probe_patcher(monkeypatch: pytest.MonkeyPatch):
    """Phase-15 / VF-019 inv-4 BACKEND-DEGRADATION: monkeypatch shutil.which
    to simulate missing soffice / pdftoppm + monkeypatch urllib.request to
    simulate Ollama unreachable. Returns a configurator callable.

    Usage:
        backend_probe_patcher(missing={"soffice"})  # only soffice missing
        backend_probe_patcher(missing={"pdftoppm"}, ollama_unreachable=True)
        backend_probe_patcher(missing=set())        # all backends present

    Default (no call): all backends present.
    """
    import shutil
    import urllib.request

    real_which = shutil.which
    real_urlopen = urllib.request.urlopen
    state: dict = {"missing": set(), "ollama_unreachable": False}

    _known_backends = {"soffice", "pdftoppm"}

    def _fake_which(cmd: str, *args, **kwargs):
        if cmd in state["missing"]:
            return None
        if cmd in _known_backends:
            return f"/usr/bin/{cmd}"
        return real_which(cmd, *args, **kwargs)

    def _fake_urlopen(*args, **kwargs):
        if state["ollama_unreachable"]:
            raise ConnectionError("Simulated: Ollama endpoint unreachable")
        return real_urlopen(*args, **kwargs)

    monkeypatch.setattr(shutil, "which", _fake_which)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    def _configure(*, missing: set[str] | None = None,
                   ollama_unreachable: bool = False) -> None:
        state["missing"] = set(missing or ())
        state["ollama_unreachable"] = ollama_unreachable

    return _configure
# END_BLOCK_BACKEND_PROBE_PATCHER
