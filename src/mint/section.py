# FILE: src/mint/section.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Generate docx-js code for individual document sections via LLM
#   SCOPE: Section code generation, validation, retry, sequential generate_all
#   DEPENDS: M-PLAN, M-LLM
#   LINKS: docs/knowledge-graph.xml#M-SECTION, docs/verification-plan.xml#V-M-SECTION
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   SectionCode - per-section generation result
#   SiblingRef - lightweight reference to sibling section
#   GenerateAllResult - aggregate result for all sections
#   SectionError - base section exception
#   SectionGenerationFailedError - LLM call or parse failure
#   SectionCodeInvalidError - code validation failure
#   SectionNotFoundError - section not found in plan
#   generate_section - generate code for a single section
#   generate_all - generate all sections sequentially with sibling context
#   validate_section_code - static check on generated JS code
#   render_section_prompt - build prompt for section code generation
#   MAX_SECTION_RETRIES - default max retry count per section (2)
#   MAX_TOTAL_RETRIES - global retry budget across all sections (20)
#   SANDBOX_GLOBALS_DOCX - comma-separated string of pre-loaded docx-js globals
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation: per-section code generation + retry
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from mint.plan import DocumentPlan, SectionSpec

logger = logging.getLogger(__name__)

_LOG_PREFIX = "Section"

MAX_SECTION_RETRIES = 2
MAX_TOTAL_RETRIES = 20

SANDBOX_GLOBALS_DOCX = (
    "All docx-js exports (Document, Packer, Paragraph, TextRun, HeadingLevel, "
    "AlignmentType, Table, TableRow, TableCell, WidthType, "
    "BorderStyle, ShadingType, ImageRun, ExternalHyperlink, "
    "InternalHyperlink, Bookmark, LevelFormat, PageOrientation, "
    "SectionType, TabStopType, TabStopPosition, "
    "Header, Footer, PageNumber, PageBreak, TableOfContents, "
    "FootnoteReferenceRun, PositionalTab, Column, "
    "Numbering, StyleLevel, UnderlineType, HighlightColor, "
    "writeFileSync, docx"
)


class SectionError(Exception):
    pass


class SectionGenerationFailedError(SectionError):
    pass


class SectionCodeInvalidError(SectionError):
    pass


class SectionNotFoundError(SectionError):
    pass


@dataclass(frozen=True)
class SiblingRef:
    section_id: str
    title: str
    type: str


@dataclass
class SectionCode:
    section_id: str
    code: str
    attempt: int = 1
    success: bool = True
    error: str | None = None
    model: str = ""
    duration_ms: int = 0


@dataclass(frozen=True)
class GenerateAllResult:
    sections: dict[str, SectionCode] = field(default_factory=dict)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    total_retries: int = 0
    total_duration_ms: int = 0


# START_BLOCK_SECTION_VALIDATE
def validate_section_code(code: str) -> list[str]:
    errors: list[str] = []

    import_patterns = [
        (r"\bimport\s+", "import statement"),
        (r"""require\s*\(\s*['"]""", "require() call"),
        (r"\bfs\b", "fs reference"),
        (r"\bprocess\s*\.\s*exit", "process.exit"),
    ]
    for pattern, desc in import_patterns:
        if re.search(pattern, code):
            errors.append(f"Forbidden pattern: {desc}")

    stack: list[str] = []
    in_string: str | None = None
    escape_next = False
    i = 0
    while i < len(code):
        ch = code[i]

        if escape_next:
            escape_next = False
            i += 1
            continue

        if ch == "\\" and in_string:
            escape_next = True
            i += 1
            continue

        if in_string:
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch in ('"', "'", "`"):
            in_string = ch
            i += 1
            continue

        if ch == "/" and i + 1 < len(code):
            if code[i + 1] == "/":
                while i < len(code) and code[i] != "\n":
                    i += 1
                continue
            if code[i + 1] == "*":
                i += 2
                while i + 1 < len(code) and not (code[i] == "*" and code[i + 1] == "/"):
                    i += 1
                i += 2
                continue

        if ch in "({[":
            stack.append(ch)
        elif ch in ")}]":
            pairs = {"(": ")", "{": "}", "[": "]"}
            if stack and pairs.get(stack[-1]) == ch:
                stack.pop()
            else:
                errors.append(f"Unmatched closing bracket at position {i}")
                break

        i += 1

    if stack and not errors:
        errors.append(f"Unmatched opening brackets: {len(stack)} unclosed")

    logger.info(
        f"[{_LOG_PREFIX}][validate][BLOCK_SECTION_VALIDATE] "
        "Code validation: %s (%d chars)",
        "PASS" if not errors else f"FAIL ({len(errors)} errors)",
        len(code),
    )
    return errors
