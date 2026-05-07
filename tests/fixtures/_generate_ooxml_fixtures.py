"""Generate OOXML fixtures used by tests/unit/test_ooxml.py.

Produces:
  - with_quote_chars.docx        : DOCX with ASCII apostrophes and straight
                                   double quotes inside w:t for
                                   escape_smart_quotes round-trip tests.
  - with_durable_id_overflow.docx: DOCX containing one element with
                                   w:durableId=2147483646 (0x7FFFFFFE) for
                                   pack auto-repair test.
  - with_dangling_rel.docx       : DOCX whose word/_rels/document.xml.rels
                                   references Target="missing.xml" that does
                                   not exist in the package, used to test
                                   OOXML_RELATIONSHIP_BROKEN.

Run once (idempotent — overwrites existing output).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
SRC = FIXTURES_DIR / "minimal_valid.docx"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _document_xml(body_p: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}" xmlns:w14="{W14_NS}">'
        "<w:body>"
        f"{body_p}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
        "</w:body></w:document>"
    ).encode("utf-8")


def create_with_quote_chars() -> Path:
    out = FIXTURES_DIR / "with_quote_chars.docx"
    entries = _read_zip(SRC)
    body = (
        "<w:p>"
        '<w:r><w:t>It\'s a "quoted" word, isn\'t it?</w:t></w:r>'
        "</w:p>"
        "<w:p>"
        '<w:r><w:rPr><w:rStyle w:val="SourceCode"/></w:rPr>'
        '<w:t>code: don\'t escape "this"</w:t></w:r>'
        "</w:p>"
    )
    entries["word/document.xml"] = _document_xml(body)
    _write_zip(out, entries)
    return out


def create_with_durable_id_overflow() -> Path:
    out = FIXTURES_DIR / "with_durable_id_overflow.docx"
    entries = _read_zip(SRC)
    # 0x7FFFFFFE = 2147483646; auto-repair regenerates anything >= 0x7FFFFFFF
    # so we deliberately use a value at the boundary that should trip repair.
    body = (
        '<w:p w14:paraId="00000001" w:durableId="2147483646">'
        "<w:r><w:t>Has overflowing durable id</w:t></w:r>"
        "</w:p>"
    )
    entries["word/document.xml"] = _document_xml(body)
    _write_zip(out, entries)
    return out


def create_with_dangling_rel() -> Path:
    out = FIXTURES_DIR / "with_dangling_rel.docx"
    entries = _read_zip(SRC)
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{PR_NS}">'
        '<Relationship Id="rIdMissing01" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        'Target="media/missing.xml"/>'
        "</Relationships>"
    ).encode("utf-8")
    entries["word/_rels/document.xml.rels"] = rels_xml
    _write_zip(out, entries)
    return out


def main() -> None:
    p1 = create_with_quote_chars()
    p2 = create_with_durable_id_overflow()
    p3 = create_with_dangling_rel()
    print(p1)
    print(p2)
    print(p3)


if __name__ == "__main__":
    main()
