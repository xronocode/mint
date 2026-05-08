# FILE: tests/unit/test_mp_section.py
# START_MODULE_CONTRACT
#   PURPOSE: V-MP-SECTION scenarios 1-6 — Section fluent surface, render order
#     with heading, str/Paragraph polymorphism on add_paragraph, level-range
#     enforcement, and the add_chart Phase-2 stub (NotImplementedError content
#     + BLOCK_PHASE_GUARD trace).
#   SCOPE: Reuses central conftest fixtures (mp_clean_env, caplog_at_info,
#     marker_counter); imports sibling Paragraph + Table to validate
#     polymorphism + render ordering. No fixture redefinitions.
#   DEPENDS: pytest, mint_python.core.section, mint_python.core.content,
#     mint_python.core.table, python-docx.
#   LINKS: docs/verification-plan.xml#V-MP-SECTION,
#     docs/development-plan.xml#MP-SECTION
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_scenario_1_fluent_returns_self
#   test_scenario_2_render_emits_heading_then_blocks_in_order
#   test_scenario_3_add_paragraph_accepts_str_and_paragraph
#   test_scenario_4_level_out_of_range_raises
#   test_scenario_5_add_chart_raises_not_implemented
#   test_scenario_6_add_chart_emits_block_phase_guard_before_raising
#   test_phase_guard_is_subclass_of_not_implemented_error
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Wave-7-3 (MP-SECTION): initial test suite.
# END_CHANGE_SUMMARY
from __future__ import annotations

import pytest
from docx import Document

from mint_python.core.content import Paragraph
from mint_python.core.section import (
    PhaseGuardNotImplementedError,
    Section,
    SectionLevelOutOfRangeError,
)
from mint_python.core.table import Table

# ---------------------------------------------------------------------------
# V-MP-SECTION scenario-1: fluent setters return self
# ---------------------------------------------------------------------------


def test_scenario_1_fluent_returns_self() -> None:
    section = Section("Title", level=1)
    assert section.add_paragraph("text") is section

    table = Table.from_list([["h"], ["a"]], header=True)
    assert section.add_table(table) is section

    # add_image accepts an Image; we don't construct one here to avoid a real
    # file dependency — scenario-1 only contracts "returns self" on
    # add_paragraph (chained with add_table to confirm both are fluent).


# ---------------------------------------------------------------------------
# V-MP-SECTION scenario-2: render emits heading then content blocks in order
# ---------------------------------------------------------------------------


def test_scenario_2_render_emits_heading_then_blocks_in_order() -> None:
    doc = Document()
    section = (
        Section("My Section", level=2)
        .add_paragraph("first")
        .add_paragraph("second")
    )
    before = len(doc.paragraphs)
    section.render(doc)

    new_paragraphs = doc.paragraphs[before:]
    # Expect: 1 heading paragraph + 2 body paragraphs.
    assert len(new_paragraphs) == 3

    heading_para = new_paragraphs[0]
    # python-docx assigns "Heading 2" style for level=2.
    assert heading_para.style.name == "Heading 2"
    assert heading_para.text == "My Section"

    # Body paragraphs preserve order.
    assert new_paragraphs[1].text == "first"
    assert new_paragraphs[2].text == "second"


# ---------------------------------------------------------------------------
# V-MP-SECTION scenario-3: add_paragraph accepts both str and Paragraph
# ---------------------------------------------------------------------------


def test_scenario_3_add_paragraph_accepts_str_and_paragraph() -> None:
    doc = Document()
    explicit = Paragraph("explicit")
    section = (
        Section("Polymorphism", level=3)
        .add_paragraph("shorthand")
        .add_paragraph(explicit)
    )
    before = len(doc.paragraphs)
    section.render(doc)

    new_paragraphs = doc.paragraphs[before:]
    # heading + 2 body paragraphs.
    assert len(new_paragraphs) == 3
    assert new_paragraphs[1].text == "shorthand"
    assert new_paragraphs[2].text == "explicit"


# ---------------------------------------------------------------------------
# V-MP-SECTION scenario-4: level out of range raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_level", [0, 7, 10, -1])
def test_scenario_4_level_out_of_range_raises(bad_level: int) -> None:
    with pytest.raises(SectionLevelOutOfRangeError) as exc_info:
        Section("X", level=bad_level)
    assert str(bad_level) in str(exc_info.value)


# ---------------------------------------------------------------------------
# V-MP-SECTION scenario-5: add_chart raises NotImplementedError; message
# names "Phase 2" and "matplotlib"
# ---------------------------------------------------------------------------


def test_scenario_5_add_chart_raises_not_implemented() -> None:
    section = Section("Charts", level=1)
    with pytest.raises(NotImplementedError) as exc_info:
        section.add_chart({"data": [1, 2, 3]})
    msg = str(exc_info.value)
    assert "Phase 2" in msg
    assert "matplotlib" in msg.lower()


def test_phase_guard_is_subclass_of_not_implemented_error() -> None:
    # The custom error MUST remain a NotImplementedError subclass so callers
    # using the standard exception type keep working.
    assert issubclass(PhaseGuardNotImplementedError, NotImplementedError)

    section = Section("Charts", level=1)
    with pytest.raises(PhaseGuardNotImplementedError):
        section.add_chart()


# ---------------------------------------------------------------------------
# V-MP-SECTION scenario-6: add_chart emits BLOCK_PHASE_GUARD BEFORE raising;
# payload carries method=add_chart and target_phase=Phase 2 substring
# ---------------------------------------------------------------------------


def test_scenario_6_add_chart_emits_block_phase_guard_before_raising(
    caplog_at_info, marker_counter
) -> None:
    section = Section("Charts", level=1)
    with pytest.raises(NotImplementedError):
        section.add_chart(kind="bar")

    # Marker counter sees BLOCK_PHASE_GUARD exactly once.
    counts = marker_counter(caplog_at_info)
    assert counts["BLOCK_PHASE_GUARD"] == 1

    # Locate the record that carries the marker; verify payload substrings.
    guard_records = [
        r for r in caplog_at_info.records if "BLOCK_PHASE_GUARD" in r.getMessage()
    ]
    assert len(guard_records) == 1
    payload = guard_records[0].getMessage()
    assert "[MP-Section]" in payload
    assert "[add_chart]" in payload
    assert "method=add_chart" in payload
    assert "target_phase=Phase 2" in payload
