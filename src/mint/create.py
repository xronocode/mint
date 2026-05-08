# FILE: src/mint/create.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Orchestrate full document generation: single-pass or modular (plan→section→assemble)
#   SCOPE: Create pipeline with code execution, template fill, and modular generation modes
#   DEPENDS: M-SKILLS, M-SANDBOX, M-TEMPLATES, M-VALIDATE, M-CONFIG,
#            M-PLAN, M-SECTION, M-ASSEMBLE, M-LLM
#   LINKS: docs/knowledge-graph.xml#M-CREATE, docs/verification-plan.xml#V-M-CREATE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CreateRequest - input dataclass for document generation
#   CreateResult - output dataclass with validation report and metadata
#   ModelCallFailedError - raised when model call fails
#   ExecutionFailedError - raised when sandbox execution fails
#   ValidationFailedError - raised when validation fails
#   create - main orchestration entry point (routes to single-pass or modular)
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.1 - Wave G: aggressive table normalize (full 9360 width,
#                dark-navy header + white bold text, 80/120 cell margins,
#                thin grey borders) and keepNext on headings to prevent
#                orphan tables on next page. Aligns output with the
#                docs/docx_showcase.docx reference design system.
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mint.config import SeverityMode, Tier
from mint.paths import (
    RULES_DIR as _DEFAULT_RULES_DIR,
)
from mint.paths import (
    SKILLS_DIR as _DEFAULT_SKILLS_DIR,
)
from mint.paths import (
    TEMPLATES_DIR as _DEFAULT_TEMPLATES_DIR,
)
from mint.sandbox import SandboxResult
from mint.sandbox import execute as sandbox_execute
from mint.skills import SkillRef, SkillRegistry
from mint.templates import TemplateEngine
from mint.theme import ThemeTokens, load_theme
from mint.validate import ValidationReport, run_checks

logger = logging.getLogger(__name__)


class ModelCallFailedError(Exception):
    pass


class ExecutionFailedError(Exception):
    pass


class ValidationFailedError(Exception):
    pass


@dataclass(frozen=True)
class CreateRequest:
    format: str
    tier: str
    prompt: str
    design_tokens: dict[str, Any] | None = None
    design_tokens_path: Path | None = None
    template_name: str | None = None
    model_response_override: str | None = None
    modular: bool = False
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_fallback_model: str | None = None
    self_evaluate: bool = False
    max_refine_rounds: int = 1
    theme_name: str | None = None


@dataclass
class CreateResult:
    output_path: Path | None = None
    validation_report: ValidationReport | None = None
    qa_report: Any = None
    execution_mode: str = ""
    duration_ms: int = 0
    error: str | None = None
    success: bool = False
    plan: Any = None
    sections_total: int = 0
    sections_succeeded: int = 0


# START_BLOCK_ORCHESTRATE
def create(
    request: CreateRequest,
    *,
    skills_dir: Path | None = None,
    templates_dir: Path | None = None,
    rules_dir: Path | None = None,
) -> CreateResult:
    start = time.monotonic()
    logger.info(
        "[Create][orchestrate][BLOCK_ORCHESTRATE] "
        "Starting create: format=%s, tier=%s",
        request.format,
        request.tier,
    )

    try:
        tier = Tier(request.tier)
    except ValueError:
        return CreateResult(
            error=f"Invalid tier: {request.tier}",
            execution_mode="none",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    if skills_dir is None:
        skills_dir = _DEFAULT_SKILLS_DIR
    if rules_dir is None:
        rules_dir = _DEFAULT_RULES_DIR

    skill_registry = SkillRegistry(skills_dir)
    try:
        skill = skill_registry.select_skill(request.tier, request.format)
    except Exception as e:
        return CreateResult(
            error=f"Skill selection failed: {e}",
            execution_mode="none",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    if request.modular:
        result = _create_modular_mode(request, rules_dir)
    elif tier == Tier.SMALL:
        result = _create_template_mode(
            request, skill, skill_registry, templates_dir, rules_dir
        )
    else:
        result = _create_code_mode(request, skill, skill_registry, rules_dir)

    result.duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "[Create][orchestrate][BLOCK_ORCHESTRATE] "
        "Finished create: success=%s, mode=%s, duration=%dms",
        result.success,
        result.execution_mode,
        result.duration_ms,
    )
    return result
# END_BLOCK_ORCHESTRATE


def _call_model(
    request: CreateRequest, skill: SkillRef, skill_registry: SkillRegistry
) -> str:
    from mint.llm import LLMCallError, LLMClient

    base_url = request.llm_base_url
    if not base_url:
        raise ModelCallFailedError(
            "No model response provided and no LLM_BASE_URL configured. "
            "Set model_response_override or configure LLM endpoint."
        )

    skill_prompt = skill_registry.render_prompt(skill, request.design_tokens)

    code_or_json = (
        "JavaScript code using docx-js"
        if request.format == "docx"
        else "JavaScript code using pptxgenjs"
        if request.format == "pptx"
        else "JSON content"
    )
    if request.tier != "small" and request.format == "docx":
        sandbox_note = (
            "CRITICAL RULES:\n"
            "1. Do NOT use import/require/fs. ALL docx-js classes are pre-loaded as globals.\n"
            "2. Use writeFileSync('output.docx', buffer) to save. "
            "NOT fs.writeFileSync, NOT writeFile.\n"
            "3. Tables MUST use new Table({ rows: [...] }). "
            "NEVER put TableRow directly in sections.children.\n"
            "4. TableCell width MUST be an object: "
            "{ size: NUMBER, type: WidthType.DXA }. Never a bare number.\n"
            "5. Shading: use { fill: 'COLOR', type: ShadingType.CLEAR }. "
            "NEVER use type: 'solid' (causes black background).\n"
            "6. Do NOT wrap code in async IIFE (runtime does this).\n"
            "7. For line breaks use TextRun({ break: 1 }) NOT LineBreak.\n"
            "8. Save: writeFileSync('output.docx', buffer).\n"
            "9. Return ONLY raw JavaScript code, no markdown fences."
        )
    elif request.tier != "small" and request.format == "pptx":
        sandbox_note = (
            "CRITICAL RULES:\n"
            "1. Do NOT use import/require. Pre-loaded globals: pptxgen, "
            "writeFileSync.\n"
            "2. Do NOT use docx-js classes (Document, Paragraph, etc). "
            "Use ONLY pptxgenjs API.\n"
            "3. Do NOT wrap code in async IIFE (runtime does this).\n"
            "4. Background: use slide.background = { color: 'FFFFFF' }. "
            "Do NOT use slide.addBackground().\n"
            "5. Save: const buffer = await pptx.write({ outputType: "
            "'nodebuffer' }); writeFileSync('output.pptx', buffer).\n"
            "6. Do NOT use pptx.writeFile().\n"
            "7. Return ONLY raw JavaScript code, no markdown fences."
        )
    else:
        sandbox_note = (
            "Return ONLY a JSON object matching the template placeholders. "
            "No markdown fences, no explanations."
        )
    system_prompt = (
        f"You are a document generation assistant. "
        f"Generate {code_or_json} "
        f"to create a {request.format.upper()} document. "
        f"{sandbox_note}\n\n"
        f"{skill_prompt}"
    )

    client = LLMClient(
        base_url=base_url,
        api_key=request.llm_api_key or "",
        model=request.llm_model or "glm-5",
    )

    try:
        response = client.call(request.prompt, system=system_prompt)
    except LLMCallError as e:
        raise ModelCallFailedError(str(e)) from e

    logger.info(
        "[Create][llm] Model responded: model=%s, tokens=%s, duration=%dms",
        response.model,
        response.usage,
        response.duration_ms,
    )
    return _strip_code_fences(response.text)


def _strip_code_fences(text: str) -> str:
    match = re.search(r"```(?:javascript|js|json)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _evaluate_and_refine(
    request: CreateRequest,
    code: str,
    skill: SkillRef,
) -> str | None:
    from mint.llm import LLMCallError, LLMClient

    base_url = request.llm_base_url
    if not base_url:
        return None

    evaluate_prompt = (
        "You are a senior document design reviewer. Examine the JavaScript code below "
        "that generates a document using docx-js/pptxgenjs.\n\n"
        "Evaluate on these criteria (score 1-10 each):\n"
        "1. TYPOGRAPHY: Are heading and body fonts consistent? Proper sizes? Good hierarchy?\n"
        "2. COLOR SCHEME: Is there a cohesive color palette? Are headers colored? Tables styled?\n"
        "3. SPACING: Proper margins, paragraph spacing, section breaks?\n"
        "4. TABLE DESIGN: Header rows with background color? Alternating row colors? Borders?\n"
        "5. VISUAL HIERARCHY: Clear distinction between heading levels? Professional layout?\n"
        "6. COMPLETENESS: Does the code fully address the user's request?\n\n"
        f"USER REQUEST: {request.prompt}\n\n"
        f"GENERATED CODE:\n```javascript\n{code}\n```\n\n"
        "If ALL scores are 7 or above, respond with exactly: APPROVED\n"
        "If any score is below 7, respond with improved JavaScript code that fixes the issues. "
        "Focus on:\n"
        "- Adding named styles with colors and proper fonts\n"
        "- Table header rows with colored backgrounds (shading: {fill: '1E40AF'})\n"
        "- Alternating row backgrounds\n"
        "- Proper page margins and paragraph spacing\n"
        "- Visual hierarchy with consistent heading sizes\n\n"
        "Return ONLY raw JavaScript code (if improving) or APPROVED (if satisfied). "
        "No markdown fences, no explanations."
    )

    client = LLMClient(
        base_url=base_url,
        api_key=request.llm_api_key or "",
        model=request.llm_model or "glm-5",
    )

    try:
        response = client.call(evaluate_prompt)
    except LLMCallError as e:
        logger.warning("[Create][evaluate] Evaluation failed: %s", e)
        return None

    text = response.text.strip()
    if "APPROVED" in text.upper()[:50]:
        logger.info("[Create][evaluate] Code approved by self-evaluation")
        return None

    refined = _strip_code_fences(text)
    if len(refined) < 50 or "APPROVED" in refined.upper()[:50]:
        logger.info("[Create][evaluate] No actionable refinement provided")
        return None

    logger.info(
        "[Create][evaluate] Refined code received (%d chars → %d chars)",
        len(code),
        len(refined),
    )
    return refined


def _postprocess_docx(
    path: Path, theme: ThemeTokens | None = None
) -> None:
    if path.suffix.lower() != ".docx":
        return

    import io
    import zipfile

    from lxml import etree

    if theme is None:
        theme = load_theme()

    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": w_ns}
    changes = 0
    body_size = theme.typography.style("body").size

    try:
        with zipfile.ZipFile(path, "r") as zin:
            entries = {n: zin.read(n) for n in zin.namelist()}
    except zipfile.BadZipFile:
        return

    def _parse_xml(name: str) -> etree._Element | None:
        raw = entries.get(name)
        if raw is None:
            return None
        try:
            return etree.fromstring(raw)
        except etree.XMLSyntaxError:
            return None

    def _serialize_xml(root: etree._Element) -> bytes:
        return etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )

    # Fix 1: Deduplicate styles
    styles_path = "word/styles.xml"
    styles_root = _parse_xml(styles_path)
    if styles_root is not None:
        seen_ids: set[str] = set()
        to_remove: list[etree._Element] = []
        for style_el in styles_root.findall("w:style", ns):
            sid = style_el.get(f"{{{w_ns}}}styleId")
            if sid is None:
                continue
            if sid in seen_ids:
                to_remove.append(style_el)
                changes += 1
            else:
                seen_ids.add(sid)
        for el in to_remove:
            styles_root.remove(el)
        # Wave H: force theme colors + font on Heading1/2/3 and Normal
        # styles. docx-js writes paragraphStyles entries but Word's built-in
        # Heading style defaults (Accent 1 = 2E74B5) override the run.color
        # we asked for. We rewrite styles.xml directly so themed runs land
        # on every heading.
        styles_changed = False
        primary_hex = theme.palette.primary
        body_hex = theme.palette.body
        default_font = theme.typography.default_font
        for style_el in styles_root.findall(f"{{{w_ns}}}style"):
            sid = style_el.get(f"{{{w_ns}}}styleId", "")
            target_color: str | None = None
            target_size: int | None = None
            if sid in ("Heading1", "Heading2", "Heading3", "Heading4",
                       "Heading5", "Heading6"):
                target_color = primary_hex
                role_map = {
                    "Heading1": "heading1",
                    "Heading2": "heading2",
                    "Heading3": "heading3",
                }
                if sid in role_map:
                    role = role_map[sid]
                    if role in theme.typography.styles:
                        target_size = theme.typography.style(role).size
            elif sid == "Title":
                target_color = primary_hex
                if "title" in theme.typography.styles:
                    target_size = theme.typography.style("title").size
            elif sid == "Normal":
                target_color = body_hex
                if "body" in theme.typography.styles:
                    target_size = theme.typography.style("body").size
            else:
                continue
            rpr = style_el.find(f"{{{w_ns}}}rPr")
            if rpr is None:
                rpr = etree.SubElement(style_el, f"{{{w_ns}}}rPr")
                style_el.insert(0, rpr)
            col = rpr.find(f"{{{w_ns}}}color")
            if col is None:
                col = etree.SubElement(rpr, f"{{{w_ns}}}color")
            col.set(f"{{{w_ns}}}val", target_color)
            font_el = rpr.find(f"{{{w_ns}}}rFonts")
            if font_el is None:
                font_el = etree.SubElement(rpr, f"{{{w_ns}}}rFonts")
            font_el.set(f"{{{w_ns}}}ascii", default_font)
            font_el.set(f"{{{w_ns}}}hAnsi", default_font)
            font_el.set(f"{{{w_ns}}}cs", default_font)
            if target_size is not None:
                sz_el = rpr.find(f"{{{w_ns}}}sz")
                if sz_el is None:
                    sz_el = etree.SubElement(rpr, f"{{{w_ns}}}sz")
                sz_el.set(f"{{{w_ns}}}val", str(target_size))
                szcs_el = rpr.find(f"{{{w_ns}}}szCs")
                if szcs_el is None:
                    szcs_el = etree.SubElement(rpr, f"{{{w_ns}}}szCs")
                szcs_el.set(f"{{{w_ns}}}val", str(target_size))
            styles_changed = True

        if to_remove or styles_changed:
            entries[styles_path] = _serialize_xml(styles_root)
            if styles_changed:
                changes += 1
                logger.info(
                    "[Create][postprocess] Wave H: themed heading/Normal "
                    "styles rewritten in styles.xml"
                )

    # Fix 2: Remove empty comments.xml and its references
    comments_path = "word/comments.xml"
    comments_rels_path = "word/_rels/comments.xml.rels"
    comments_root = _parse_xml(comments_path)
    if comments_root is not None and len(comments_root) == 0:
        entries.pop(comments_path, None)
        entries.pop(comments_rels_path, None)
        changes += 1

        doc_rels_path = "word/_rels/document.xml.rels"
        doc_rels_root = _parse_xml(doc_rels_path)
        if doc_rels_root is not None:
            for rel in list(doc_rels_root):
                target = rel.get("Target", "")
                if target == "comments.xml":
                    doc_rels_root.remove(rel)
            entries[doc_rels_path] = _serialize_xml(doc_rels_root)

        ct_path = "[Content_Types].xml"
        ct_root = _parse_xml(ct_path)
        if ct_root is not None:
            for override in list(ct_root):
                part = override.get("PartName", "")
                if part == "/word/comments.xml":
                    ct_root.remove(override)
            entries[ct_path] = _serialize_xml(ct_root)

    # Fix 3 + Fix 4: Table layout + font sizes (single parse of document.xml)
    doc_path = "word/document.xml"
    doc_root = _parse_xml(doc_path)
    doc_dirty = False
    if doc_root is not None:
        for tbl in doc_root.iter(f"{{{w_ns}}}tbl"):
            tbl_pr = tbl.find(f"{{{w_ns}}}tblPr")
            if tbl_pr is None:
                tbl_pr = etree.SubElement(tbl, f"{{{w_ns}}}tblPr")
                tbl.insert(0, tbl_pr)

            if tbl_pr.find(f"{{{w_ns}}}tblLayout") is None:
                layout_el = etree.SubElement(tbl_pr, f"{{{w_ns}}}tblLayout")
                layout_el.set(f"{{{w_ns}}}type", "fixed")
                changes += 1

            if tbl_pr.find(f"{{{w_ns}}}tblLook") is None:
                look_el = etree.SubElement(tbl_pr, f"{{{w_ns}}}tblLook")
                look_el.set(f"{{{w_ns}}}val", "04A0")
                look_el.set(f"{{{w_ns}}}firstRow", "1")
                look_el.set(f"{{{w_ns}}}lastRow", "0")
                look_el.set(f"{{{w_ns}}}firstColumn", "1")
                look_el.set(f"{{{w_ns}}}lastColumn", "0")
                look_el.set(f"{{{w_ns}}}noHBand", "0")
                look_el.set(f"{{{w_ns}}}noVBand", "1")
                changes += 1

        for sz_el in doc_root.iter(f"{{{w_ns}}}sz"):
            val = sz_el.get(f"{{{w_ns}}}val")
            if val and val.isdigit():
                int_val = int(val)
                if int_val > 200:
                    sz_el.set(f"{{{w_ns}}}val", str(int_val // 100))
                    changes += 1
                elif int_val == 1:
                    sz_el.set(f"{{{w_ns}}}val", str(body_size))
                    changes += 1
        for sz_cs_el in doc_root.iter(f"{{{w_ns}}}szCs"):
            val = sz_cs_el.get(f"{{{w_ns}}}val")
            if val and val.isdigit():
                int_val = int(val)
                if int_val > 200:
                    sz_cs_el.set(f"{{{w_ns}}}val", str(int_val // 100))
                    changes += 1

        doc_dirty = changes > 0
        if doc_dirty:
            entries[doc_path] = _serialize_xml(doc_root)

    # Fix 4b: Strip problematic Unicode characters that don't render in
    # common readers. Models sometimes emit U+2011 (non-breaking hyphen),
    # U+00AD (soft hyphen), U+200B (zero-width space), U+200C/D (joiners),
    # U+FEFF (BOM). soffice/Pages render these as black squares.
    raw_doc = entries.get(doc_path, b"")
    if raw_doc:
        problem_chars = {
            "\u2011": "-",  # non-breaking hyphen
            "\u00ad": "",   # soft hyphen
            "\u200b": "",   # zero-width space
            "\u200c": "",   # zero-width non-joiner
            "\u200d": "",   # zero-width joiner
            "\ufeff": "",   # byte-order mark
            "\u2028": " ",  # line separator
            "\u2029": " ",  # paragraph separator
        }
        decoded = raw_doc.decode("utf-8", errors="replace")
        replaced = decoded
        unicode_changes = 0
        for bad, good in problem_chars.items():
            count_in = replaced.count(bad)
            if count_in:
                replaced = replaced.replace(bad, good)
                unicode_changes += count_in
        if unicode_changes:
            entries[doc_path] = replaced.encode("utf-8")
            changes += unicode_changes
            logger.info(
                "[Create][postprocess] Replaced %d problematic unicode chars",
                unicode_changes,
            )

    # Fix 5: Strip invalid XML tags from malformed LLM output (raw bytes)
    raw_doc = entries.get(doc_path, b"")
    if raw_doc:
        import re as _re
        fixed, count = _re.subn(
            rb"<[0-9]+/>",
            b"",
            raw_doc,
        )
        if count > 0:
            entries[doc_path] = fixed
            changes += count
            logger.info(
                "[Create][postprocess] Removed %d invalid XML tags", count
            )

    # Fix 5b + 5c + 6: TOC, cover-title cap, table widths (single re-parse)
    doc_root = _parse_xml(doc_path)
    doc_dirty = False
    if doc_root is not None:
        raw_doc = entries.get(doc_path, b"")
        has_toc = raw_doc and b"TOC" in raw_doc

        if has_toc:
            # We've populated the TOC with static H1/H2 entries below the
            # field, so Word doesn't NEED to refresh. Setting <w:updateFields>
            # combined with dirty=true and the \h switch on the TOC field
            # makes Word show a "fields may refer to other files" warning
            # on open. Strip both so the doc opens silently.
            settings_path = "word/settings.xml"
            settings_root = _parse_xml(settings_path)
            if settings_root is not None:
                upd = settings_root.find(f"{{{w_ns}}}updateFields")
                if upd is not None:
                    settings_root.remove(upd)
                    entries[settings_path] = _serialize_xml(settings_root)
                    changes += 1

            # Strip w:dirty="true" from any TOC fldChar so Word doesn't
            # try to auto-update on open.
            for fc in doc_root.iter(f"{{{w_ns}}}fldChar"):
                if fc.get(f"{{{w_ns}}}dirty") == "true":
                    del fc.attrib[f"{{{w_ns}}}dirty"]
                    changes += 1

            toc_separate_p = None
            for p in doc_root.iter(f"{{{w_ns}}}p"):
                instr = p.find(f".//{{{w_ns}}}instrText")
                if instr is not None and instr.text and "TOC" in instr.text:
                    toc_separate_p = p
                    break
            if toc_separate_p is not None:
                # Idempotency guard: if a paragraph immediately following
                # the TOC field already has the injected-entry shape
                # (tabs with dot-leader), the TOC was already populated by
                # a prior postprocess pass — skip re-injecting to avoid
                # duplicate entries.
                already_injected = False
                parent_check = toc_separate_p.getparent()
                if parent_check is not None:
                    children = list(parent_check)
                    try:
                        idx_check = children.index(toc_separate_p)
                    except ValueError:
                        idx_check = -1
                    for sib in children[idx_check + 1: idx_check + 6]:
                        if sib.tag != f"{{{w_ns}}}p":
                            continue
                        leader_tab = sib.find(
                            f"{{{w_ns}}}pPr/{{{w_ns}}}tabs/{{{w_ns}}}tab"
                        )
                        if (
                            leader_tab is not None
                            and leader_tab.get(f"{{{w_ns}}}leader") == "dot"
                        ):
                            already_injected = True
                            break

            if toc_separate_p is not None and not already_injected:
                tocs: list[tuple[int, str]] = []
                seen_toc = False
                for p in doc_root.iter(f"{{{w_ns}}}p"):
                    if p is toc_separate_p:
                        seen_toc = True
                        continue
                    if not seen_toc:
                        continue
                    p_style = p.find(f"{{{w_ns}}}pPr/{{{w_ns}}}pStyle")
                    if p_style is None:
                        continue
                    style_id = p_style.get(f"{{{w_ns}}}val", "")
                    if style_id not in ("Heading1", "Heading2"):
                        continue
                    text = "".join(
                        (t.text or "") for t in p.iter(f"{{{w_ns}}}t")
                    ).strip()
                    if not text:
                        continue
                    level = 1 if style_id == "Heading1" else 2
                    tocs.append((level, text))

                if tocs:
                    parent = toc_separate_p.getparent()
                    if parent is not None:
                        idx = list(parent).index(toc_separate_p) + 1
                        # Page width 9360 dxa minus left indent of entry.
                        # Right tab stop at 9000 to leave a small right margin.
                        right_tab_pos = 9000
                        # Estimate page numbers: assume ~3 paragraphs per H1
                        # section. Real PAGEREF would be better but requires
                        # bookmarks; static estimate is sufficient for VLM
                        # checks and gives users a usable preview.
                        running_page = 3  # cover + TOC + first content page
                        last_h1_page = running_page
                        for level, text in tocs:
                            if level == 1:
                                last_h1_page += 2
                                page_no = last_h1_page
                            else:
                                page_no = last_h1_page
                            indent_left = (level - 1) * 360
                            new_p = etree.Element(f"{{{w_ns}}}p")
                            ppr = etree.SubElement(new_p, f"{{{w_ns}}}pPr")
                            tabs = etree.SubElement(ppr, f"{{{w_ns}}}tabs")
                            tab = etree.SubElement(tabs, f"{{{w_ns}}}tab")
                            tab.set(f"{{{w_ns}}}val", "right")
                            tab.set(f"{{{w_ns}}}leader", "dot")
                            tab.set(f"{{{w_ns}}}pos", str(right_tab_pos))
                            ind = etree.SubElement(ppr, f"{{{w_ns}}}ind")
                            ind.set(f"{{{w_ns}}}left", str(indent_left))
                            spacing = etree.SubElement(ppr, f"{{{w_ns}}}spacing")
                            spacing.set(f"{{{w_ns}}}after", "60")
                            # Run with title text
                            r_el = etree.SubElement(new_p, f"{{{w_ns}}}r")
                            t_el = etree.SubElement(r_el, f"{{{w_ns}}}t")
                            t_el.text = text
                            # Tab + page number run
                            tab_r = etree.SubElement(new_p, f"{{{w_ns}}}r")
                            tab_el = etree.SubElement(tab_r, f"{{{w_ns}}}tab")
                            _ = tab_el  # element placement only
                            page_r = etree.SubElement(new_p, f"{{{w_ns}}}r")
                            page_t = etree.SubElement(page_r, f"{{{w_ns}}}t")
                            page_t.text = str(page_no)
                            parent.insert(idx, new_p)
                            idx += 1
                            changes += 1
                        doc_dirty = True

        first_h1 = None
        for p in doc_root.iter(f"{{{w_ns}}}p"):
            text = "".join((t.text or "") for t in p.iter(f"{{{w_ns}}}t"))
            if text.strip():
                first_h1 = p
                break
        if first_h1 is not None:
            cover_changes = 0
            cap = theme.cover.title_size_cap
            cap_str = str(cap)
            for sz in first_h1.iter(f"{{{w_ns}}}sz"):
                v = sz.get(f"{{{w_ns}}}val")
                if v and v.isdigit() and int(v) > cap:
                    sz.set(f"{{{w_ns}}}val", cap_str)
                    cover_changes += 1
            for sz in first_h1.iter(f"{{{w_ns}}}szCs"):
                v = sz.get(f"{{{w_ns}}}val")
                if v and v.isdigit() and int(v) > cap:
                    sz.set(f"{{{w_ns}}}val", cap_str)
                    cover_changes += 1
            if cover_changes > 0:
                doc_dirty = True
                changes += cover_changes
                logger.info(
                    "[Create][postprocess] Capped %d cover-title sizes to 28pt",
                    cover_changes,
                )

        # Wave G2: cap oversized body run sizes. Cover-title gets a
        # special cap to 56 (28pt) above; for all OTHER paragraphs, any
        # run with sz > 24 (12pt) is capped to 22 (11pt). This catches
        # model-emitted code blocks at sz=36 (18pt) which look gigantic.
        # Headings rely on pStyle (no run-level sz), so they're unaffected.
        body_size_changes = 0
        body_cap = body_size + 2
        body_size_str = str(body_size)
        first_p_with_text = None
        for p in doc_root.iter(f"{{{w_ns}}}p"):
            text = "".join((t.text or "") for t in p.iter(f"{{{w_ns}}}t"))
            if text.strip():
                first_p_with_text = p
                break
        for p in doc_root.iter(f"{{{w_ns}}}p"):
            if p is first_p_with_text:
                continue
            for sz in p.iter(f"{{{w_ns}}}sz"):
                v = sz.get(f"{{{w_ns}}}val")
                if v and v.isdigit() and int(v) > body_cap:
                    sz.set(f"{{{w_ns}}}val", body_size_str)
                    body_size_changes += 1
            for sz in p.iter(f"{{{w_ns}}}szCs"):
                v = sz.get(f"{{{w_ns}}}val")
                if v and v.isdigit() and int(v) > body_cap:
                    sz.set(f"{{{w_ns}}}val", body_size_str)
                    body_size_changes += 1
        if body_size_changes > 0:
            doc_dirty = True
            changes += body_size_changes
            logger.info(
                "[Create][postprocess] Capped %d oversized body runs to 11pt",
                body_size_changes,
            )

        table_changes = 0
        for tbl in doc_root.iter(f"{{{w_ns}}}tbl"):
            grid = tbl.find(f"{{{w_ns}}}tblGrid")
            if grid is None:
                continue
            grid_widths: list[int] = []
            for gc in grid.findall(f"{{{w_ns}}}gridCol"):
                w_attr = gc.get(f"{{{w_ns}}}w")
                if w_attr and w_attr.isdigit():
                    grid_widths.append(int(w_attr))
            if not grid_widths:
                continue
            total = sum(grid_widths)
            tbl_pr = tbl.find(f"{{{w_ns}}}tblPr")
            if tbl_pr is not None:
                tbl_w = tbl_pr.find(f"{{{w_ns}}}tblW")
                if tbl_w is not None:
                    cur_type = tbl_w.get(f"{{{w_ns}}}type", "dxa")
                    cur_w = tbl_w.get(f"{{{w_ns}}}w", "0")
                    if cur_type == "dxa" and cur_w != str(total):
                        tbl_w.set(f"{{{w_ns}}}w", str(total))
                        table_changes += 1
                    elif cur_type == "pct":
                        tbl_w.set(f"{{{w_ns}}}type", "dxa")
                        tbl_w.set(f"{{{w_ns}}}w", str(total))
                        table_changes += 1
            for tr in tbl.findall(f"{{{w_ns}}}tr"):
                cells = tr.findall(f"{{{w_ns}}}tc")
                if len(cells) != len(grid_widths):
                    continue
                for cell, gw in zip(cells, grid_widths, strict=False):
                    tc_pr = cell.find(f"{{{w_ns}}}tcPr")
                    if tc_pr is None:
                        continue
                    span = tc_pr.find(f"{{{w_ns}}}gridSpan")
                    if span is not None:
                        continue
                    tc_w = tc_pr.find(f"{{{w_ns}}}tcW")
                    if tc_w is None:
                        continue
                    cur_type = tc_w.get(f"{{{w_ns}}}type", "dxa")
                    cur_w = tc_w.get(f"{{{w_ns}}}w", "0")
                    if cur_type == "dxa" and cur_w != str(gw):
                        tc_w.set(f"{{{w_ns}}}w", str(gw))
                        table_changes += 1
        if table_changes > 0:
            doc_dirty = True
            changes += table_changes
            logger.info(
                "[Create][postprocess] Reconciled %d table-width entries",
                table_changes,
            )

        if doc_dirty:
            entries[doc_path] = _serialize_xml(doc_root)

    # Fix 7 (Wave F): paragraph spacing default. Body paragraphs without
    # explicit spacing.after look cramped together. Inject default 6pt
    # (after=120 dxa) on every body paragraph; 12pt before on heading
    # paragraphs. Skipped if paragraph already has spacing element.
    doc_root = _parse_xml(doc_path)
    if doc_root is not None:
        spacing_added = 0
        for p in doc_root.iter(f"{{{w_ns}}}p"):
            text = "".join((t.text or "") for t in p.iter(f"{{{w_ns}}}t"))
            if not text.strip():
                continue
            spacing_ppr = p.find(f"{{{w_ns}}}pPr")
            if spacing_ppr is None:
                spacing_ppr = etree.SubElement(p, f"{{{w_ns}}}pPr")
                p.insert(0, spacing_ppr)
            existing = spacing_ppr.find(f"{{{w_ns}}}spacing")
            if existing is not None:
                continue
            pstyle = spacing_ppr.find(f"{{{w_ns}}}pStyle")
            style_id = (
                pstyle.get(f"{{{w_ns}}}val", "") if pstyle is not None else ""
            )
            spacing_el = etree.SubElement(spacing_ppr, f"{{{w_ns}}}spacing")
            if style_id.startswith("Heading"):
                spacing_el.set(
                    f"{{{w_ns}}}before", str(theme.paragraph.heading_before)
                )
                spacing_el.set(
                    f"{{{w_ns}}}after", str(theme.paragraph.heading_after)
                )
            else:
                spacing_el.set(
                    f"{{{w_ns}}}after", str(theme.paragraph.body_after)
                )
                spacing_el.set(
                    f"{{{w_ns}}}line", str(theme.paragraph.body_line)
                )
                spacing_el.set(
                    f"{{{w_ns}}}lineRule", theme.paragraph.body_line_rule
                )
            spacing_added += 1
        if spacing_added:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += spacing_added
            logger.info(
                "[Create][postprocess] Added spacing on %d paragraphs",
                spacing_added,
            )

    # Wave I: collapse forced section breaks. Each docx-js Section in
    # M-ASSEMBLE becomes a <w:sectPr> with default type (nextPage), forcing
    # a page break between every conceptual section. The result feels like
    # 10 disjoint pages instead of one flowing document. We rewrite all
    # mid-document sectPr to type="continuous" so content flows on the
    # same page when it fits — page breaks happen only on actual overflow.
    #
    # Exceptions:
    #   - The FIRST sectPr ends the cover section: leave as nextPage so the
    #     cover sits on its own page.
    #   - The FINAL sectPr (top-level body child) governs the overall
    #     document — leave alone (it's the doc-final config).
    #
    # Wave I.2: unify header/footer references. With per-section headers
    # (header1.xml..header7.xml all containing the same running header),
    # LibreOffice picks the SECTION's header for each page, and continuous
    # sections that don't have an explicit headerReference inherit
    # inconsistently — causing the running header to disappear on body
    # pages mid-document. Fix: rewrite every non-cover sectPr to point at
    # the SAME first header/footer rId so every body page renders the
    # same running header.
    doc_root = _parse_xml(doc_path)
    if doc_root is not None:
        sect_prs = doc_root.findall(f".//{{{w_ns}}}sectPr")
        body_for_sect = doc_root.find(f"{{{w_ns}}}body")
        # final sectPr is the one that's a direct child of <w:body>
        final_sect_pr = None
        for sp in sect_prs:
            if sp.getparent() is body_for_sect:
                final_sect_pr = sp
        sect_changes = 0
        for idx, sp in enumerate(sect_prs):
            if sp is final_sect_pr:
                continue
            if idx == 0:
                continue  # cover end-of-section keeps nextPage
            type_el = sp.find(f"{{{w_ns}}}type")
            if type_el is None:
                type_el = etree.Element(f"{{{w_ns}}}type")
                sp.insert(0, type_el)
            if type_el.get(f"{{{w_ns}}}val") != "continuous":
                type_el.set(f"{{{w_ns}}}val", "continuous")
                sect_changes += 1
        if sect_changes:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += sect_changes
            logger.info(
                "[Create][postprocess] Wave I: %d sectPr → continuous",
                sect_changes,
            )

        # Wave I.2: unify header/footer rIds across non-cover sections.
        rel_ns = (
            "http://schemas.openxmlformats.org/"
            "officeDocument/2006/relationships"
        )
        first_hdr_rid: str | None = None
        first_ftr_rid: str | None = None
        for sp in sect_prs:
            if first_hdr_rid is None:
                hr = sp.find(f"{{{w_ns}}}headerReference")
                if hr is not None:
                    first_hdr_rid = hr.get(f"{{{rel_ns}}}id")
            if first_ftr_rid is None:
                fr = sp.find(f"{{{w_ns}}}footerReference")
                if fr is not None:
                    first_ftr_rid = fr.get(f"{{{rel_ns}}}id")
            if first_hdr_rid and first_ftr_rid:
                break

        unified_changes = 0
        if first_hdr_rid or first_ftr_rid:
            for idx, sp in enumerate(sect_prs):
                if idx == 0:
                    continue  # cover keeps no header/footer
                # Wipe existing references and inject the unified pair.
                for ref in list(
                    sp.findall(f"{{{w_ns}}}headerReference")
                    + sp.findall(f"{{{w_ns}}}footerReference")
                ):
                    sp.remove(ref)
                if first_hdr_rid:
                    hr_el = etree.SubElement(
                        sp, f"{{{w_ns}}}headerReference"
                    )
                    hr_el.set(f"{{{w_ns}}}type", "default")
                    hr_el.set(f"{{{rel_ns}}}id", first_hdr_rid)
                    # headerReference must come before <w:type> per
                    # OOXML schema; reorder by moving to front.
                    sp.remove(hr_el)
                    sp.insert(0, hr_el)
                if first_ftr_rid:
                    fr_el = etree.SubElement(
                        sp, f"{{{w_ns}}}footerReference"
                    )
                    fr_el.set(f"{{{w_ns}}}type", "default")
                    fr_el.set(f"{{{rel_ns}}}id", first_ftr_rid)
                    sp.remove(fr_el)
                    sp.insert(1 if first_hdr_rid else 0, fr_el)
                unified_changes += 1
        if unified_changes:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += unified_changes
            logger.info(
                "[Create][postprocess] Wave I.2: unified %d sectPr to "
                "header=%s footer=%s",
                unified_changes,
                first_hdr_rid,
                first_ftr_rid,
            )

        # Wave I.4: cap excessive after-spacing on cover paragraphs.
        # The cover JS uses spacing.after=7200 (5 inches) on the tagline
        # to push the metadata footer toward the bottom — but the value
        # is so large that the footer overflows to page 2 instead. Cap
        # any after-spacing above 4000 dxa on cover paragraphs to 2880.
        cover_cap_changes = 0
        cover_section_found = False
        body_iter = list(body_for_sect) if body_for_sect is not None else []
        for el in body_iter:
            if el.tag == f"{{{w_ns}}}p":
                # Stop scanning once we leave the cover (i.e. once we hit
                # the paragraph containing sectPr 0).
                inner_sect = el.find(f"{{{w_ns}}}pPr/{{{w_ns}}}sectPr")
                if inner_sect is not None:
                    cover_section_found = True
                cover_ppr = el.find(f"{{{w_ns}}}pPr")
                if cover_ppr is not None:
                    sp_el = cover_ppr.find(f"{{{w_ns}}}spacing")
                    if sp_el is not None:
                        after_v = sp_el.get(f"{{{w_ns}}}after", "")
                        if after_v.isdigit() and int(after_v) > 4000:
                            sp_el.set(f"{{{w_ns}}}after", "2880")
                            cover_cap_changes += 1
                if cover_section_found:
                    break
        if cover_cap_changes:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += cover_cap_changes
            logger.info(
                "[Create][postprocess] Wave I.4: capped %d cover "
                "after-spacing values to 2880",
                cover_cap_changes,
            )

        # Wave I.3: delete redundant intermediate sectPr. After Wave I+I.2
        # they all carry the same continuous type and the same header/
        # footer — i.e., they're effectively no-ops. But LibreOffice
        # still rebinds page-headers per intermediate sectPr boundary,
        # which causes the running header to vanish on body pages mid-
        # document. Keep just two sections in the whole doc:
        #   - sectPr 0 (in cover paragraph): ends the cover.
        #   - final body sectPr: governs the rest.
        # Drop everything between by removing the parent paragraph of
        # those intermediate sectPrs. Each intermediate sectPr lives
        # inside a <w:p><w:pPr><w:sectPr/></w:pPr></w:p> hand-off
        # paragraph emitted by docx-js per Section — these paragraphs
        # contribute no visible content, so deleting them is safe.
        delete_changes = 0
        for idx, sp in enumerate(sect_prs):
            if idx == 0:
                continue
            if sp is final_sect_pr:
                continue
            # sp is inside a p/pPr. Remove the whole containing paragraph.
            ppr_parent = sp.getparent()
            if ppr_parent is None:
                continue
            p_parent = ppr_parent.getparent()
            if p_parent is None:
                continue
            grandparent = p_parent.getparent()
            if grandparent is None:
                continue
            # Sanity: only delete the wrapper paragraph if it has nothing
            # but pPr/sectPr — never blow away real content.
            visible_runs = [
                r for r in p_parent.iter(f"{{{w_ns}}}r")
            ]
            visible_text = "".join(
                (t.text or "") for t in p_parent.iter(f"{{{w_ns}}}t")
            ).strip()
            if visible_runs and visible_text:
                # Has real content — strip just the sectPr instead.
                ppr_parent.remove(sp)
            else:
                grandparent.remove(p_parent)
            delete_changes += 1
        if delete_changes:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += delete_changes
            logger.info(
                "[Create][postprocess] Wave I.3: removed %d redundant "
                "intermediate sectPr(s)",
                delete_changes,
            )

        # Wave I.5: the FINAL body sectPr governs the body section. Wave I
        # forced it to `continuous`, which is wrong for the document-last
        # sectPr — there is no next section to be continuous with, and
        # LibreOffice reads `continuous` as "this section IS the previous
        # one", which causes the body section's headerReference to be
        # ignored and the running header to vanish on body pages. Drop the
        # type so it falls back to default (nextPage) and the body section
        # owns its own header/footer chrome again.
        final_type_dropped = False
        if final_sect_pr is not None:
            type_el = final_sect_pr.find(f"{{{w_ns}}}type")
            if (
                type_el is not None
                and type_el.get(f"{{{w_ns}}}val") == "continuous"
            ):
                final_sect_pr.remove(type_el)
                final_type_dropped = True
        if final_type_dropped:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += 1
            logger.info(
                "[Create][postprocess] Wave I.5: dropped continuous type "
                "from final body sectPr"
            )

    # Fix 7c (Wave G): keepNext on heading paragraphs that immediately
    # precede a table or another paragraph. Prevents orphan tables on
    # the next page when a heading lands at the bottom margin.
    doc_root = _parse_xml(doc_path)
    if doc_root is not None:
        keepnext_added = 0
        body = doc_root.find(f"{{{w_ns}}}body")
        if body is not None:
            children = list(body)
            for idx, el in enumerate(children):
                if el.tag != f"{{{w_ns}}}p":
                    continue
                head_ppr = el.find(f"{{{w_ns}}}pPr")
                if head_ppr is None:
                    continue
                pstyle = head_ppr.find(f"{{{w_ns}}}pStyle")
                style_id = (
                    pstyle.get(f"{{{w_ns}}}val", "") if pstyle is not None else ""
                )
                if not style_id.startswith("Heading"):
                    continue
                if idx + 1 >= len(children):
                    continue
                if head_ppr.find(f"{{{w_ns}}}keepNext") is None:
                    etree.SubElement(head_ppr, f"{{{w_ns}}}keepNext")
                    keepnext_added += 1
                if head_ppr.find(f"{{{w_ns}}}keepLines") is None:
                    etree.SubElement(head_ppr, f"{{{w_ns}}}keepLines")
        if keepnext_added:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += keepnext_added
            logger.info(
                "[Create][postprocess] Added keepNext on %d headings",
                keepnext_added,
            )

    # Wave J: strip paragraph shading that doesn't belong to the active
    # theme. The model often emits hardcoded hex fills (e.g. EBF5FB from
    # showcase note-callout, D5E8F0, F0F8FF, etc) that don't match the
    # current theme — under claret_serif a stray light-blue background on
    # bullet lists looks broken. Allowed fills are: theme palette colors,
    # alt-row, callout fills, and structural ones (auto/FFFFFF).
    allowed_fills = {
        "AUTO", "FFFFFF",
        theme.palette.primary,
        theme.palette.body,
        theme.palette.alt_row,
        theme.tables.alt_row_fill,
        theme.tables.header.fill or "",
    }
    for cp in theme.palette.callouts.values():
        allowed_fills.add(cp.fill)
        allowed_fills.add(cp.border)
    allowed_fills.discard("")
    allowed_fills_norm = {a.upper() for a in allowed_fills}

    doc_root = _parse_xml(doc_path)
    if doc_root is not None:
        stripped = 0
        for p in doc_root.iter(f"{{{w_ns}}}p"):
            strip_ppr = p.find(f"{{{w_ns}}}pPr")
            if strip_ppr is None:
                continue
            strip_shd = strip_ppr.find(f"{{{w_ns}}}shd")
            if strip_shd is None:
                continue
            fill = (strip_shd.get(f"{{{w_ns}}}fill", "") or "").upper()
            if not fill or fill in allowed_fills_norm:
                continue
            # Foreign fill: strip it. If postprocess Wave C decides this
            # paragraph is a callout (Note:/Warning:/...), it will set the
            # right theme fill back later in this same pass.
            strip_ppr.remove(strip_shd)
            stripped += 1
        if stripped:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += stripped
            logger.info(
                "[Create][postprocess] Wave J: stripped %d foreign "
                "paragraph fills (not in theme palette)",
                stripped,
            )

    # Fix 7b (Wave C): convert "Warning:", "Note:", "Tip:", "Caution:"
    # prefixed paragraphs into distinct-coloured callout blocks. Gives the
    # doc varied semantic emphasis (info / warning / tip) without requiring
    # the model to write the right border+shading by hand.
    callout_styles = {
        kind: (palette.border, palette.fill)
        for kind, palette in theme.palette.callouts.items()
    }
    callout_pat = re.compile(
        r"^\s*(Warning|Note|Tip|Caution|Important)\s*[:—-]",
        re.IGNORECASE,
    )
    doc_root = _parse_xml(doc_path)
    if doc_root is not None:
        callout_changes = 0
        for p in doc_root.iter(f"{{{w_ns}}}p"):
            text = "".join(
                (t.text or "") for t in p.iter(f"{{{w_ns}}}t")
            )
            m = callout_pat.match(text)
            if not m:
                continue
            kind = m.group(1).lower()
            if kind == "important":
                kind = "warning"
            border_color, fill = callout_styles[kind]
            callout_ppr = p.find(f"{{{w_ns}}}pPr")
            if callout_ppr is None:
                callout_ppr = etree.SubElement(p, f"{{{w_ns}}}pPr")
                p.insert(0, callout_ppr)
            # skip if already styled as a callout
            if callout_ppr.find(f"{{{w_ns}}}pBdr") is not None:
                continue
            pbdr = etree.SubElement(callout_ppr, f"{{{w_ns}}}pBdr")
            left = etree.SubElement(pbdr, f"{{{w_ns}}}left")
            left.set(f"{{{w_ns}}}val", "single")
            left.set(f"{{{w_ns}}}sz", str(theme.callout_layout.border_width))
            left.set(f"{{{w_ns}}}space", str(theme.callout_layout.border_space))
            left.set(f"{{{w_ns}}}color", border_color)
            shd_target = callout_ppr.find(f"{{{w_ns}}}shd")
            if shd_target is None:
                shd_target = etree.SubElement(callout_ppr, f"{{{w_ns}}}shd")
            shd_target.set(f"{{{w_ns}}}val", "clear")
            shd_target.set(f"{{{w_ns}}}color", "auto")
            shd_target.set(f"{{{w_ns}}}fill", fill)
            ind_existing = callout_ppr.find(f"{{{w_ns}}}ind")
            if ind_existing is None:
                ind_new = etree.SubElement(callout_ppr, f"{{{w_ns}}}ind")
                ind_new.set(
                    f"{{{w_ns}}}left", str(theme.callout_layout.indent_left)
                )
            callout_changes += 1
        if callout_changes:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += callout_changes
            logger.info(
                "[Create][postprocess] Styled %d paragraphs as callouts",
                callout_changes,
            )

    # Fix 8a (Wave G): aggressive table-style normalization to match the
    # docs/docx_showcase.docx reference design system. Applied on every table
    # regardless of what the model emitted.
    #
    # Per-table:
    #   - tblW = 9360 DXA (full content width)
    #   - tblBorders = thin grey (single, sz 4, color DDDDDD)
    #   - tblCellMar default = top/bottom 80, left/right 120
    #   - gridCol widths redistributed to sum to 9360
    # Per row:
    #   - First row (header): tblHeader marker
    # Per cell:
    #   - tcW from grid
    #   - tcMar = top/bottom 80, left/right 120
    #   - Header cells: shading fill 1B3A5C, runs colored FFFFFF + bold
    #   - Body cells: runs colored 333333
    primary_dark = theme.tables.header.fill or theme.palette.primary
    primary_text = theme.tables.header.text
    body_text = theme.tables.body.text
    border_grey = theme.tables.borders.color
    border_size_str = str(theme.tables.borders.size)
    cell_mar = theme.tables.cell_margins
    target_width = theme.tables.target_width_dxa
    doc_root = _parse_xml(doc_path)
    if doc_root is not None:
        table_norm_changes = 0
        for tbl in doc_root.iter(f"{{{w_ns}}}tbl"):
            tbl_pr = tbl.find(f"{{{w_ns}}}tblPr")
            if tbl_pr is None:
                tbl_pr = etree.Element(f"{{{w_ns}}}tblPr")
                tbl.insert(0, tbl_pr)

            tbl_w = tbl_pr.find(f"{{{w_ns}}}tblW")
            if tbl_w is None:
                tbl_w = etree.SubElement(tbl_pr, f"{{{w_ns}}}tblW")
            tbl_w.set(f"{{{w_ns}}}w", str(target_width))
            tbl_w.set(f"{{{w_ns}}}type", "dxa")

            existing_borders = tbl_pr.find(f"{{{w_ns}}}tblBorders")
            if existing_borders is not None:
                tbl_pr.remove(existing_borders)
            tbl_borders = etree.SubElement(tbl_pr, f"{{{w_ns}}}tblBorders")
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                el = etree.SubElement(tbl_borders, f"{{{w_ns}}}{side}")
                el.set(f"{{{w_ns}}}val", "single")
                el.set(f"{{{w_ns}}}sz", border_size_str)
                el.set(f"{{{w_ns}}}color", border_grey)

            existing_cellmar = tbl_pr.find(f"{{{w_ns}}}tblCellMar")
            if existing_cellmar is not None:
                tbl_pr.remove(existing_cellmar)
            tbl_cell_mar = etree.SubElement(tbl_pr, f"{{{w_ns}}}tblCellMar")
            cell_mar_pairs = (
                ("top", str(cell_mar.top)),
                ("left", str(cell_mar.left)),
                ("bottom", str(cell_mar.bottom)),
                ("right", str(cell_mar.right)),
            )
            for side, val in cell_mar_pairs:
                mar_side = etree.SubElement(tbl_cell_mar, f"{{{w_ns}}}{side}")
                mar_side.set(f"{{{w_ns}}}w", val)
                mar_side.set(f"{{{w_ns}}}type", "dxa")

            tbl_grid = tbl.find(f"{{{w_ns}}}tblGrid")
            grid_widths = []
            if tbl_grid is not None:
                grid_cols = tbl_grid.findall(f"{{{w_ns}}}gridCol")
                widths: list[int] = []
                for gc in grid_cols:
                    w_attr = gc.get(f"{{{w_ns}}}w", "0") or "0"
                    try:
                        widths.append(int(w_attr))
                    except ValueError:
                        widths.append(0)
                if widths and sum(widths) != target_width:
                    if sum(widths) <= 0:
                        n = len(widths)
                        each = target_width // n
                        widths = [each] * (n - 1) + [target_width - each * (n - 1)]
                    else:
                        old_total = sum(widths)
                        scaled = [int(w * target_width / old_total) for w in widths]
                        scaled[-1] += target_width - sum(scaled)
                        widths = scaled
                    for gc, w in zip(grid_cols, widths, strict=False):
                        gc.set(f"{{{w_ns}}}w", str(w))
                grid_widths = widths

            rows = tbl.findall(f"{{{w_ns}}}tr")
            if not rows:
                table_norm_changes += 1
                continue
            for ri, row in enumerate(rows):
                is_header = ri == 0
                if is_header:
                    row_tr_pr = row.find(f"{{{w_ns}}}trPr")
                    if row_tr_pr is None:
                        row_tr_pr = etree.Element(f"{{{w_ns}}}trPr")
                        row.insert(0, row_tr_pr)
                    if row_tr_pr.find(f"{{{w_ns}}}tblHeader") is None:
                        etree.SubElement(row_tr_pr, f"{{{w_ns}}}tblHeader")
                cells = row.findall(f"{{{w_ns}}}tc")
                for ci, cell in enumerate(cells):
                    cell_tcpr = cell.find(f"{{{w_ns}}}tcPr")
                    if cell_tcpr is None:
                        cell_tcpr = etree.Element(f"{{{w_ns}}}tcPr")
                        cell.insert(0, cell_tcpr)
                    if ci < len(grid_widths):
                        tc_w = cell_tcpr.find(f"{{{w_ns}}}tcW")
                        if tc_w is None:
                            tc_w = etree.SubElement(cell_tcpr, f"{{{w_ns}}}tcW")
                        tc_w.set(f"{{{w_ns}}}w", str(grid_widths[ci]))
                        tc_w.set(f"{{{w_ns}}}type", "dxa")
                    existing_tcmar = cell_tcpr.find(f"{{{w_ns}}}tcMar")
                    if existing_tcmar is not None:
                        cell_tcpr.remove(existing_tcmar)
                    cell_mar_el = etree.SubElement(
                        cell_tcpr, f"{{{w_ns}}}tcMar"
                    )
                    for side, val in cell_mar_pairs:
                        mar_side = etree.SubElement(
                            cell_mar_el, f"{{{w_ns}}}{side}"
                        )
                        mar_side.set(f"{{{w_ns}}}w", val)
                        mar_side.set(f"{{{w_ns}}}type", "dxa")
                    if is_header:
                        cell_shd = cell_tcpr.find(f"{{{w_ns}}}shd")
                        if cell_shd is None:
                            cell_shd = etree.SubElement(cell_tcpr, f"{{{w_ns}}}shd")
                        cell_shd.set(f"{{{w_ns}}}val", "clear")
                        cell_shd.set(f"{{{w_ns}}}color", "auto")
                        cell_shd.set(f"{{{w_ns}}}fill", primary_dark)
                    for run in cell.iter(f"{{{w_ns}}}r"):
                        rpr = run.find(f"{{{w_ns}}}rPr")
                        if rpr is None:
                            rpr = etree.Element(f"{{{w_ns}}}rPr")
                            run.insert(0, rpr)
                        existing_col = rpr.find(f"{{{w_ns}}}color")
                        if existing_col is not None:
                            rpr.remove(existing_col)
                        col_el = etree.SubElement(rpr, f"{{{w_ns}}}color")
                        if is_header:
                            col_el.set(f"{{{w_ns}}}val", primary_text)
                            if theme.tables.header.bold and rpr.find(
                                f"{{{w_ns}}}b"
                            ) is None:
                                etree.SubElement(rpr, f"{{{w_ns}}}b")
                        else:
                            col_el.set(f"{{{w_ns}}}val", body_text)
            table_norm_changes += 1
        if table_norm_changes:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += table_norm_changes
            logger.info(
                "[Create][postprocess] Table normalize: %d tables",
                table_norm_changes,
            )

    # Fix 8 (Wave E): alt-row coloring on tables that don't already have it.
    # Every other body row (skipping header) gets a light fill so tables
    # are easier to scan.
    doc_root = _parse_xml(doc_path)
    if doc_root is not None:
        alt_changes = 0
        alt_fill = theme.tables.alt_row_fill
        for tbl in doc_root.iter(f"{{{w_ns}}}tbl"):
            rows = tbl.findall(f"{{{w_ns}}}tr")
            if len(rows) < 3:
                continue
            existing_pattern = False
            for r in rows[1:]:
                for cell in r.findall(f"{{{w_ns}}}tc"):
                    tcpr = cell.find(f"{{{w_ns}}}tcPr")
                    shd_check = (
                        tcpr.find(f"{{{w_ns}}}shd") if tcpr is not None else None
                    )
                    fill = (
                        shd_check.get(f"{{{w_ns}}}fill", "")
                        if shd_check is not None
                        else ""
                    )
                    if fill and fill not in ("auto", "FFFFFF"):
                        existing_pattern = True
                        break
                if existing_pattern:
                    break
            if existing_pattern:
                continue
            for i, row in enumerate(rows[1:], start=1):
                if i % 2 != 0:
                    continue
                for cell in row.findall(f"{{{w_ns}}}tc"):
                    tcpr = cell.find(f"{{{w_ns}}}tcPr")
                    if tcpr is None:
                        tcpr = etree.SubElement(cell, f"{{{w_ns}}}tcPr")
                        cell.insert(0, tcpr)
                    if tcpr.find(f"{{{w_ns}}}shd") is None:
                        shd = etree.SubElement(tcpr, f"{{{w_ns}}}shd")
                        shd.set(f"{{{w_ns}}}val", "clear")
                        shd.set(f"{{{w_ns}}}color", "auto")
                        shd.set(f"{{{w_ns}}}fill", alt_fill)
                        alt_changes += 1
        if alt_changes:
            entries[doc_path] = _serialize_xml(doc_root)
            changes += alt_changes
            logger.info(
                "[Create][postprocess] Applied alt-row shading on %d cells",
                alt_changes,
            )

    if changes == 0:
        return

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)

    path.write_bytes(buf.getvalue())
    logger.info(
        "[Create][postprocess] Applied %d fixes to %s",
        changes,
        path.name,
    )


def _create_modular_mode(
    request: CreateRequest,
    rules_dir: Path,
) -> CreateResult:
    from mint.assemble import assemble as do_assemble
    from mint.plan import PlanError
    from mint.plan import plan as do_plan
    from mint.section import generate_all as do_generate_all

    base_url = request.llm_base_url
    if not base_url:
        return CreateResult(
            error="modular=True requires llm_base_url",
            execution_mode="modular",
            success=False,
        )

    theme = (
        load_theme(request.theme_name)
        if request.theme_name
        else load_theme()
    )

    try:
        doc_plan = do_plan(
            request.prompt,
            request.format,
            llm_base_url=base_url,
            llm_api_key=request.llm_api_key or "",
            llm_model=request.llm_model or "qwen3.6:35b",
            design_tokens=request.design_tokens,
        )
    except PlanError as e:
        return CreateResult(
            error=f"Plan generation failed: {e}",
            execution_mode="modular",
            success=False,
        )

    logger.info(
        "[Create][modular] Plan generated: %d sections",
        len(doc_plan.sections),
    )

    gen_result = do_generate_all(
        doc_plan,
        llm_base_url=base_url,
        llm_api_key=request.llm_api_key or "",
        llm_model=request.llm_model or "qwen3.6:35b",
        llm_fallback_model=request.llm_fallback_model,
        theme=theme,
    )

    logger.info(
        "[Create][modular] Sections generated: %d/%d succeeded",
        gen_result.succeeded,
        gen_result.total,
    )

    try:
        assembly_result = do_assemble(doc_plan, gen_result.sections, theme=theme)
    except Exception as e:
        return CreateResult(
            error=f"Assembly failed: {e}",
            execution_mode="modular",
            success=False,
            plan=doc_plan,
            sections_total=gen_result.total,
            sections_succeeded=gen_result.succeeded,
        )

    if not assembly_result.success:
        return CreateResult(
            error=assembly_result.error,
            execution_mode="modular",
            success=False,
            plan=doc_plan,
            sections_total=gen_result.total,
            sections_succeeded=gen_result.succeeded,
        )

    output_path = assembly_result.output_path
    if output_path is None:
        return CreateResult(
            error="Assembly produced no output file",
            execution_mode="modular",
            success=False,
            plan=doc_plan,
            sections_total=gen_result.total,
            sections_succeeded=gen_result.succeeded,
        )

    _postprocess_docx(output_path, theme=theme)

    validation = run_checks(output_path, SeverityMode.LENIENT, rules_dir=rules_dir)

    return CreateResult(
        output_path=output_path,
        validation_report=validation,
        execution_mode="modular",
        success=validation.passed and gen_result.failed == 0,
        plan=doc_plan,
        sections_total=gen_result.total,
        sections_succeeded=gen_result.succeeded,
    )


def _create_code_mode(
    request: CreateRequest,
    skill: SkillRef,
    skill_registry: SkillRegistry,
    rules_dir: Path,
) -> CreateResult:
    code = request.model_response_override
    if code is None:
        try:
            code = _call_model(request, skill, skill_registry)
        except ModelCallFailedError as e:
            return CreateResult(
                error=str(e),
                execution_mode="code",
                success=False,
            )

    logger.info(
        "[Create][execute] Generated code (%d chars)",
        len(code),
    )

    if request.self_evaluate and request.model_response_override is None:
        for round_num in range(request.max_refine_rounds):
            logger.info("[Create][evaluate] Self-evaluation round %d", round_num + 1)
            refined = _evaluate_and_refine(request, code, skill)
            if refined is None:
                break
            code = refined
            logger.info(
                "[Create][evaluate] Using refined code (%d chars)",
                len(code),
            )

    try:
        sandbox_result: SandboxResult = sandbox_execute(code)
    except Exception as e:
        logger.error("[Create][execute] Sandbox failed: %s", e)
        error_msg = f"Sandbox execution failed: {e}"
        if "SANDBOX" in str(type(e).__name__).upper():
            error_msg = f"SANDBOX: {e}"
        return CreateResult(
            error=error_msg,
            execution_mode="code",
            success=False,
        )

    if not sandbox_result.success:
        return CreateResult(
            error=f"Sandbox returned failure: {sandbox_result.stderr}",
            execution_mode="code",
            success=False,
        )

    output_path = sandbox_result.output_path
    if output_path is None or not output_path.exists():
        return CreateResult(
            error="Sandbox produced no output file",
            execution_mode="code",
            success=False,
        )

    _postprocess_docx(output_path)

    validation = run_checks(output_path, SeverityMode.LENIENT, rules_dir=rules_dir)

    return CreateResult(
        output_path=output_path,
        validation_report=validation,
        execution_mode="code",
        success=validation.passed,
    )


def _create_template_mode(
    request: CreateRequest,
    skill: SkillRef,
    skill_registry: SkillRegistry,
    templates_dir: Path | None,
    rules_dir: Path,
) -> CreateResult:
    import json

    content_text = request.model_response_override
    if content_text is None:
        try:
            content_text = _call_model(request, skill, skill_registry)
        except ModelCallFailedError as e:
            return CreateResult(
                error=str(e),
                execution_mode="template",
                success=False,
            )

    if templates_dir is None:
        templates_dir = _DEFAULT_TEMPLATES_DIR

    template_name = request.template_name
    if not template_name:
        template_name = "business-memo"

    try:
        engine = TemplateEngine(templates_dir)
        meta = engine.find_template(template_name, fmt=request.format)
    except Exception as e:
        return CreateResult(
            error=f"Template not found: {e}",
            execution_mode="template",
            success=False,
        )

    try:
        content = json.loads(content_text)
    except json.JSONDecodeError as e:
        return CreateResult(
            error=f"Invalid JSON content: {e}",
            execution_mode="template",
            success=False,
        )

    try:
        fill_result = engine.fill(meta, content, design_tokens=request.design_tokens)
    except Exception as e:
        return CreateResult(
            error=f"Template fill failed: {e}",
            execution_mode="template",
            success=False,
        )

    output_path = fill_result.output_path
    validation = run_checks(output_path, SeverityMode.LENIENT, rules_dir=rules_dir)

    return CreateResult(
        output_path=output_path,
        validation_report=validation,
        execution_mode="template",
        success=validation.passed,
    )