# END_BLOCK_SECTION_VALIDATE


def render_section_prompt(
    plan_data: DocumentPlan,
    section_spec: SectionSpec,
    siblings: list[SiblingRef] | None = None,
) -> str:
    siblings_block = ""
    if siblings:
        siblings_block = "\nAlready generated sections (for context/coherence):\n"
        for s in siblings:
            siblings_block += f"  - [{s.type}] {s.title}\n"

    numbering_block = ""
    if plan_data.numbering:
        numbering_block = "\nAvailable numbering references:\n"
        for nc in plan_data.numbering:
            numbering_block += f"  - {nc.reference}\n"

    styles = plan_data.styles

    return (
        "You are a document section code generator using docx-js.\n\n"
        "RULES:\n"
        "1. No import/require/fs. All docx-js classes are pre-loaded.\n"
        "2. Return ONLY children array elements (Paragraph, Table, etc).\n"
        "3. Font size is in HALF-POINTS: 11pt=size:22, 14pt=size:28, "
        "16pt=size:32, 24pt=size:48. NEVER use size above 100.\n"
        "4. Use style: 'Heading1'/'Heading2'/'Heading3' for headings. "
        "No style for body text.\n"
        "5. Tables: width: {size:100, type:WidthType.PERCENTAGE}. "
        "Header row: shading {fill:'PRIMARY', type:ShadingType.CLEAR}, "
        "white bold text. Even rows: shading fill='F9FAFB'. "
        "Odd rows: no shading.\n"
        "6. Shading: always type:ShadingType.CLEAR, never 'solid'.\n"
        "7. Separate ALL elements with commas.\n"
        "8. No LineBreak/ListParagraph/Body style — they don't exist.\n"
        "9. No markdown fences. Raw JavaScript only.\n\n"
        f"SECTION: [{section_spec.type}] {section_spec.title} "
        f"(level={section_spec.level}, id={section_spec.id})\n"
        f"Description: {section_spec.description}\n\n"
        f"COLORS: primary={styles.colors.get('primary', '#1E40AF')} "
        f"accent={styles.colors.get('accent', '#3B82F6')} "
        f"text={styles.colors.get('text', '#1F2937')}\n"
        f"BODY SIZE: {styles.body_size} ({styles.body_size / 2:.0f}pt)\n"
        f"{numbering_block}"
        f"{siblings_block}\n"
        "Generate rich content: at least 5 paragraphs for content sections, "
        "full tables with 5+ rows for table sections. "
        "Professional formatting with consistent sizes.\n"
    )


def _render_retry_prompt(
    section_spec: SectionSpec,
    previous_error: str,
    previous_code: str,
) -> str:
    return (
        "Your previous section code was rejected.\n\n"
        f"SECTION: [{section_spec.type}] {section_spec.title}\n"
        f"Description: {section_spec.description}\n\n"
        f"ERROR:\n{previous_error}\n\n"
        f"FAILED CODE (DO NOT repeat these mistakes):\n"
        f"```javascript\n{previous_code[:2000]}\n```\n\n"
        "Fix the error and return corrected JavaScript code. "
        "Return ONLY raw JavaScript, no markdown fences.\n"
    )


