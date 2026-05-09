# FILE: src/mint_python/sdk/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Public SDK surface for prompt-style document construction.
#     Re-exports the §3 type set so users write
#     `from mint_python.sdk import Document, Section, Table, Style, Image,
#                                  TOC, Pt, ColorPalette, presets`.
#   SCOPE: Phase-7 Wave-7-5 — public re-exports + named-preset registry alias
#     (`presets`) + Phase-7 marker class `TOC`. Forbidden surface beyond §3
#     (V-MP-SDK forbidden-1) and any legacy `mint.*` imports (forbidden-2).
#   DEPENDS: mint_python.core.{document, section, table, style, content}.
#     No imports from src/mint/* (legacy js-engine path).
#   LINKS: docs/knowledge-graph.xml#MP-SDK, docs/development-plan.xml#MP-SDK,
#     docs/verification-plan.xml#V-MP-SDK,
#     docs/mint-pure-python-handover-v1.md#section-3
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Document - re-export of mint_python.core.document.Document
#   Section - re-export of mint_python.core.section.Section
#   Table - re-export of mint_python.core.table.Table
#   Style - re-export of mint_python.core.style.Style
#   Image - re-export of mint_python.core.content.Image
#   Pt - re-export of mint_python.core.style.Pt
#   ColorPalette - re-export of mint_python.core.style.ColorPalette
#   TOC - Phase-7 marker class (Document.add_toc carries the only params)
#   presets - alias to mint_python.core.style.BUILTIN_PRESETS (read-only Mapping)
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Wave-7-5: populate public re-exports per handover §3
#     (Document/Section/Table/Style/Image/TOC/Pt/ColorPalette + presets alias).
#     Replaces Phase-6 empty placeholder.
# END_CHANGE_SUMMARY

from __future__ import annotations

from mint_python.core.content import Image
from mint_python.core.document import Document
from mint_python.core.section import Section
from mint_python.core.style import (
    BUILTIN_PRESETS as presets,  # noqa: N811 - public alias per handover §3
)
from mint_python.core.style import ColorPalette, Pt, Style
from mint_python.core.table import Table


class TOC:
    """Phase-7 marker class for table-of-contents anchors.

    The actual TOC field is emitted by ``Document.add_toc(max_level=...)``.
    This class exists per handover §3 export list; concrete config (depth
    aliases, title strings) lands in Phase-2+ if needed.
    """


__all__ = [
    "TOC",
    "ColorPalette",
    "Document",
    "Image",
    "Pt",
    "Section",
    "Style",
    "Table",
    "presets",
]
