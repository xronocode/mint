# FILE: src/mint_python/core/presets/__init__.py
# VERSION: 0.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Built-in style preset JSON registry for the Pure Python Edition.
#     Houses the three Phase-7 presets (alga_corporate, minimal, compact)
#     consumed by mint_python.core.style.load_preset() via name lookup.
#   SCOPE: Empty package marker. The JSON files in this directory ARE the
#     registry; this module exposes no Python surface — `style.BUILTIN_PRESETS`
#     is the canonical resolver mapping name -> Path.
#   DEPENDS: none (pure data directory)
#   LINKS: docs/style-preset-schema.md (normative schema),
#     docs/knowledge-graph.xml#MP-STYLE,
#     docs/development-plan.xml#MP-STYLE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-1 (MP-STYLE): initial provisioning of built-in preset
#     directory; ships alga_corporate.json, minimal.json, compact.json.
# END_CHANGE_SUMMARY

from __future__ import annotations
