# FILE: tests/unit/test_plan.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Unit tests for M-PLAN module (plan validation + prompt rendering)
#   SCOPE: validate_plan, render_plan_prompt, _parse_plan_json
#   DEPENDS: M-PLAN
#   LINKS: docs/verification-plan.xml#V-M-PLAN
# END_MODULE_CONTRACT

import json

import pytest

from mint.plan import (
    DocumentPlan,
    HeaderFooterSpec,
    NumberingConfig,
    NumberingLevel,
    PageSetup,
    PlanEmptyError,
    PlanInvalidError,
    PlanMetadata,
    SectionSpec,
    StylesConfig,
    validate_plan,
    render_plan_prompt,
    _parse_plan_json,
)


def _make_plan(**overrides):
    defaults = dict(
        format="docx",
        sections=[
            SectionSpec(
                id="s1",
                type="heading",
                title="Introduction",
                description="Document introduction",
                level=1,
                order=0,
            ),
            SectionSpec(
                id="s2",
                type="content",
                title="Main Content",
                description="Main body text",
                level=1,
                order=1,
            ),
        ],
    )
    defaults.update(overrides)
    return DocumentPlan(**defaults)


class TestValidatePlan:
    def test_valid_plan_passes(self):
        plan = _make_plan()
        validate_plan(plan)

    def test_empty_sections_raises_plan_empty(self):
        plan = _make_plan(sections=[])
        with pytest.raises(PlanEmptyError, match="at least 1 section"):
            validate_plan(plan)

    def test_unknown_section_type_raises_plan_invalid(self):
        plan = _make_plan(
            sections=[
                SectionSpec(
                    id="s1",
                    type="unknown",
                    title="Test",
                    description="Test section",
                    level=1,
                    order=0,
                )
            ]
        )
        with pytest.raises(PlanInvalidError, match="Invalid section type"):
            validate_plan(plan)

    def test_valid_page_setup_passes(self):
        plan = _make_plan(
            page_setup=PageSetup(width=12240, height=15840)
        )
        validate_plan(plan)

    def test_zero_page_width_raises(self):
        plan = _make_plan(
            page_setup=PageSetup(width=0, height=15840)
        )
        with pytest.raises(PlanInvalidError, match="width"):
            validate_plan(plan)

    def test_zero_page_height_raises(self):
        plan = _make_plan(
            page_setup=PageSetup(width=12240, height=0)
        )
        with pytest.raises(PlanInvalidError, match="height"):
            validate_plan(plan)

    def test_duplicate_section_ids_raises(self):
        plan = _make_plan(
            sections=[
                SectionSpec(id="dup", type="content", title="A", description="a", level=1, order=0),
                SectionSpec(id="dup", type="content", title="B", description="b", level=1, order=1),
            ]
        )
        with pytest.raises(PlanInvalidError, match="Duplicate"):
            validate_plan(plan)

    def test_empty_title_raises(self):
        plan = _make_plan(
            sections=[
                SectionSpec(id="s1", type="content", title="  ", description="desc", level=1, order=0),
            ]
        )
        with pytest.raises(PlanInvalidError, match="non-empty title"):
            validate_plan(plan)

    def test_empty_description_raises(self):
        plan = _make_plan(
            sections=[
                SectionSpec(id="s1", type="content", title="Title", description="  ", level=1, order=0),
            ]
        )
        with pytest.raises(PlanInvalidError, match="non-empty description"):
            validate_plan(plan)

    def test_level_out_of_range_raises(self):
        plan = _make_plan(
            sections=[
                SectionSpec(id="s1", type="content", title="T", description="d", level=5, order=0),
            ]
        )
        with pytest.raises(PlanInvalidError, match="level must be 1-3"):
            validate_plan(plan)

    def test_valid_numbering_passes(self):
        plan = _make_plan(
            numbering=[
                NumberingConfig(
                    reference="bullet-list",
                    levels=[
                        NumberingLevel(level=0, format="bullet", text="\u2022", indent_left=720, indent_hanging=360)
                    ],
                )
            ]
        )
        validate_plan(plan)

    def test_empty_numbering_reference_raises(self):
        plan = _make_plan(
            numbering=[
                NumberingConfig(
                    reference="  ",
                    levels=[],
                )
            ]
        )
        with pytest.raises(PlanInvalidError, match="reference"):
            validate_plan(plan)


class TestRenderPlanPrompt:
    def test_contains_sections_and_heading(self):
        prompt = render_plan_prompt("docx")
        assert "sections" in prompt
        assert "Heading1" in prompt

    def test_docx_contains_dxa(self):
        prompt = render_plan_prompt("docx")
        assert "DXA" in prompt

    def test_pptx_no_dxa(self):
        prompt = render_plan_prompt("pptx")
        assert "DXA" not in prompt

    def test_with_design_tokens(self):
        prompt = render_plan_prompt("docx", design_tokens={"colors": {"primary": "#FF0000"}})
        assert "#FF0000" in prompt


class TestParsePlanJson:
    def test_valid_json(self):
        raw = json.dumps({
            "sections": [
                {"id": "s1", "type": "cover", "title": "Cover", "description": "Cover page", "level": 1, "order": 0},
            ],
            "styles": {"colors": {"primary": "#000"}, "heading_sizes": {"h1": 32}, "body_size": 22, "code_font": "Mono"},
            "numbering": [],
            "header_footer": {"header_left": "Test", "page_numbers": True},
            "page_setup": {"width": 12240, "height": 15840, "orientation": "portrait", "margins": {"top": 1440, "right": 1440, "bottom": 1440, "left": 1440}},
        })
        plan = _parse_plan_json(raw, "docx")
        assert plan.format == "docx"
        assert len(plan.sections) == 1
        assert plan.sections[0].type == "cover"
        assert plan.styles.colors["primary"] == "#000"
        assert plan.header_footer.header_left == "Test"

    def test_json_in_code_fence(self):
        raw = "```json\n" + json.dumps({"sections": [{"id": "s1", "type": "content", "title": "T", "description": "D", "level": 1, "order": 0}]}) + "\n```"
        plan = _parse_plan_json(raw, "docx")
        assert len(plan.sections) == 1

    def test_minimal_json_uses_defaults(self):
        raw = json.dumps({"sections": [{"id": "s1", "type": "content", "title": "T", "description": "D"}]})
        plan = _parse_plan_json(raw, "pptx")
        assert plan.format == "pptx"
        assert plan.page_setup.width == 12240
        assert plan.styles.body_size == 22

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_plan_json("not json at all", "docx")
