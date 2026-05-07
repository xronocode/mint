# FILE: tests/unit/test_assemble.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Unit tests for M-ASSEMBLE module (template building functions)
#   SCOPE: build_styles_config, build_numbering_config, build_headers_footers, make_placeholder
#   DEPENDS: M-ASSEMBLE
#   LINKS: docs/verification-plan.xml#V-M-ASSEMBLE
# END_MODULE_CONTRACT

import pytest

from mint.assemble import (
    build_headers_footers,
    build_numbering_config,
    build_section_wrapper,
    build_styles_config,
    make_placeholder,
    render_assembly_template,
)
from mint.plan import (
    HeaderFooterSpec,
    NumberingConfig,
    NumberingLevel,
    PageSetup,
    SectionSpec,
    StylesConfig,
)


class TestBuildStylesConfig:
    def test_contains_heading_styles_with_colors_and_sizes(self):
        styles = StylesConfig(
            colors={"primary": "#FF0000"},
            heading_sizes={"h1": 32, "h2": 28, "h3": 24},
        )
        js = build_styles_config(styles)
        assert "Heading1" in js
        assert "Heading2" in js
        assert "Heading3" in js
        assert "#FF0000" in js
        assert "32" in js
        assert "28" in js

    def test_contains_body_size(self):
        styles = StylesConfig(body_size=20)
        js = build_styles_config(styles)
        assert "20" in js

    def test_contains_list_paragraph(self):
        js = build_styles_config(StylesConfig())
        assert "ListParagraph" in js


class TestBuildNumberingConfig:
    def test_bullet_and_decimal(self):
        configs = [
            NumberingConfig(
                reference="bullet-list",
                levels=[
                    NumberingLevel(level=0, format="bullet", text="\u2022", indent_left=720, indent_hanging=360)
                ],
            ),
            NumberingConfig(
                reference="decimal-list",
                levels=[
                    NumberingLevel(level=0, format="decimal", text="%1.", indent_left=720, indent_hanging=360)
                ],
            ),
        ]
        js = build_numbering_config(configs)
        assert "LevelFormat.BULLET" in js
        assert "LevelFormat.DECIMAL" in js
        assert "bullet-list" in js
        assert "decimal-list" in js

    def test_empty_returns_empty(self):
        assert build_numbering_config([]) == ""


class TestBuildHeadersFooters:
    def test_header_with_page_numbers(self):
        hf = HeaderFooterSpec(header_left="My Doc", page_numbers=True)
        header_js, footer_js = build_headers_footers(hf)
        assert "My Doc" in header_js
        assert "PageNumber.CURRENT" in footer_js
        assert "PageNumber.TOTAL_PAGES" in footer_js

    def test_empty_returns_undefined(self):
        hf = HeaderFooterSpec(page_numbers=False)
        header_js, footer_js = build_headers_footers(hf)
        assert header_js == "undefined"
        assert footer_js == "undefined"


class TestMakePlaceholder:
    def test_contains_title(self):
        spec = SectionSpec(id="s1", type="content", title="My Section", description="d", level=1, order=0)
        js = make_placeholder(spec)
        assert "My Section" in js
        assert "Section generation failed" in js
        assert "Paragraph" in js


class TestBuildSectionWrapper:
    def test_cover_has_no_header_footer(self):
        spec = SectionSpec(id="cover", type="cover", title="Cover", description="d", level=1, order=0)
        ps = PageSetup()
        wrapper = build_section_wrapper(spec, ps, "new Header(...)", "new Footer(...)")
        assert "header" not in wrapper.lower() or "undefined" in wrapper

    def test_content_has_header_footer(self):
        spec = SectionSpec(id="s1", type="content", title="Body", description="d", level=1, order=0)
        ps = PageSetup()
        wrapper = build_section_wrapper(spec, ps, "new Header(...)", "new Footer(...)")
        assert "new Header" in wrapper
        assert "new Footer" in wrapper

    def test_page_dimensions_included(self):
        spec = SectionSpec(id="s1", type="content", title="Body", description="d", level=1, order=0)
        ps = PageSetup(width=12240, height=15840)
        wrapper = build_section_wrapper(spec, ps, "undefined", "undefined")
        assert "12240" in wrapper
        assert "15840" in wrapper


class TestRenderAssemblyTemplate:
    def test_produces_valid_js_with_sections(self):
        from mint.plan import DocumentPlan

        plan = DocumentPlan(
            format="docx",
            sections=[
                SectionSpec(id="s1", type="content", title="Test", description="d", level=1, order=0),
            ],
            styles=StylesConfig(),
        )
        sections_code = {"s1": "new Paragraph({ children: [new TextRun('Hello')] }),"}
        js = render_assembly_template(plan, sections_code)
        assert "new Document" in js
        assert "Packer.toBuffer" in js
        assert "writeFileSync" in js
        assert "Hello" in js

    def test_placeholder_for_missing_section(self):
        from mint.plan import DocumentPlan

        plan = DocumentPlan(
            format="docx",
            sections=[
                SectionSpec(id="s1", type="content", title="Test", description="d", level=1, order=0),
            ],
            styles=StylesConfig(),
        )
        js = render_assembly_template(plan, {}, sections_placeholder={"s1"})
        assert "Section generation failed" in js
