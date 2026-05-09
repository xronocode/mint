# FILE: src/mint_python/_hash.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Internal SHA-256 file hashing utility shared by MP-FIX and MP-GRACE.
#     Provides single 64 KB chunked hash function.
#   SCOPE: compute_file_hash(path) → SHA-256 hex digest.
#   DEPENDS: hashlib, pathlib (stdlib only)
#   LINKS: docs/knowledge-graph.xml#MP-HASH
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   compute_file_hash - Return SHA-256 hex digest of file at path
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Phase-11 post-review fix — extracted from duplicate
#     implementations in fix.py and grace/__init__.py. Unified chunk size
#     to 64 KB (65536 bytes).
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 65536  # 64K


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()
