# FILE: src/mint_python/adapters/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Input adapters that convert external content formats into the
#     ArticleSpec dataclass — the bridge between "what the user has on hand"
#     (markdown, chat transcripts, future docx/pdf) and "what the MP-DOCUMENT
#     builder consumes". Each adapter is a deterministic pure function; no
#     LLM in this layer.
#   SCOPE: Empty package marker. Each adapter ships as its own module
#     (markdown.py, future chat.py, future pdf.py, ...).
#   DEPENDS: tools.article_experiment.spec (ArticleSpec types — reused, not
#     duplicated)
# END_MODULE_CONTRACT

from __future__ import annotations
