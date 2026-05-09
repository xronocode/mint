# FILE: tests/integration/conftest.py
# START_MODULE_CONTRACT
#   PURPOSE: Re-export the Phase-7 SwarmFixtures from tests/unit/conftest.py
#     so tests under tests/integration/ (e.g. VF-013 e2e) can consume the
#     same fixture set used by unit tests.
#   SCOPE: Thin fixture-import shim. NO fixtures are redefined here.
#     Avoids `pytest_plugins = [...]` because that approach collides when
#     both tests/unit/ and tests/integration/ are collected in the same run
#     (tests.unit.conftest gets registered both as auto-conftest AND as
#     plugin, raising "Plugin already registered under a different name").
#   DEPENDS: tests.unit.conftest
#   LINKS: docs/verification-plan.xml#SwarmFixtures
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   (re-exports) - mp_clean_env, tmp_docx_path, mp_minimal_config,
#     caplog_at_info, marker_counter, golden_doc_builder, mpl_figure_cleanup,
#     chart_baseline_path
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-8-2 - extend re-exports with mpl_figure_cleanup (autouse)
#     and chart_baseline_path (session-scoped) so VF-014 chart e2e under
#     tests/integration/ can consume the Phase-8 fixture additions.
#   PRIOR: Wave-7-5 - shim added so VF-013 e2e under tests/integration/
#     can reuse mp_clean_env / tmp_docx_path / caplog_at_info / marker_counter
#     / golden_doc_builder / mp_minimal_config fixtures.
# END_CHANGE_SUMMARY
from __future__ import annotations

# Importing fixture functions into the conftest namespace makes them
# available to tests under tests/integration/ without re-registering the
# tests.unit.conftest module as a plugin.
from tests.unit.conftest import (
    caplog_at_info,
    chart_baseline_path,
    golden_doc_builder,
    marker_counter,
    mp_clean_env,
    mp_minimal_config,
    mpl_figure_cleanup,
    tmp_docx_path,
)

__all__ = [
    "caplog_at_info",
    "chart_baseline_path",
    "golden_doc_builder",
    "marker_counter",
    "mp_clean_env",
    "mp_minimal_config",
    "mpl_figure_cleanup",
    "tmp_docx_path",
]
