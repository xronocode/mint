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
#   LAST_CHANGE: v0.1.0 - Initial implementation: JS template + sandbox execution
# END_CHANGE_SUMMARY

from __future__ import annotations

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


def build_styles_config(styles: StylesConfig) -> str:
    heading_styles = ""
    for level in range(1, 7):
        size_key = f"h{level}"
        size = styles.heading_sizes.get(size_key, 24)
        color = styles.colors.get("primary", "#1E40AF")
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
                f"    font: 'Calibri',\n"
                f"  }},\n"
                f"  paragraph: {{\n"
                f"    spacing: {{ before: 240, after: 120 }},\n"
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
                f"    font: 'Calibri',\n"
                f"  }},\n"
                f"}},\n"
            )

    return (
        "default: {\n"
        f"  document: {{\n"
        f"    run: {{\n"
        f"      size: {styles.body_size},\n"
        f"      font: 'Calibri',\n"
        f"      color: '{styles.colors.get('text', '#1F2937')}',\n"
        f"    }},\n"
        f"  }},\n"
        f"}},\n"
        f"paragraphStyles: [\n"
        f"  {{\n"
        f"    id: 'Normal',\n"
        f"    name: 'Normal',\n"
        f"    run: {{\n"
        f"      size: {styles.body_size},\n"
        f"      font: 'Calibri',\n"
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
        escaped = hf.header_left.replace("'", "\\'")
        header_parts.append(
            "new Paragraph({\n"
            "  children: [\n"
            f"    new TextRun('{escaped}'),\n"
            "  ],\n"
            "})"
        )
    if hf.header_right:
        escaped = hf.header_right.replace("'", "\\'")
        header_parts.append(
            "new Paragraph({\n"
            "  children: [\n"
            "    new PositionalTab({ alignment: PositionalTabAlignment.RIGHT }),\n"
            f"    new TextRun('{escaped}'),\n"
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
        escaped = hf.footer_left.replace("'", "\\'")
        footer_parts.append(f"new TextRun('{escaped}')")
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

    if spec.type == "cover":
        pass
    else:
        if header_js != "undefined":
            props_parts.append(f"header: {header_js}")
        if footer_js != "undefined":
            props_parts.append(f"footer: {footer_js}")

    props_js = ",\n  ".join(props_parts)

    toc_note = ""
    if spec.type == "toc":
        toc_note = "// Note: TableOfContents should be added as first child\n"

    return (
        f"{{\n"
        f"  properties: {{\n"
        f"    {props_js}\n"
        f"  }},\n"
        f"  children: [\n"
        f"    {toc_note}"
        f"    /* SECTION_CODE_PLACEHOLDER:{spec.id} */\n"
        f"  ],\n"
        f"}}"
    )


def make_placeholder(spec: SectionSpec) -> str:
    escaped_title = spec.title.replace("'", "\\'")
    return (
        f"new Paragraph({{\n"
        f"  children: [\n"
        f"    new TextRun({{\n"
        f"      text: '{escaped_title}',\n"
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
) -> str:
    if sections_placeholder is None:
        sections_placeholder = set()

    styles_js = build_styles_config(plan_data.styles)
    valid_numbering = [nc for nc in plan_data.numbering if nc.reference.strip()]
    numbering_js = build_numbering_config(valid_numbering)
    header_js, footer_js = build_headers_footers(plan_data.header_footer)

    sections_js_parts: list[str] = []
    for spec in plan_data.sections:
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
            f"        new Paragraph({{ children: [new TextRun({{ text: '{spec.title}', "
            f"bold: true, size: 28 }})] }}),\n"
            f"        new Paragraph({{ children: [new TextRun({{ "
            f"text: 'Section failed: ' + __e_{safe_id}.message, "
            f"italics: true, color: '999999' }})] }}),\n"
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

    try:
        js_code = render_assembly_template(plan_data, code_map, placeholder_ids)
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