def _strip_code_fences(text: str) -> str:
    match = re.search(r"```(?:javascript|js)?\s*\n(.*?)```", text, re.DOTALL)
    text = match.group(1).strip() if match else text.strip()

    text = re.sub(r"\)\s*\n\s*(new\s)", r"),\n\1", text)
    text = re.sub(r"\)\s*\n\s*(\/\*)", r"),\n\1", text)

    return text


# START_BLOCK_SECTION_GENERATE
def generate_section(
    plan_data: DocumentPlan,
    section_id: str,
    *,
    siblings: list[SiblingRef] | None = None,
    llm_base_url: str,
    llm_api_key: str = "",
    llm_model: str = "qwen3.6:35b",
    max_retries: int = MAX_SECTION_RETRIES,
) -> SectionCode:
    from mint.llm import LLMCallError, LLMClient

    section_spec = None
    for s in plan_data.sections:
        if s.id == section_id:
            section_spec = s
            break
    if section_spec is None:
        raise SectionNotFoundError(
            f"Section '{section_id}' not found in plan"
        )

    logger.info(
        f"[{_LOG_PREFIX}][generate][BLOCK_SECTION_GENERATE] "
        "Generating section: id=%s, type=%s, title=%s",
        section_id,
        section_spec.type,
        section_spec.title,
    )

    client = LLMClient(
        base_url=llm_base_url,
        api_key=llm_api_key,
        model=llm_model,
    )

    system_prompt = render_section_prompt(plan_data, section_spec, siblings)
    last_error: str = ""
    last_code: str = ""

    for attempt in range(1 + max_retries):
        start = time.monotonic()

        user_prompt: str
        if last_error and last_code:
            user_prompt = _render_retry_prompt(
                section_spec, last_error, last_code
            )
        else:
            user_prompt = (
                f"Generate the section code for: "
                f"[{section_spec.type}] {section_spec.title}\n"
                f"Content description: {section_spec.description}"
            )

        try:
            response = client.call(user_prompt, system=system_prompt)
            raw_code = _strip_code_fences(response.text)
        except LLMCallError as e:
            last_error = str(e)
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                f"[{_LOG_PREFIX}][generate] Attempt %d: LLM error: %s",
                attempt + 1,
                e,
            )
            if attempt == max_retries:
                return SectionCode(
                    section_id=section_id,
                    code="",
                    attempt=attempt + 1,
                    success=False,
                    error=last_error,
                    model=llm_model,
                    duration_ms=duration_ms,
                )
            continue

        if len(raw_code.strip()) < 20:
            last_error = "Generated code is empty or too short"
            last_code = raw_code
            logger.warning(
                f"[{_LOG_PREFIX}][generate] Attempt %d: empty code (%d chars)",
                attempt + 1,
                len(raw_code),
            )
            continue

        code_errors = validate_section_code(raw_code)
        duration_ms = int((time.monotonic() - start) * 1000)

        if not code_errors:
            logger.info(
                f"[{_LOG_PREFIX}][generate][BLOCK_SECTION_GENERATE] "
                "Section generated: id=%s, attempt=%d, duration=%dms",
                section_id,
                attempt + 1,
                duration_ms,
            )
            return SectionCode(
                section_id=section_id,
                code=raw_code,
                attempt=attempt + 1,
                success=True,
                model=response.model,
                duration_ms=duration_ms,
            )

        last_error = "; ".join(code_errors)
        last_code = raw_code
        logger.warning(
            f"[{_LOG_PREFIX}][generate] Attempt %d: code validation failed: %s",
            attempt + 1,
            last_error,
        )

    return SectionCode(
        section_id=section_id,
        code="",
        attempt=1 + max_retries,
        success=False,
        error=last_error,
        model=llm_model,
        duration_ms=0,
    )
