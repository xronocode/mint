# FILE: src/mint_python/mcp/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: FastMCP tool surface for the post-alpha product shape — tools
#     that drive planning-mode dialogs via ctx.elicit, consume design tokens
#     from MP-STYLE presets, and inject GRACE audit-trail metadata on every
#     produced document. Phase-13 ships MP-MEMO-POC as the canonical
#     example; Phase-14 adds Letter / Report / Contract following the same
#     shape.
#   SCOPE: Empty package marker. Each tool / doc-type ships as its own
#     module (memo.py, future letter.py, future report.py).
#   DEPENDS: fastmcp (Context.elicit), mint_python.core (Document/Section/
#     Table builders, MP-STYLE preset application), mint_python.adapters
#     .markdown (markdown_to_spec for source_md fact extraction),
#     mint_python.grace (audit-trail injection).
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   memo - MP-MEMO-POC: create_memo tool with elicitation dialog
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — Phase-13 step-3 reactivates this package with
#     the MEMO-POC tool + planning-dialog pattern via ctx.elicit.
#   PRIOR: v0.0.0 — Phase-6 empty skeleton.
# END_CHANGE_SUMMARY

from __future__ import annotations
