# FILE: src/mint_python/__init__.py
# VERSION: 0.3.0
# START_MODULE_CONTRACT
#   PURPOSE: Top-level marker for the Pure Python Edition. As of v0.3.0 the
#     package ships 12 functional MP-* modules (PKG/STYLE/CONTENT/TABLE/
#     SECTION/DOCUMENT/CHART/SDK/RULES/VALIDATE/FIX/GRACE) covering the full
#     handover §3 SDK surface — ZERO active stubs.
#   SCOPE: Package marker + version constant. Public SDK surface lives in
#     mint_python.sdk (re-exports Document, Section, Table, Style, Image,
#     TOC, Pt, ColorPalette, Chart, presets per handover §3).
#   DEPENDS: none (the package itself); subpackages declare their own deps
#   LINKS: docs/knowledge-graph.xml#MP-PKG, docs/development-plan.xml#MP-PKG,
#     docs/mint-pure-python-handover-v1.md#section-5
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   __version__ - package version string (synchronized with pyproject.toml [project].version)
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.3.0 - Pure Python Edition complete: Phases 6/7/8/9/11/12
#                shipped (Phase-10 Execution tiers + RestrictedPython skipped
#                per roadmap pivot 2026-05-09). All 12 MP-* modules functional;
#                ZERO active stubs.
#   PRIOR:       v0.0.0 - Phase-6 (Phase 0 of pure-python rollout): empty skeleton.
# END_CHANGE_SUMMARY

from __future__ import annotations

__version__ = "0.3.0"
