# FILE: src/mint_python/_hash.py
# Internal: SHA-256 file hashing utility shared by MP-FIX and MP-GRACE.
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
