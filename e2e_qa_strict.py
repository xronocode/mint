"""Strict OOXML structural validator for MINT outputs.

Catches what mint validate doesn't: orphan rIds, missing parts,
malformed Content-Types overrides, broken TOC field codes, dangling
header/footer references. Output is structured JSON.

Usage:
    python e2e_qa_strict.py path/to/doc.docx
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _parse(zf: zipfile.ZipFile, name: str) -> etree._Element | None:
    if name not in zf.namelist():
        return None
    return etree.fromstring(zf.read(name))


def _rels_for(part: str) -> str:
    """Return the .rels path that pairs with `part` (e.g. word/document.xml ->
    word/_rels/document.xml.rels)."""
    p = Path(part)
    return f"{p.parent.as_posix()}/_rels/{p.name}.rels"


def collect_rels(zf: zipfile.ZipFile, part: str) -> dict[str, dict[str, str]]:
    """Return {rid: {Target, Type, TargetMode}} for the given part's rels."""
    out: dict[str, dict[str, str]] = {}
    rels_path = _rels_for(part)
    rel_root = _parse(zf, rels_path)
    if rel_root is None:
        return out
    for r in rel_root:
        if r.tag != f"{{{NS['rel']}}}Relationship":
            continue
        out[r.get("Id", "")] = {
            "Target": r.get("Target", ""),
            "Type": r.get("Type", ""),
            "TargetMode": r.get("TargetMode", "Internal"),
        }
    return out


def resolve_target(part: str, target: str) -> str:
    """Resolve a relationship Target into the absolute archive path."""
    if target.startswith("/"):
        return target.lstrip("/")
    base = Path(part).parent
    return (base / target).as_posix()


