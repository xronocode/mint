# FILE: tests/unit/test_assemble.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Unit tests for M-ASSEMBLE module (template building functions)
#   SCOPE: build_styles_config, build_numbering_config, build_headers_footers, make_placeholder
#   DEPENDS: M-ASSEMBLE
#   LINKS: docs/verification-plan.xml#V-M-ASSEMBLE
# END_MODULE_CONTRACT


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
        # Theme is the design ground-truth — styles.colors no longer leaks
        # into headings. The default theme (showcase_v1) provides primary
        # 1B3A5C, so heading color is "#1B3A5C" regardless of plan input.
        styles = StylesConfig(
            colors={"primary": "#FF0000"},
            heading_sizes={"h1": 32, "h2": 28, "h3": 24},
        )
        js = build_styles_config(styles)
        assert "Heading1" in js
        assert "Heading2" in js
        assert "Heading3" in js
        assert "#1B3A5C" in js  # default theme primary, not plan
        assert "#FF0000" not in js  # plan colors must NOT leak into heading
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
    def test_contains_title_using_heading_style(self):
        # Placeholder must use a Heading style so the rendered heading
        # matches the rest of the document, AND must NOT leak any
        # "Section generation failed" chrome into the user-visible doc.
        spec = SectionSpec(id="s1", type="content", title="My Section",
                           description="Some real description.", level=1,
                           order=0)
        js = make_placeholder(spec)
        assert "My Section" in js
        assert "Some real description" in js
        assert "style: 'Heading1'" in js
        assert "Section generation failed" not in js
        assert "Paragraph" in js

    def test_uses_description_as_body(self):
        spec = SectionSpec(id="s2", type="content", title="X",
                           description="Body content here.", level=2,
                           order=0)
        js = make_placeholder(spec)
        assert "Body content here." in js
        assert "style: 'Heading2'" in js


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
        # Placeholder must use the heading style and the section's
        # description as body — and must NOT leak failure chrome.
        assert "Test" in js
        assert "Heading1" in js
        assert "Section generation failed" not in js

    def test_no_branding_leaks_when_chrome_unset(self):
        # When the planner provides no chrome strings, nothing branded
        # like "Generated by MINT" or "MINT-generated report" should
        # land in the output document.
        from mint.plan import DocumentPlan, HeaderFooterSpec

        plan = DocumentPlan(
            format="docx",
            sections=[
                SectionSpec(id="cov", type="cover", title="My Doc",
                            description="d", level=1, order=0),
                SectionSpec(id="s1", type="content", title="Body",
                            description="d", level=1, order=1),
            ],
            styles=StylesConfig(),
            header_footer=HeaderFooterSpec(),
        )
        js = render_assembly_template(plan, {"s1": ""})
        assert "Generated by MINT" not in js
        assert "MINT-generated report" not in js

    def test_chrome_strings_from_plan_render(self):
        # When the planner supplies header_right and cover_metadata,
        # both must reach the output JS as TextRuns.
        from mint.plan import DocumentPlan, HeaderFooterSpec

        plan = DocumentPlan(
            format="docx",
            sections=[
                SectionSpec(id="cov", type="cover", title="Project Atlas",
                            description="d", level=1, order=0),
                SectionSpec(id="s1", type="content", title="Body",
                            description="d", level=1, order=1),
            ],
            styles=StylesConfig(),
            header_footer=HeaderFooterSpec(
                header_left="Project Atlas",
                header_right="Q1 2026 Review",
                cover_metadata="Acme Corp  •  Engineering",
            ),
        )
        js = render_assembly_template(plan, {"s1": ""})
        assert "Q1 2026 Review" in js
        assert "Acme Corp" in js
        assert "Project Atlas" in js