# END_BLOCK_SECTION_GENERATE


def _score_section_code(code: str) -> int:
    if not code or len(code) < 50:
        return 0
    score = min(len(code), 5000) // 50
    if "style:" in code:
        score += 5
    if "Heading" in code:
        score += 5
    if "Table" in code:
        score += 3
    if "shading" in code.lower():
        score += 3
    if "F9FAFB" in code or "F3F4F6" in code:
        score += 5
    return score


def _pick_best(primary: SectionCode, secondary: SectionCode) -> SectionCode:
    if not primary.success and not secondary.success:
        return primary
    if primary.success and not secondary.success:
        return primary
    if not primary.success and secondary.success:
        return secondary
    p_score = _score_section_code(primary.code)
    s_score = _score_section_code(secondary.code)
    return primary if p_score >= s_score else secondary


# START_BLOCK_GENERATE_ALL
def generate_all(
    plan_data: DocumentPlan,
    *,
    llm_base_url: str,
    llm_api_key: str = "",
    llm_model: str = "qwen3.6:35b",
    llm_fallback_model: str | None = None,
    max_retries: int = MAX_SECTION_RETRIES,
    max_total_retries: int = MAX_TOTAL_RETRIES,
) -> GenerateAllResult:
    fb_info = f", fallback={llm_fallback_model}" if llm_fallback_model else ""
    logger.info(
        f"[{_LOG_PREFIX}][generate_all] Starting: %d sections, model=%s%s",
        len(plan_data.sections),
        llm_model,
        fb_info,
    )

    results: dict[str, SectionCode] = {}
    fallback_results: dict[str, SectionCode] = {}
    siblings: list[SiblingRef] = []
    total_retries = 0
    total_start = time.monotonic()

    for section_spec in plan_data.sections:
        sc = generate_section(
            plan_data,
            section_spec.id,
            siblings=siblings,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            max_retries=max_retries,
        )
        results[section_spec.id] = sc

        if llm_fallback_model and not sc.success:
            logger.info(
                f"[{_LOG_PREFIX}][generate_all] "
                "Primary failed for %s, trying fallback %s",
                section_spec.id,
                llm_fallback_model,
            )
            fb = generate_section(
                plan_data,
                section_spec.id,
                siblings=siblings,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_fallback_model,
                max_retries=max_retries,
            )
            fallback_results[section_spec.id] = fb

        if sc.attempt > 1:
            total_retries += sc.attempt - 1

        if total_retries >= max_total_retries:
            logger.warning(
                f"[{_LOG_PREFIX}][generate_all] "
                "Global retry budget exhausted: %d/%d retries",
                total_retries,
                max_total_retries,
            )

        siblings.append(SiblingRef(
            section_id=section_spec.id,
            title=section_spec.title,
            type=section_spec.type,
        ))

    # Pick best between primary and fallback
    for sid, fb_sc in fallback_results.items():
        primary = results[sid]
        best = _pick_best(primary, fb_sc)
        if best is fb_sc:
            logger.info(
                f"[{_LOG_PREFIX}][generate_all] "
                "Using fallback result for %s (score improvement)",
                sid,
            )
        results[sid] = best

    succeeded = sum(1 for sc in results.values() if sc.success)
    failed = sum(1 for sc in results.values() if not sc.success)
    total_duration = int((time.monotonic() - total_start) * 1000)

    result = GenerateAllResult(
        sections=results,
        total=len(results),
        succeeded=succeeded,
        failed=failed,
        total_retries=total_retries,
        total_duration_ms=total_duration,
    )

    logger.info(
        f"[{_LOG_PREFIX}][generate_all] Finished: "
        "total=%d, succeeded=%d, failed=%d, retries=%d, duration=%dms",
        result.total,
        result.succeeded,
        result.failed,
        result.total_retries,
        result.total_duration_ms,
    )
    return result
# END_BLOCK_GENERATE_ALL
