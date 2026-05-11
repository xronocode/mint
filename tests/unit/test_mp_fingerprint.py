# FILE: tests/unit/test_mp_fingerprint.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Verify MP-FINGERPRINT pure-python port per V-MP-FINGERPRINT
#     scenarios 1-7. Asserts public API parity with legacy mint.fingerprint,
#     SHA-256 stability + sensitivity, exception hierarchy, DriftStatus
#     mapping, the BLOCK_FP_COMPUTE log marker, and a constraint-8 grep gate.
#   SCOPE: 7 deterministic scenarios + forbidden-behavior assertions over
#     shared fixtures from tests._helpers.sample_docs.
#   DEPENDS: mint_python.fingerprint, mint.fingerprint (legacy, for parity
#     scenario-5 ONLY — read-only side-by-side comparison; not for runtime),
#     tests._helpers.sample_docs, pytest, caplog_at_info, marker_counter.
#   LINKS: docs/verification-plan.xml#V-MP-FINGERPRINT
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TestScenario1BasicFingerprint - V-MP-FINGERPRINT scenario-1
#   TestScenario2HashDeterministicAndSensitive - scenario-2
#   TestScenario3MissingStylesXml - scenario-3
#   TestScenario4CompareOutcomes - scenario-4
#   TestScenario5LegacyCompatibility - scenario-5 (PORTING-PARITY)
#   TestScenario6LogMarker - scenario-6
#   TestScenario7NoLegacyImport - scenario-7 (grep gate)
#   TestForbiddenBehaviors - forbidden-1 / forbidden-2 / forbidden-3
#   TestEdgeCoverage - non-scenario branches needed for 100% coverage
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-16-1 initial implementation — 7 scenario tests covering
#     the full V-MP-FINGERPRINT plan plus edge coverage for unsupported
#     suffix, missing path, pptx flag, BadZipFile path, _hash_member OSError.
# END_CHANGE_SUMMARY

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from mint_python.fingerprint import (
    DOCX_FALLBACK_FILES,
    DOCX_STYLE_FILES,
    PPTX_FALLBACK_FILES,
    PPTX_STYLE_FILES,
    DriftStatus,
    FingerprintError,
    FingerprintResult,
    HashFailedError,
    MissingStyleXmlError,
    _hash_member,
    compare,
    compute,
    fingerprint,
)
from tests._helpers.sample_docs import (
    minimal_docx_bytes,
    no_styles_xml_docx_bytes,
    not_a_zip_bytes,
    valid_memo_docx_bytes,
    write_to_tmp,
)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _swap_styles_xml(src: bytes, new_styles: bytes) -> bytes:
    """Rebuild a docx zip replacing word/styles.xml with new_styles."""
    buf = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(src), "r") as src_zf,
        zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst_zf,
    ):
        for info in src_zf.infolist():
            if info.filename == "word/styles.xml":
                dst_zf.writestr(info.filename, new_styles)
            else:
                dst_zf.writestr(info, src_zf.read(info.filename))
    return buf.getvalue()


class TestScenario1BasicFingerprint:
    """scenario-1: fingerprint(memo_docx) returns expected shape."""

    def test_scenario_1_basic_fingerprint(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
        result = fingerprint(doc)
        assert isinstance(result, FingerprintResult)
        assert _HEX64_RE.match(result.hash), f"hash not 64-hex: {result.hash!r}"
        assert result.format == "docx"
        assert result.has_styles_xml is True
        assert result.byte_count > 0

    def test_fingerprint_accepts_str_path(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
        result = fingerprint(str(doc))
        assert _HEX64_RE.match(result.hash)


class TestScenario2HashDeterministicAndSensitive:
    """scenario-2: identical bytes -> equal hash; different styles.xml -> different hash."""

    def test_identical_bytes_produce_equal_hashes(self, tmp_path: Path) -> None:
        a = write_to_tmp(tmp_path, "a.docx", valid_memo_docx_bytes())
        b = write_to_tmp(tmp_path, "b.docx", valid_memo_docx_bytes())
        assert fingerprint(a).hash == fingerprint(b).hash

    def test_different_styles_xml_produce_different_hashes(
        self, tmp_path: Path
    ) -> None:
        base = valid_memo_docx_bytes()
        with zipfile.ZipFile(io.BytesIO(base)) as zf:
            original_styles = zf.read("word/styles.xml")
        mutated = _swap_styles_xml(base, original_styles + b"<!--drift-->")
        original_doc = write_to_tmp(tmp_path, "orig.docx", base)
        mutated_doc = write_to_tmp(tmp_path, "mutated.docx", mutated)
        assert fingerprint(original_doc).hash != fingerprint(mutated_doc).hash


class TestScenario3MissingStylesXml:
    """scenario-3: docx without word/styles.xml -> MissingStyleXmlError."""

    def test_no_styles_xml_raises(self, tmp_path: Path) -> None:
        # The no_styles_xml fixture only strips word/styles.xml; the legacy
        # algorithm falls back to numbering.xml (still present) so we must
        # ALSO strip numbering.xml and the document.xml fallback to trigger
        # the missing-style path.
        src = no_styles_xml_docx_bytes()
        buf = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(src), "r") as src_zf,
            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst_zf,
        ):
            for info in src_zf.infolist():
                if info.filename in (
                    "word/numbering.xml",
                    "word/document.xml",
                ):
                    continue
                dst_zf.writestr(info, src_zf.read(info.filename))
        doc = write_to_tmp(tmp_path, "no_styles.docx", buf.getvalue())
        with pytest.raises(MissingStyleXmlError):
            fingerprint(doc)