def validate(path: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    info: dict[str, Any] = {}
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        return {
            "ok": False,
            "issues": [{"severity": "fatal", "code": "BAD_ZIP", "msg": str(exc)}],
            "info": {},
        }

    with zf:
        names = set(zf.namelist())
        info["parts_count"] = len(names)
        info["size_bytes"] = sum(zf.getinfo(n).file_size for n in names)

        # ---- Content Types
        ct = _parse(zf, "[Content_Types].xml")
        if ct is None:
            issues.append({"severity": "fatal", "code": "NO_CONTENT_TYPES",
                           "msg": "[Content_Types].xml missing"})
            return {"ok": False, "issues": issues, "info": info}
        overrides: dict[str, str] = {}
        defaults: dict[str, str] = {}
        for el in ct:
            local = etree.QName(el).localname
            if local == "Override":
                pn = el.get("PartName", "").lstrip("/")
                ctype = el.get("ContentType", "")
                overrides[pn] = ctype
            elif local == "Default":
                ext = el.get("Extension", "")
                ctype = el.get("ContentType", "")
                defaults[ext] = ctype
        info["overrides"] = len(overrides)
        info["default_types"] = len(defaults)

        # Every XML part should have either an Override or its extension covered
        # by a Default (with .xml usually mapping to the workbook xml type — but
        # for docx, .xml parts under word/ etc. need explicit Override).
        for n in sorted(names):
            if n.endswith("/") or n.startswith("_rels/") or n == "[Content_Types].xml":
                continue
            if n.endswith(".rels"):
                # Covered by Default Extension="rels"
                if "rels" not in defaults:
                    issues.append({"severity": "high", "code": "NO_RELS_DEFAULT",
                                   "msg": f"Default for .rels missing in [Content_Types].xml"})
                continue
            if n.endswith(".xml"):
                if n not in overrides:
                    # Some packages legitimately rely on a Default for .xml,
                    # but DOCX standard requires Override per part.
                    issues.append({"severity": "high", "code": "MISSING_OVERRIDE",
                                   "msg": f"No Override for {n}",
                                   "part": n})
            # Binary parts (images, fonts) should have a matching Default by ext
            ext = n.rsplit(".", 1)[-1] if "." in n else ""
            if ext and ext != "xml" and ext != "rels":
                if ext not in defaults:
                    issues.append({"severity": "medium", "code": "NO_DEFAULT_EXT",
                                   "msg": f"No Default ContentType for .{ext}",
                                   "part": n})

        # ---- Cross-check rels: every Target must resolve, every rId must be unique
        all_parts_with_rels: list[str] = ["word/document.xml"]
        # Add headers/footers/footnotes/endnotes that exist
        for n in names:
            if (n.startswith("word/header") and n.endswith(".xml")) or \
               (n.startswith("word/footer") and n.endswith(".xml")) or \
               n in ("word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"):
                all_parts_with_rels.append(n)

        rels_by_part: dict[str, dict[str, dict[str, str]]] = {}
        for part in all_parts_with_rels:
            if part in names:
                rels = collect_rels(zf, part)
                rels_by_part[part] = rels
                # Every internal Target must exist in the archive
                for rid, meta in rels.items():
                    if meta["TargetMode"] == "External":
                        continue
                    target_path = resolve_target(part, meta["Target"])
                    if target_path not in names:
                        issues.append({
                            "severity": "high",
                            "code": "ORPHAN_REL_TARGET",
                            "msg": f"{part} rId={rid} -> {meta['Target']} (resolved to {target_path}) — not in archive",
                            "part": part,
                            "rid": rid,
                        })

        # ---- Cross-check r:id references inside content
        # collect r:id usages from document.xml (and headers/footers)
        for part in all_parts_with_rels:
            if part not in names:
                continue
            root = _parse(zf, part)
            if root is None:
                continue
            rels = rels_by_part.get(part, {})
            # Find all elements with r:id attribute
            for el in root.iter():
                rid = el.get(f"{{{NS['r']}}}id")
                if rid and rid not in rels:
                    issues.append({
                        "severity": "high",
                        "code": "UNRESOLVED_RID",
                        "msg": f"{part}: element <{etree.QName(el).localname}> r:id={rid} has no Relationship",
                        "part": part,
                        "rid": rid,
                    })

        # ---- TOC field-code well-formedness
        doc_root = _parse(zf, "word/document.xml")
        if doc_root is not None:
            # Find <w:fldChar w:fldCharType="begin"> ... <w:fldChar fldCharType="end"> pairs
            fldChars = doc_root.findall(f".//{{{NS['w']}}}fldChar")
            stack = 0
            for fc in fldChars:
                t = fc.get(f"{{{NS['w']}}}fldCharType", "")
                if t == "begin":
                    stack += 1
                elif t == "end":
                    stack -= 1
                if stack < 0:
                    issues.append({"severity": "high", "code": "FLDCHAR_UNBALANCED",
                                   "msg": "Field code end without begin in document.xml"})
                    break
            if stack != 0:
                issues.append({"severity": "high", "code": "FLDCHAR_UNCLOSED",
                               "msg": f"{stack} unclosed field begins in document.xml"})

            # Settings.xml: w:updateFields recommended for TOC to refresh on open
            settings = _parse(zf, "word/settings.xml")
            has_toc = bool(re.search(r"\bTOC\b", etree.tostring(doc_root).decode("utf-8", errors="ignore")))
            if has_toc:
                if settings is None:
                    issues.append({"severity": "medium", "code": "TOC_NO_SETTINGS",
                                   "msg": "TOC present but settings.xml missing"})
                else:
                    upd = settings.find(f"{{{NS['w']}}}updateFields")
                    if upd is None:
                        issues.append({"severity": "low", "code": "TOC_NO_UPDATEFIELDS",
                                       "msg": "TOC present but settings has no <w:updateFields>; users will see empty TOC until manual refresh"})

        # ---- Header/footer references in document.xml exist
        if doc_root is not None:
            doc_rels = rels_by_part.get("word/document.xml", {})
            for ref_tag in ("headerReference", "footerReference"):
                for el in doc_root.iter(f"{{{NS['w']}}}{ref_tag}"):
                    rid = el.get(f"{{{NS['r']}}}id")
                    if rid and rid in doc_rels:
                        target_path = resolve_target("word/document.xml", doc_rels[rid]["Target"])
                        if target_path not in names:
                            issues.append({
                                "severity": "high",
                                "code": "HEADERFOOTER_TARGET_MISSING",
                                "msg": f"{ref_tag} rId={rid} -> {target_path} (not in archive)",
                            })

        # ---- Mandatory parts present
        for required in ("word/document.xml", "[Content_Types].xml"):
            if required not in names:
                issues.append({
                    "severity": "fatal",
                    "code": "MISSING_REQUIRED_PART",
                    "msg": f"Required part missing: {required}",
                })

        # Recommended (warn if absent)
        for recommended in ("word/styles.xml", "word/settings.xml", "docProps/core.xml"):
            if recommended not in names:
                issues.append({
                    "severity": "low",
                    "code": "MISSING_RECOMMENDED_PART",
                    "msg": f"Recommended part missing: {recommended}",
                })

    fatal = sum(1 for i in issues if i["severity"] == "fatal")
    high = sum(1 for i in issues if i["severity"] == "high")
    medium = sum(1 for i in issues if i["severity"] == "medium")
    low = sum(1 for i in issues if i["severity"] == "low")

    return {
        "ok": fatal == 0 and high == 0,
        "path": path,
        "info": info,
        "counts": {"fatal": fatal, "high": high, "medium": medium, "low": low,
                   "total": len(issues)},
        "issues": issues,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: e2e_qa_strict.py <docx-path>", file=sys.stderr)
        sys.exit(2)
    result = validate(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)
