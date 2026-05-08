from pathlib import Path

import pytest

from mint.theme import parse_theme
from mint.theme_extract import (
    extract_theme,
    register_user_theme,
    write_theme_toml,
)

FIXTURE = Path(__file__).resolve().parents[2] / "docs" / "docx_showcase.docx"


@pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="docs/docx_showcase.docx not present in this checkout",
)
class TestExtractFromShowcase:
    def test_extracts_primary_from_showcase(self) -> None:
        tokens = extract_theme(FIXTURE, name="user_test")
        assert tokens["meta"]["name"] == "user_test"
        # The reference uses 1B3A5C as primary navy; extractor should find it.
        assert tokens["palette"]["primary"] == "1B3A5C"

    def test_extracts_table_tokens(self) -> None:
        tokens = extract_theme(FIXTURE, name="user_test")
        assert tokens["tables"]["target_width_dxa"] == 9360
        margins = tokens["tables"]["cell_margins"]
        assert margins == {"top": 80, "bottom": 80, "left": 120, "right": 120}
        header = tokens["tables"]["header"]
        assert header["fill"] == "1B3A5C"
        assert header["text"] == "FFFFFF"

    def test_round_trips_through_parse_theme(self, tmp_path: Path) -> None:
        # Extracted tokens must satisfy the parse_theme contract.
        tokens = extract_theme(FIXTURE, name="user_round_trip")
        out = tmp_path / "user_round_trip.toml"
        write_theme_toml(tokens, out)
        # Read back via the regular loader path
        import tomllib
        with out.open("rb") as f:
            data = tomllib.load(f)
        theme = parse_theme(data)
        assert theme.name == "user_round_trip"
        assert theme.palette.primary == "1B3A5C"
        assert theme.tables.target_width_dxa == 9360

    def test_register_user_theme_writes_to_themes_dir(
        self, tmp_path: Path
    ) -> None:
        target = register_user_theme(
            FIXTURE,
            "user_registered",
            description="extracted via test",
            themes_dir=tmp_path,
        )
        assert target == tmp_path / "user_registered.toml"
        assert target.exists()
        # Loadable through the same parse_theme contract.
        import tomllib
        with target.open("rb") as f:
            data = tomllib.load(f)
        theme = parse_theme(data)
        assert theme.name == "user_registered"
        assert theme.description == "extracted via test"


def test_register_user_theme_rejects_unsafe_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        register_user_theme(
            tmp_path / "missing.docx",
            "../../etc/passwd",
            themes_dir=tmp_path,
        )


def test_extract_theme_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_theme(tmp_path / "nope.docx", name="x")