class TestScenario4CompareOutcomes:
    """scenario-4: compare() MATCH / DRIFT / UNKNOWN."""

    def test_match(self) -> None:
        assert compare("abc", "abc") == DriftStatus.MATCH

    def test_drift(self) -> None:
        assert compare("abc", "xyz") == DriftStatus.DRIFT

    def test_unknown_when_first_none(self) -> None:
        assert compare(None, "abc") == DriftStatus.UNKNOWN

    def test_unknown_when_second_none(self) -> None:
        assert compare("abc", None) == DriftStatus.UNKNOWN

    def test_unknown_when_both_none(self) -> None:
        assert compare(None, None) == DriftStatus.UNKNOWN


class TestScenario5LegacyCompatibility:
    """scenario-5: hash byte-identical to legacy mint.fingerprint on same fixture.

    PORTING-PARITY CHECK — the legacy module stays in place; we import it
    side-by-side ONLY in this test. forbidden-3 oracle.
    """

    def test_porting_parity_on_memo_fixture(self, tmp_path: Path) -> None:
        import mint.fingerprint as legacy

        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
        new_hash = fingerprint(doc).hash
        legacy_hash = legacy.fingerprint(doc).hash
        assert new_hash == legacy_hash, (
            f"porting-parity divergence: new={new_hash} legacy={legacy_hash}"
        )

    def test_porting_parity_on_minimal_docx(self, tmp_path: Path) -> None:
        import mint.fingerprint as legacy

        doc = write_to_tmp(tmp_path, "minimal.docx", minimal_docx_bytes())
        assert fingerprint(doc).hash == legacy.fingerprint(doc).hash


class TestScenario6LogMarker:
    """scenario-6: [MP-Fingerprint][compute][BLOCK_FP_COMPUTE] payload."""

    def test_log_marker_emitted(
        self,
        tmp_path: Path,
        caplog_at_info: pytest.LogCaptureFixture,
        marker_counter,
    ) -> None:
        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
        fingerprint(doc)
        counts: Counter[str] = marker_counter(caplog_at_info)
        assert counts["BLOCK_FP_COMPUTE"] >= 1

    def test_log_marker_payload_format(
        self,
        tmp_path: Path,
        caplog_at_info: pytest.LogCaptureFixture,
    ) -> None:
        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
        fingerprint(doc)
        msgs = [r.getMessage() for r in caplog_at_info.records]
        block = [m for m in msgs if "BLOCK_FP_COMPUTE" in m]
        assert len(block) >= 1
        msg = block[0]
        assert "[MP-Fingerprint][compute][BLOCK_FP_COMPUTE]" in msg
        assert "format=docx" in msg
        assert "byte_count=" in msg
        assert "chunks_read=" in msg


class TestScenario7NoLegacyImport:
    """scenario-7: constraint-8 grep gate over the new module."""

    def test_no_legacy_mint_imports(self) -> None:
        module_path = (
            Path(__file__).resolve().parent.parent.parent
            / "src"
            / "mint_python"
            / "fingerprint.py"
        )
        text = module_path.read_text(encoding="utf-8")
        # Ignore comments — only check effective code lines.
        offending: list[str] = []
        bad_patterns = (
            re.compile(r"\bfrom\s+mint\."),
            re.compile(r"\bimport\s+mint\."),
            re.compile(r"^\s*import\s+mint\b"),
        )
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if any(p.search(stripped) for p in bad_patterns):
                offending.append(line)
        assert offending == [], f"constraint-8 violations: {offending}"


