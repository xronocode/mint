# FILE: tests/_helpers/ooxml_parity.py
"""ooxml_parity — porting-parity oracle for V-MP-OOXML scenario-7.

Side-by-side fixture-driven equivalence check between the pure-python port
(mint_python.ooxml) and the legacy implementation (mint.ooxml).  Encapsulates
the unpack -> manifest comparison so both V-MP-OOXML scenario-7 and the
forthcoming V-MP-EDIT parity tests can re-use the same oracle without
re-implementing the side-by-side scaffolding.

Constraint-8 note: this helper deliberately imports the legacy package because
it IS the oracle.  Production code under src/mint_python/ MUST NOT import from
src/mint/ — only tests/_helpers may straddle the two trees for parity proofs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParityRecord:
    """A single side-by-side parity datapoint.

    Holds the manifests + auto-repair counters from both implementations on
    the same fixture so the test can assert equality without re-walking the
    unpack tree.
    """

    legacy_parts: list[str]
    legacy_runs_merged: int
    legacy_quotes_escaped: int
    legacy_format: str
    legacy_repaired_durable_ids: int
    legacy_preserved_whitespace_runs: int

    port_parts: list[str]
    port_runs_merged: int
    port_quotes_escaped: int
    port_format: str
    port_repaired_durable_ids: int
    port_preserved_whitespace_runs: int


def collect_parity(doc_path: Path, work_dir: Path) -> ParityRecord:
    """Run unpack + pack with both implementations on doc_path.

    Each side uses an isolated subdirectory of work_dir so the trees do not
    collide. Returns a frozen ParityRecord the caller can diff field-by-field.
    """
    from mint import (
        ooxml as legacy_ooxml,
    )
    from mint_python import ooxml as port_ooxml

    legacy_unpack = work_dir / "legacy_unpack"
    legacy_pack = work_dir / "legacy_pack.docx"
    port_unpack = work_dir / "port_unpack"
    port_pack = work_dir / "port_pack.docx"

    legacy_u = legacy_ooxml.unpack(doc_path, legacy_unpack)
    legacy_p = legacy_ooxml.pack(legacy_unpack, legacy_pack)

    port_u = port_ooxml.unpack(doc_path, port_unpack)
    port_p = port_ooxml.pack(port_unpack, port_pack)

    return ParityRecord(
        legacy_parts=sorted(legacy_u.parts),
        legacy_runs_merged=legacy_u.runs_merged,
        legacy_quotes_escaped=legacy_u.quotes_escaped,
        legacy_format=legacy_u.format,
        legacy_repaired_durable_ids=legacy_p.repaired_durable_ids,
        legacy_preserved_whitespace_runs=legacy_p.preserved_whitespace_runs,
        port_parts=sorted(port_u.parts),
        port_runs_merged=port_u.runs_merged,
        port_quotes_escaped=port_u.quotes_escaped,
        port_format=port_u.format,
        port_repaired_durable_ids=port_p.repaired_durable_ids,
        port_preserved_whitespace_runs=port_p.preserved_whitespace_runs,
    )
