from pathlib import Path

import pytest

from mint.theme import (
    DEFAULT_THEME_NAME,
    StyleSpec,
    ThemeTokens,
    load_theme,
    parse_theme,
)


def test_default_theme_loads() -> None:
    theme = load_theme()
    assert isinstance(theme, ThemeTokens)
    assert theme.name == DEFAULT_THEME_NAME


def test_default_theme_palette_matches_showcase() -> None:
    theme = load_theme()
    assert theme.palette.primary == "1B3A5C"
    assert theme.palette.primary_text_on == "FFFFFF"
    assert theme.palette.body == "333333"
    assert theme.palette.border == "DDDDDD"
    assert theme.palette.alt_row == "F3F4F6"


def test_default_theme_callouts_present() -> None:
    theme = load_theme()
    for kind in ("warning", "note", "tip", "caution"):
        assert kind in theme.palette.callouts
        c = theme.palette.callouts[kind]
        assert len(c.border) == 6
        assert len(c.fill) == 6


def test_default_theme_typography_roles() -> None:
    theme = load_theme()
    expected = {
        "title", "heading1", "heading2", "heading3",
        "body", "code", "caption", "footer", "subtitle",
    }
    assert expected <= set(theme.typography.styles.keys())
    body = theme.typography.style("body")
    assert isinstance(body, StyleSpec)
    assert body.size == 22


def test_default_theme_tables_match_reference() -> None:
    theme = load_theme()
    assert theme.tables.target_width_dxa == 9360
    assert theme.tables.cell_margins.top == 80
    assert theme.tables.cell_margins.left == 120
    assert theme.tables.header.fill == "1B3A5C"
    assert theme.tables.header.text == "FFFFFF"
    assert theme.tables.body.text == "333333"
    assert theme.tables.alt_row_fill == "F3F4F6"


def test_load_theme_unknown_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_theme("does_not_exist")


def test_parse_theme_missing_required_raises() -> None:
    with pytest.raises(KeyError):
        parse_theme({"meta": {"name": "broken", "version": 1}})


def test_typography_unknown_style_raises() -> None:
    theme = load_theme()
    with pytest.raises(KeyError):
        theme.typography.style("does_not_exist")
