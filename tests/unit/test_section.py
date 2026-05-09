# FILE: tests/unit/test_section.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Unit tests for M-SECTION module (code validation + prompt rendering)
#   SCOPE: validate_section_code, render_section_prompt
#   DEPENDS: M-SECTION
#   LINKS: docs/verification-plan.xml#V-M-SECTION
# END_MODULE_CONTRACT


from mint.plan import (
    DocumentPlan,
    NumberingConfig,
    NumberingLevel,
    SectionSpec,
    StylesConfig,
)
from mint.section import (
    SiblingRef,
    render_section_prompt,
    validate_section_code,
)


def _make_plan():
    return DocumentPlan(
        format="docx",
        sections=[
            SectionSpec(id="s1", type="heading", title="Intro", description="Introduction", level=1, order=0),
            SectionSpec(id="s2", type="content", title="Body", description="Main content", level=1, order=1),
        ],
        styles=StylesConfig(),
        numbering=[
            NumberingConfig(
                reference="bullet-list",
                levels=[
                    NumberingLevel(level=0, format="bullet", text="\u2022", indent_left=720, indent_hanging=360)
                ],
            )
        ],
    )


class TestValidateSectionCode:
    def test_valid_paragraph_code_passes(self):
        # The min-content gate (≥3 elements OR ≥600 chars) means a single
        # Paragraph in a tiny snippet is intentionally rejected. Test passes
        # 3 paragraphs to clear the gate.
        code = (
            "new Paragraph({ children: [new TextRun('Hello')] }),\n"
            "new Paragraph({ children: [new TextRun('World')] }),\n"
            "new Paragraph({ children: [new TextRun('Done')] })"
        )
        errors = validate_section_code(code)
        assert errors == []

    def test_import_statement_detected(self):
        code = "import { Document } from 'docx';\nnew Paragraph({ children: [] }),"
        errors = validate_section_code(code)
        assert any("import" in e for e in errors)

    def test_require_detected(self):
        code = "const fs = require('fs');\nnew Paragraph({ children: [] }),"
        errors = validate_section_code(code)
        assert any("require" in e for e in errors)

    def test_unmatched_brackets_detected(self):
        # Validator runs `node --check` on a wrapped section, so the exact
        # error string is whatever node emits ("Unexpected token", "Missing )
        # after argument list", etc.). Just assert that broken syntax produces
        # a non-empty error list.
        code = "new Paragraph({ children: [new TextRun('hi')\n},"
        errors = validate_section_code(code)
        assert errors, (
            f"expected at least one error for broken code, got {errors!r}"
        )

    def test_valid_table_code_passes(self):
        # ≥600-char branch of the min-content gate.
        code = (
            "new Table({\n"
            "  rows: [\n"
            "    new TableRow({ children: [\n"
            "      new TableCell({ children: [new Paragraph('Pad-A')] }),\n"
            "      new TableCell({ children: [new Paragraph('Pad-B')] }),\n"
            "      new TableCell({ children: [new Paragraph('Pad-C')] }),\n"
            "      new TableCell({ children: [new Paragraph('Pad-D')] }),\n"
            "    ]}),\n"
            "    new TableRow({ children: [\n"
            "      new TableCell({ children: [new Paragraph('cell-1-1')] }),\n"
            "      new TableCell({ children: [new Paragraph('cell-1-2')] }),\n"
            "      new TableCell({ children: [new Paragraph('cell-1-3')] }),\n"
            "      new TableCell({ children: [new Paragraph('cell-1-4')] }),\n"
            "    ]}),\n"
            "  ],\n"
            "}),"
        )
        errors = validate_section_code(code)
        assert errors == []


class TestRenderSectionPrompt:
    def test_contains_section_title_and_description(self):
        plan = _make_plan()
        spec = plan.sections[1]
        prompt = render_section_prompt(plan, spec)
        assert spec.title in prompt
        assert spec.description in prompt

    def test_contains_sibling_context(self):
        plan = _make_plan()
        spec = plan.sections[1]
        siblings = [SiblingRef(section_id="s1", title="Intro", type="heading")]
        prompt = render_section_prompt(plan, spec, siblings=siblings)
        assert "Intro" in prompt
        assert "heading" in prompt

    def test_contains_style_context(self):
        plan = _make_plan()
        spec = plan.sections[0]
        prompt = render_section_prompt(plan, spec)
        assert "#1E40AF" in prompt
        assert "22" in prompt

    def test_contains_numbering_reference(self):
        plan = _make_plan()
        spec = plan.sections[0]
        prompt = render_section_prompt(plan, spec)
        assert "bullet-list" in prompt
