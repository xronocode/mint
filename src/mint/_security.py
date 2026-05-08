from __future__ import annotations

import zipfile
from pathlib import Path


class PathTraversalError(ValueError):
    """Raised when a path escapes the allowed base directory."""


def resolve_safe_path(raw: str | Path, base: Path) -> Path:
    """Resolve *raw* and verify it stays inside *base*.

    Prevents ``../../etc/passwd`` style traversal in MCP tool arguments.
    ``base`` itself is ``resolve()``-d so the comparison is unambiguous.
    """
    resolved = Path(raw).resolve()
    base_resolved = base.resolve()
    if not (resolved == base_resolved or str(resolved).startswith(str(base_resolved) + "/")):
        raise PathTraversalError(
            f"Path '{raw}' resolves to '{resolved}' which is outside '{base_resolved}'"
        )
    return resolved


def validate_zip_paths(zf: zipfile.ZipFile) -> None:
    """Raise ``PathTraversalError`` if any entry in *zf* escapes its parent directory."""
    for name in zf.namelist():
        if name.startswith("/") or ".." in Path(name).parts:
            raise PathTraversalError(
                f"ZIP entry '{name}' escapes the target directory (zip slip)"
            )