class TestForbiddenBehaviors:
    """forbidden-1 / forbidden-2 / forbidden-3 assertions."""

    def test_forbidden_2_does_not_mutate_source(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
        before = doc.read_bytes()
        fingerprint(doc)
        after = doc.read_bytes()
        assert before == after, "fingerprint() mutated the source docx"

    def test_forbidden_3_parity_with_legacy(self, tmp_path: Path) -> None:
        # Belt-and-braces over scenario-5: parity over BOTH shared fixtures.
        import mint.fingerprint as legacy

        for name, payload in (
            ("memo.docx", valid_memo_docx_bytes()),
            ("minimal.docx", minimal_docx_bytes()),
        ):
            doc = write_to_tmp(tmp_path, name, payload)
            assert fingerprint(doc).hash == legacy.fingerprint(doc).hash


class TestEdgeCoverage:
    """Branches outside the 7 scenarios needed to hit 100% line coverage."""

    def test_compute_missing_path_raises_fingerprint_error(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "nope.docx"
        with pytest.raises(FingerprintError) as exc_info:
            compute(missing)
        assert "not found" in str(exc_info.value).lower()

    def test_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        bad = write_to_tmp(tmp_path, "weird.odt", b"irrelevant")
        with pytest.raises(FingerprintError) as exc_info:
            compute(bad)
        assert "unsupported format" in str(exc_info.value).lower()

    def test_not_a_zip_raises_fingerprint_error(self, tmp_path: Path) -> None:
        doc = write_to_tmp(tmp_path, "junk.docx", not_a_zip_bytes())
        with pytest.raises(FingerprintError) as exc_info:
            compute(doc)
        assert "zip" in str(exc_info.value).lower()
        # Must NOT be the MissingStyleXmlError subclass — it's the BadZipFile branch.
        assert not isinstance(exc_info.value, MissingStyleXmlError)
        assert not isinstance(exc_info.value, HashFailedError)

    def test_fallback_path_when_only_document_xml_present(
        self, tmp_path: Path
    ) -> None:
        # Strip styles.xml AND numbering.xml so only document.xml remains
        # (the fallback). has_styles_xml must come back False.
        src = valid_memo_docx_bytes()
        buf = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(src), "r") as src_zf,
            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst_zf,
        ):
            for info in src_zf.infolist():
                if info.filename in ("word/styles.xml", "word/numbering.xml"):
                    continue
                dst_zf.writestr(info, src_zf.read(info.filename))
        doc = write_to_tmp(tmp_path, "fallback.docx", buf.getvalue())
        result = fingerprint(doc)
        assert result.format == "docx"
        assert result.has_styles_xml is False
        assert result.byte_count > 0

    def test_pptx_path(self, tmp_path: Path) -> None:
        # Hand-roll a minimal pptx-shaped zip with ppt/theme/theme1.xml.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ppt/theme/theme1.xml", b"<theme>x</theme>")
        doc = write_to_tmp(tmp_path, "deck.pptx", buf.getvalue())
        result = fingerprint(doc)
        assert result.format == "pptx"
        assert result.has_styles_xml is True
        assert result.byte_count == len(b"<theme>x</theme>")

    def test_pptx_fallback_when_only_presentation_xml(
        self, tmp_path: Path
    ) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ppt/presentation.xml", b"<p/>")
        doc = write_to_tmp(tmp_path, "deck.pptx", buf.getvalue())
        result = fingerprint(doc)
        assert result.format == "pptx"
        assert result.has_styles_xml is False
        assert result.byte_count == len(b"<p/>")

    def test_pptx_empty_zip_raises_missing(self, tmp_path: Path) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("unrelated.xml", b"<x/>")
        doc = write_to_tmp(tmp_path, "empty.pptx", buf.getvalue())
        with pytest.raises(MissingStyleXmlError):
            fingerprint(doc)

    def test_hash_member_oserror_becomes_hash_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())

        real_open = zipfile.ZipFile.open

        def boom(self, name, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "word/styles.xml":
                raise OSError("simulated disk failure")
            return real_open(self, name, mode, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "open", boom)
        with pytest.raises(HashFailedError) as exc_info:
            compute(doc)
        assert "simulated disk failure" in str(exc_info.value)

    def test_constants_are_tuples(self) -> None:
        assert isinstance(DOCX_STYLE_FILES, tuple)
        assert isinstance(DOCX_FALLBACK_FILES, tuple)
        assert isinstance(PPTX_STYLE_FILES, tuple)
        assert isinstance(PPTX_FALLBACK_FILES, tuple)

    def test_zipfile_open_oserror_becomes_hash_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # OSError raised by zipfile.ZipFile constructor (not a per-member
        # read failure) — exercises the outer except OSError -> HashFailedError
        # branch.
        doc = write_to_tmp(tmp_path, "memo.docx", valid_memo_docx_bytes())
        real_init = zipfile.ZipFile.__init__

        def boom(self, file, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("simulated permission denied")

        monkeypatch.setattr(zipfile.ZipFile, "__init__", boom)
        with pytest.raises(HashFailedError) as exc_info:
            compute(doc)
        assert "simulated permission denied" in str(exc_info.value)
        # Restore so subsequent fixtures in the test session aren't broken.
        monkeypatch.setattr(zipfile.ZipFile, "__init__", real_init)

    def test_hash_member_direct_chunks_accounting(
        self, tmp_path: Path
    ) -> None:
        import hashlib

        # Build a fixture and read styles.xml back to validate _hash_member
        # accounting matches a one-shot hash of the same bytes.
        src = valid_memo_docx_bytes()
        path = write_to_tmp(tmp_path, "memo.docx", src)
        with zipfile.ZipFile(path) as zf:
            styles_bytes = zf.read("word/styles.xml")
        reference = hashlib.sha256(styles_bytes).hexdigest()

        sha = hashlib.sha256()
        with zipfile.ZipFile(path) as zf:
            chunks, total = _hash_member(zf, "word/styles.xml", sha, 0, 0)
        assert chunks >= 1
        assert total == len(styles_bytes)
        assert sha.hexdigest() == reference
