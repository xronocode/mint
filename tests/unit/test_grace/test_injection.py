from pathlib import Path

import pytest

from mint.grace import (
    GRACEInjectionError,
    GRACEManifest,
    bootstrap,
    describe,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


class TestBootstrap:
    def test_bootstrap_creates_grace_docx(self, tmp_path: Path) -> None:
        import shutil

        src = FIXTURES / "minimal_valid.docx"
        dst = tmp_path / "test.docx"
        shutil.copy2(src, dst)

        manifest = bootstrap(dst)
        assert isinstance(manifest, GRACEManifest)
        assert manifest.namespace == "urn:mint:grace:2026:manifest"
        assert len(manifest.fingerprint) == 64
        assert len(manifest.instructions) >= 5

    def test_bootstrap_output_path(self, tmp_path: Path) -> None:
        import shutil

        src = FIXTURES / "minimal_valid.docx"
        dst = tmp_path / "input.docx"
        output = tmp_path / "output.docx"
        shutil.copy2(src, dst)

        bootstrap(dst, output_path=output)
        assert output.exists()

    def test_bootstrap_preserves_vendor_xml(self, tmp_path: Path) -> None:
        import shutil
        import zipfile

        src = FIXTURES / "with_vendor_xml.docx"
        dst = tmp_path / "test.docx"
        shutil.copy2(src, dst)

        with zipfile.ZipFile(dst, "r") as zf:
            vendor_before = zf.read("customXml/vendor_meta.xml")

        output = tmp_path / "output.docx"
        bootstrap(dst, output_path=output)

        with zipfile.ZipFile(output, "r") as zf:
            vendor_after = zf.read("customXml/vendor_meta.xml")

        assert vendor_before == vendor_after

    def test_bootstrap_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(GRACEInjectionError, match="not found"):
            bootstrap(tmp_path / "nonexistent.docx")


class TestDescribe:
    def test_describe_grace_docx(self) -> None:
        manifest = describe(FIXTURES / "with_grace.docx")
        assert manifest is not None
        assert isinstance(manifest, GRACEManifest)
        assert len(manifest.instructions) >= 5
        assert len(manifest.fingerprint) == 64

    def test_describe_no_grace_returns_none(self) -> None:
        manifest = describe(FIXTURES / "minimal_valid.docx")
        assert manifest is None

    def test_describe_vendor_xml_returns_none(self) -> None:
        manifest = describe(FIXTURES / "with_vendor_xml.docx")
        assert manifest is None
