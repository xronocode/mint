# FILE: src/mint/assemble.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Assemble final document from DocumentPlan + section code via JS template
#   SCOPE: Template rendering, styles/numbering/headers, sandbox execution
#   DEPENDS: M-PLAN, M-SANDBOX
#   LINKS: docs/knowledge-graph.xml#M-ASSEMBLE, docs/verification-plan.xml#V-M-ASSEMBLE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   AssemblyResult - assembly output dataclass
#   AssemblyError - base assembly exception
#   AssemblyTemplateError - template rendering failure
#   AssemblyExecutionFailedError - sandbox execution failure
#   AssemblyMissingSectionsError - required sections missing
#   assemble - main entry: plan + sections → DOCX file
#   build_styles_config - render JS styles from StylesConfig
#   build_numbering_config - render JS numbering from NumberingConfig list
#   build_headers_footers - render JS header/footer
#   build_section_wrapper - map SectionSpec to docx-js Section config
#   make_placeholder - generate placeholder JS for failed sections
#   render_assembly_template - generate complete JS from template
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.1 - Migrate hardcoded color/size literals (cover hero,
#                header, footer, TOC, fallback) to ThemeTokens. assemble(),
#                render_assembly_template(), build_styles_config() now
#                accept a theme: ThemeTokens | None and default to
#                load_theme(). Plan-level styles still override.
# END_CHANGE_SUMMARY

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mint.plan import (
    DocumentPlan,
    HeaderFooterSpec,
    NumberingConfig,
    PageSetup,
    SectionSpec,
    StylesConfig,
)
from mint.theme import ThemeTokens, load_theme

logger = logging.getLogger(__name__)

_LOG_PREFIX = "Assemble"


class AssemblyError(Exception):
    pass


class AssemblyTemplateError(AssemblyError):
    pass


class AssemblyExecutionFailedError(AssemblyError):
    pass


class AssemblyMissingSectionsError(AssemblyError):
    pass


@dataclass(frozen=True)
class AssemblyResult:
    output_path: Path | None = None
    success: bool = False
    sections_included: int = 0
    sections_placeholder: int = 0
    duration_ms: int = 0
    error: str | None = None


def build_styles_config(
    styles: StylesConfig, theme: ThemeTokens | None = None
) -> str:
    if theme is None:
        theme = load_theme()
    heading_styles = ""
    for level in range(1, 7):
        size_key = f"h{level}"
        size = styles.heading_sizes.get(size_key, 24)
        # Theme wins unconditionally for design colors.
        color = "#" + theme.palette.primary
        if level <= 3:
            heading_styles += (
                f"{{\n"
                f"  id: 'Heading{level}',\n"
                f"  name: 'Heading {level}',\n"
                f"  basedOn: 'Normal',\n"
                f"  next: 'Normal',\n"
                f"  quickFormat: true,\n"
                f"  run: {{\n"
                f"    size: {size},\n"
                f"    color: '{color}',\n"
                f"    bold: true,\n"
                f"    font: '{theme.typography.default_font}',\n"
                f"  }},\n"
                f"  paragraph: {{\n"
                f"    spacing: {{ "
                f"before: {theme.paragraph.heading_before}, "
                f"after: {theme.paragraph.heading_after} }},\n"
                f"  }},\n"
                f"}},\n"
            )
        else:
            heading_styles += (
                f"{{\n"
                f"  id: 'Heading{level}',\n"
                f"  name: 'Heading {level}',\n"
                f"  basedOn: 'Normal',\n"
                f"  next: 'Normal',\n"
                f"  quickFormat: true,\n"
                f"  run: {{\n"
                f"    size: {size},\n"
                f"    color: '{color}',\n"
                f"    bold: true,\n"
                f"    font: '{theme.typography.default_font}',\n"
                f"  }},\n"
                f"}},\n"
            )

    return (
        "default: {\n"
        f"  document: {{\n"
        f"    run: {{\n"
        f"      size: {styles.body_size},\n"
        f"      font: '{theme.typography.default_font}',\n"
        f"      color: '#{theme.palette.body}',\n"
        f"    }},\n"
        f"  }},\n"
        f"}},\n"
        f"paragraphStyles: [\n"
        f"  {{\n"
        f"    id: 'Normal',\n"
        f"    name: 'Normal',\n"
        f"    run: {{\n"
        f"      size: {styles.body_size},\n"
        f"      font: '{theme.typography.default_font}',\n"
        f"    }},\n"
        f"  }},\n"
        f"  {heading_styles}"
        f"  {{\n"
        f"    id: 'ListParagraph',\n"
        f"    name: 'List Paragraph',\n"
        f"    basedOn: 'Normal',\n"
        f"    paragraph: {{\n"
        f"      spacing: {{ before: 60, after: 60 }},\n"
        f"    }},\n"
        f"  }},\n"
        f"]"
    )


def build_numbering_config(numbering: list[NumberingConfig]) -> str:
    if not numbering:
        return ""

    configs: list[str] = []
    for nc in numbering:
        levels_js = ""
        for lv in nc.levels:
            levels_js += (
                f"{{\n"
                f"  level: {lv.level},\n"
                f"  format: LevelFormat.{lv.format.upper()},\n"
                f"  text: '{lv.text}',\n"
                f"  alignment: AlignmentType.LEFT,\n"
                f"  style: {{\n"
                f"    paragraph: {{\n"
                f"      indent: {{ "
                f"left: {lv.indent_left}, "
                f"hanging: {lv.indent_hanging} "
                f"}},\n"
                f"    }},\n"
                f"  }},\n"
                f"}},\n"
            )
        configs.append(
            f"{{\n"
            f"  reference: '{nc.reference}',\n"
            f"  levels: [\n{levels_js}  ],\n"
            f"}}"
        )

    return ", ".join(configs)


def build_headers_footers(hf: HeaderFooterSpec) -> tuple[str, str]:
    header_parts: list[str] = []
    if hf.header_left:
        header_parts.append(
            "new Paragraph({\n"
            "  children: [\n"
            f"    new TextRun({json.dumps(hf.header_left)}),\n"
            "  ],\n"
            "})"
        )
    if hf.header_right:
        header_parts.append(
            "new Paragraph({\n"
            "  children: [\n"
            "    new PositionalTab({ alignment: PositionalTabAlignment.RIGHT }),\n"
            f"    new TextRun({json.dumps(hf.header_right)}),\n"
            "  ],\n"
            "})"
        )

    if header_parts:
        header_js = (
            "new Header({ children: ["
            + ", ".join(header_parts)
            + "] })"
        )
    else:
        header_js = "undefined"

    footer_parts: list[str] = []
    if hf.footer_left:
        footer_parts.append(f"new TextRun({json.dumps(hf.footer_left)})")
    if hf.page_numbers:
        footer_parts.append(
            "new TextRun({ children: [PageNumber.CURRENT, ' / ', "
            "PageNumber.TOTAL_PAGES] })"
        )

    if footer_parts:
        joined = ", ".join(footer_parts)
        footer_js = (
            "new Footer({ children: [new Paragraph({ children: ["
            + joined
            + "] })] })"
        )
    else:
        footer_js = "undefined"

    return header_js, footer_js


def build_section_wrapper(
    spec: SectionSpec,
    page_setup: PageSetup,
    header_js: str,
    footer_js: str,
) -> str:
    props_parts: list[str] = []

    props_parts.append(
        f"page: {{\n"
        f"  size: {{\n"
        f"    width: {page_setup.width},\n"
        f"    height: {page_setup.height},\n"
        f"    orientation: PageOrientation.{page_setup.orientation.upper()},\n"
        f"  }},\n"
        f"  margin: {{\n"
        f"    top: {page_setup.margins.get('top', 1440)},\n"
        f"    right: {page_setup.margins.get('right', 1440)},\n"
        f"    bottom: {page_setup.margins.get('bottom', 1440)},\n"
        f"    left: {page_setup.margins.get('left', 1440)},\n"
        f"  }},\n"
        f"}}"
    )

    # CRITICAL: only the cover gets a default (nextPage) section break.
    # Every other section is continuous so content flows on the same page
    # when it fits — otherwise each conceptual section forces its own
    # page and the document looks like loose pages stitched together.
    if spec.type != "cover":
        props_parts.append("type: SectionType.CONTINUOUS")

    props_js = ",\n  ".join(props_parts)

    toc_note = ""
    if spec.type == "toc":
        toc_note = "// Note: TableOfContents should be added as first child\n"

    # docx-js Section: `headers` and `footers` are SIBLINGS of `properties`,
    # not nested inside it. Cover skips them so the cover page is clean.
    headers_block = ""
    footers_block = ""
    if spec.type != "cover":
        if header_js != "undefined":
            headers_block = (
                f"  headers: {{\n"
                f"    default: {header_js},\n"
                f"  }},\n"
            )
        if footer_js != "undefined":
            footers_block = (
                f"  footers: {{\n"
                f"    default: {footer_js},\n"
                f"  }},\n"
            )

    return (
        f"{{\n"
        f"  properties: {{\n"
        f"    {props_js}\n"
        f"  }},\n"
        f"{headers_block}"
        f"{footers_block}"
        f"  children: [\n"
        f"    {toc_note}"
        f"    /* SECTION_CODE_PLACEHOLDER:{spec.id} */\n"
        f"  ],\n"
        f"}}"
    )


def make_placeholder(spec: SectionSpec) -> str:
    return (
        f"new Paragraph({{\n"
        f"  children: [\n"
        f"    new TextRun({{\n"
        f"      text: {json.dumps(spec.title)},\n"
        f"      bold: true,\n"
        f"      size: {spec.level * 4 + 20},\n"
        f"    }}),\n"
        f"  ],\n"
        f"}}),\n"
        f"new Paragraph({{\n"
        f"  children: [\n"
        f"    new TextRun({{\n"
        f"      text: 'Section generation failed',\n"
        f"      italics: true,\n"
        f"      color: '999999',\n"
        f"    }}),\n"
        f"  ],\n"
        f"}})"
    )


# START_BLOCK_ASSEMBLE_TEMPLATE
def render_assembly_template(
    plan_data: DocumentPlan,
    sections_code: dict[str, str],
    sections_placeholder: set[str] | None = None,
    theme: ThemeTokens | None = None,
) -> str:
    if sections_placeholder is None:
        sections_placeholder = set()
    if theme is None:
        theme = load_theme()

    styles_js = build_styles_config(plan_data.styles, theme)
    valid_numbering = [nc for nc in plan_data.numbering if nc.reference.strip()]
    numbering_js = build_numbering_config(valid_numbering)
    header_js, footer_js = build_headers_footers(plan_data.header_footer)

    # Always-on running header + paginated footer. The plan's
    # HeaderFooterSpec is unreliable (LLM doesn't always populate it) and
    # without these checks 01/02/03 in the structural rubric fail. Derive
    # the doc title from the cover spec OR from plan.metadata.
    cover_spec = next(
        (s for s in plan_data.sections if s.type == "cover"),
        plan_data.sections[0] if plan_data.sections else None,
    )
    doc_title = cover_spec.title if cover_spec else "MINT Document"
    # Theme is the design ground-truth — it always wins over plan.styles.colors
    # which is the model's unhelpful guess from prompt context.
    primary = theme.palette.primary
    muted = theme.palette.muted
    footer_size = theme.typography.style("footer").size
    header_text_size = theme.typography.style("caption").size

    header_js = (
        "new Header({ children: [new Paragraph({\n"
        "  tabStops: [{ type: TabStopType.RIGHT, position: 9000 }],\n"
        "  border: { bottom: { style: BorderStyle.SINGLE, size: 6, "
        f"color: '{primary}', space: 4 }} }},\n"
        "  children: [\n"
        f"    new TextRun({{ text: {json.dumps(doc_title)}, "
        f"bold: true, size: {header_text_size}, color: '{primary}' }}),\n"
        "    new TextRun({ text: '\\t' }),\n"
        f"    new TextRun({{ text: 'MINT-generated report', "
        f"italics: true, size: {footer_size}, color: '{muted}' }}),\n"
        "  ],\n"
        "})] })"
    )
    footer_js = (
        "new Footer({ children: [new Paragraph({\n"
        "  alignment: AlignmentType.CENTER,\n"
        "  children: [\n"
        f"    new TextRun({{ text: 'Page ', "
        f"size: {footer_size}, color: '{muted}' }}),\n"
        f"    new TextRun({{ children: [PageNumber.CURRENT], "
        f"size: {footer_size}, color: '{muted}' }}),\n"
        f"    new TextRun({{ text: ' of ', "
        f"size: {footer_size}, color: '{muted}' }}),\n"
        f"    new TextRun({{ children: [PageNumber.TOTAL_PAGES], "
        f"size: {footer_size}, color: '{muted}' }}),\n"
        "  ],\n"
        "})] })"
    )

    sections_js_parts: list[str] = []
    for spec in plan_data.sections:
        if spec.type == "cover":
            # Wave B: replace model-generated cover with a fixed assembler
            # layout. The model's text is unreliable for cover (long
            # paragraphs, weird sizes, missing decoration). All visual
            # parameters come from the active theme — no hardcoded literals.
            primary_color = theme.palette.primary
            accent_color = theme.palette.accent
            muted_color = theme.palette.muted
            cover_title = spec.title.replace("'", "\\'")
            tagline = theme.cover.tagline

            code = (
                # Top spacer to vertically center the title block
                "new Paragraph({ spacing: { before: 3600 }, children: [] }),\n"
                # Hero title (centered, large, primary color)
                "new Paragraph({\n"
                "  alignment: AlignmentType.CENTER,\n"
                "  spacing: { after: 240 },\n"
                "  children: [new TextRun({\n"
                f"    text: '{cover_title}',\n"
                f"    bold: true, size: {theme.cover.hero_size}, "
                f"color: '{primary_color}'\n"
                "  })],\n"
                "}),\n"
                # Decorative bar
                "new Paragraph({\n"
                "  alignment: AlignmentType.CENTER,\n"
                "  border: { bottom: { style: BorderStyle.SINGLE, "
                f"size: {theme.cover.accent_bar_size}, "
                f"color: '{accent_color}', space: 1 }} }},\n"
                "  spacing: { after: 360 },\n"
                "  children: [],\n"
                "}),\n"
                # Tagline (centered, italic, muted)
                "new Paragraph({\n"
                "  alignment: AlignmentType.CENTER,\n"
                "  spacing: { after: 7200 },\n"
                "  children: [new TextRun({\n"
                f"    text: '{tagline}',\n"
                f"    italics: true, size: {theme.cover.tagline_size}, "
                f"color: '{muted_color}'\n"
                "  })],\n"
                "}),\n"
                # Bottom metadata footer
                "new Paragraph({\n"
                "  alignment: AlignmentType.CENTER,\n"
                "  children: [new TextRun({\n"
                "    text: 'Generated by MINT  •  Local LLM Pipeline',\n"
                f"    size: {theme.cover.metadata_size}, "
                f"color: '{muted_color}'\n"
                "  })],\n"
                "})"
            )
        elif spec.type == "toc":
            # Always synthesize a TOC slot — never trust model output for this
            # section (model tends to emit mock content like "Level 1 /
            # Introduction / 1"). The TableOfContents widget renders empty in
            # soffice/Pages until the user manually refreshes the field, so we
            # ALSO emit a placeholder paragraph that looks reasonable on first
            # open. Postprocess (_postprocess_docx) walks the produced XML and
            # injects real H1/H2 entries below this placeholder once it knows
            # the section structure.
            toc_h_size = theme.typography.style("heading1").size
            toc_h_color = theme.typography.style("heading1").color
            toc_caption = theme.typography.style("caption")
            code = (
                "new Paragraph({ children: ["
                f"new TextRun({{ text: 'Table of Contents', "
                f"bold: true, size: {toc_h_size}, "
                f"color: '{toc_h_color}' }})] }}),\n"
                "new Paragraph({ spacing: { after: 120 }, children: ["
                "new TextRun({ text: '(refresh to populate — entries shown "
                "below are auto-generated from headings)', "
                f"italics: true, color: '{toc_caption.color}', "
                f"size: {toc_caption.size} }})] }}),\n"
                "new TableOfContents('Table of Contents', { "
                "hyperlink: true, headingStyleRange: '1-3' })"
            )
        else:
            code = sections_code.get(spec.id, "")
            if not code or spec.id in sections_placeholder:
                code = make_placeholder(spec)

        wrapper = build_section_wrapper(
            spec, plan_data.page_setup, header_js, footer_js
        )

        wrapper = wrapper.replace(
            f"/* SECTION_CODE_PLACEHOLDER:{spec.id} */",
            code,
        )

        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", spec.id)
        wrapped = (
            f"(() => {{\n"
            f"  try {{\n"
            f"    return {wrapper};\n"
            f"  }} catch (__e_{safe_id}) {{\n"
            f"    return {{\n"
            f"      properties: {{\n"
            f"        page: {{\n"
            f"          size: {{ width: {plan_data.page_setup.width}, "
            f"height: {plan_data.page_setup.height}, "
            f"orientation: PageOrientation.{plan_data.page_setup.orientation.upper()} }},\n"
            f"          margin: {{ top: 1440, right: 1440, bottom: 1440, left: 1440 }},\n"
            f"        }},\n"
            f"      }},\n"
            f"      children: [\n"
            f"        new Paragraph({{ children: [new TextRun({{ "
            f"text: '{spec.title}', "
            f"bold: true, size: {theme.typography.style('heading2').size} }})] }}),\n"
            f"        new Paragraph({{ children: [new TextRun({{ "
            f"text: 'Section failed: ' + __e_{safe_id}.message, "
            f"italics: true, color: '{theme.palette.muted}' }})] }}),\n"
            f"      ],\n"
            f"    }};\n"
            f"  }}\n"
            f"}})()"
        )
        sections_js_parts.append(wrapped)

    numbering_section = ""
    if numbering_js:
        numbering_section = f",\n  numbering: {{ config: [{numbering_js}] }}"

    return (
        "const doc = new Document({\n"
        "  styles: {\n"
        f"    {styles_js}\n"
        "  }"
        f"{numbering_section}\n"
        "  ,\n"
        "  sections: [\n"
        + ",\n".join(sections_js_parts)
        + "\n  ],\n"
        "});\n\n"
        "const buffer = await Packer.toBuffer(doc);\n"
        "writeFileSync('output.docx', buffer);\n"
    )
# END_BLOCK_ASSEMBLE_TEMPLATE


# START_BLOCK_ASSEMBLE_EXECUTE
def assemble(
    plan_data: DocumentPlan,
    sections_code: dict[str, Any],
    theme: ThemeTokens | None = None,
) -> AssemblyResult:
    from mint.sandbox import SandboxResult
    from mint.sandbox import execute as sandbox_execute

    start = time.monotonic()
    logger.info(
        f"[{_LOG_PREFIX}][execute][BLOCK_ASSEMBLE_EXECUTE] "
        "Building assembly template: %d sections",
        len(plan_data.sections),
    )

    valid_section_ids = {s.id for s in plan_data.sections}
    code_map: dict[str, str] = {}
    placeholder_ids: set[str] = set()

    for sid, sc in sections_code.items():
        if sid not in valid_section_ids:
            logger.warning(
                f"[{_LOG_PREFIX}][template] Unknown section ID: {sid}"
            )
            continue
        code_str = sc.code if hasattr(sc, "code") else str(sc)
        has_success = hasattr(sc, "success") and sc.success
        if has_success and code_str:
            code_map[sid] = code_str
        else:
            placeholder_ids.add(sid)

    for spec in plan_data.sections:
        if spec.id not in code_map and spec.id not in placeholder_ids:
            placeholder_ids.add(spec.id)

    if not code_map and not placeholder_ids:
        return AssemblyResult(
            success=False,
            error="No sections to assemble",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    if theme is None:
        theme = load_theme()

    try:
        js_code = render_assembly_template(
            plan_data, code_map, placeholder_ids, theme=theme
        )
    except Exception as e:
        raise AssemblyTemplateError(f"Template rendering failed: {e}") from e

    logger.info(
        f"[{_LOG_PREFIX}][execute][BLOCK_ASSEMBLE_EXECUTE] "
        "Executing assembly: %d sections (%d placeholders), JS=%d chars",
        len(code_map) + len(placeholder_ids),
        len(placeholder_ids),
        len(js_code),
    )

    try:
        result: SandboxResult = sandbox_execute(js_code)
    except Exception as e:
        raise AssemblyExecutionFailedError(
            f"Sandbox execution failed: {e}"
        ) from e

    duration_ms = int((time.monotonic() - start) * 1000)

    if not result.success:
        return AssemblyResult(
            success=False,
            error=f"Sandbox execution failed: {result.stderr}",
            sections_included=len(code_map),
            sections_placeholder=len(placeholder_ids),
            duration_ms=duration_ms,
        )

    output_path = result.output_path
    if output_path is None or not output_path.exists():
        return AssemblyResult(
            success=False,
            error="Sandbox produced no output file",
            sections_included=len(code_map),
            sections_placeholder=len(placeholder_ids),
            duration_ms=duration_ms,
        )

    logger.info(
        f"[{_LOG_PREFIX}][execute][BLOCK_ASSEMBLE_EXECUTE] "
        "Assembly complete: output=%s, sections=%d, placeholders=%d, duration=%dms",
        output_path,
        len(code_map),
        len(placeholder_ids),
        duration_ms,
    )

    return AssemblyResult(
        output_path=output_path,
        success=True,
        sections_included=len(code_map),
        sections_placeholder=len(placeholder_ids),
        duration_ms=duration_ms,
    )
# END_BLOCK_ASSEMBLE_EXECUTE
