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
def _validate_with_node(code: str) -> str | None:
    """Validate section code by feeding it to `node --check` inside a wrapper
    that mirrors the assembly context (`children:[ ... ]` array body).

    Returns None on success, a short error message on failure. If `node` is
    unavailable, returns None (graceful degradation — the assembler runs
    its own node --check before sandbox execution as a final guard).
    """
    import subprocess
    import tempfile
    from pathlib import Path as _Path

    # Wrap section code the same way assemble.py does so token-context matches.
    wrapper = (
        "(function _section_check(){\n"
        "  const _arr = [\n"
        f"    {code}\n"
        "  ];\n"
        "  return _arr;\n"
        "})\n"
    )
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False
        ) as f:
            f.write(wrapper)
            tmp = f.name
    except OSError:
        return None
    try:
        result = subprocess.run(
            ["node", "--check", tmp],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return None
        # Extract first error line (skip path noise)
        stderr = (result.stderr or "").strip()
        for line in stderr.splitlines():
            line = line.strip()
            if line.startswith("SyntaxError"):
                return line
        return stderr.splitlines()[-1] if stderr else "node --check failed"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    finally:
        import contextlib
        with contextlib.suppress(OSError):
            _Path(tmp).unlink(missing_ok=True)


def validate_section_code(code: str, section_type: str | None = None) -> list[str]:
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

    # Min-content gate: only for SUBSTANTIVE section types (content, table,
    # heading, list, callout, code, images). Cover pages, TOC, and appendix
    # entries are intentionally short by design — gating them triggers wasted
    # retry cascades (the model can't honestly produce 600 chars for a cover).
    sparse_exempt = {"cover", "toc", "table_of_contents", "appendix"}
    if section_type and section_type.lower() not in sparse_exempt:
        paragraph_count = len(re.findall(r"\bnew\s+Paragraph\s*\(", code))
        table_count = len(re.findall(r"\bnew\s+Table\s*\(", code))
        elements_total = paragraph_count + table_count
        # Stricter floor: a real content section needs ≥5 elements OR ≥1000
        # chars of substantive code. The earlier ≥3-elements gate let through
        # sections like "heading + 2 thin paragraphs" that VLM scored as
        # "page mostly empty after heading".
        if elements_total < 5 and len(code) < 1000:
            errors.append(
                f"Section too sparse: only {paragraph_count} paragraph(s) and "
                f"{table_count} table(s) in {len(code)} chars. Expected ≥5 "
                f"elements or ≥1000 chars of substantive content."
            )

    # Use node --check on the section wrapped in array-body context. This is
    # authoritative (real JS parser) and avoids the regex-based bracket
    # counter's false positives on JS regex literals, template literals with
    # ${expr}, and multi-line strings. If node is unavailable the function
    # returns None and we fall through with no extra errors — the assembler
    # runs its own node --check before sandbox execution as a final guard.
    node_error = _validate_with_node(code)
    if node_error:
        errors.append(node_error)

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
        "You generate docx-js section code. Return ONLY raw JS array elements "
        "(comma-separated new Paragraph(...), new Table(...) etc). No imports, "
        "no fences, no prose, no trailing semicolon.\n\n"
        "HARD RULES (failures here = invalid output):\n"
        "- Tables: width:{size:9360,type:WidthType.DXA}, columnWidths sum=9360, "
        "cell width:{size:N,type:WidthType.DXA}. NEVER WidthType.PERCENTAGE.\n"
        "- Shading: {fill:'HEX',type:ShadingType.CLEAR}. NEVER type:'solid'.\n"
        "- Headings: use style:'Heading1'|'Heading2'|'Heading3'. No inline "
        "size/bold/color on a heading paragraph.\n"
        "- TextRun.text MUST NOT contain \\n. New line = new Paragraph({}).\n"
        "- Sizes are HALF-POINTS (11pt=22, 14pt=28, 16pt=32). Cap at 100.\n"
        "- Balance every ( [ { with ) ] }. Output is invalid if unbalanced.\n\n"
        f"SECTION [{section_spec.type}] {section_spec.title} "
        f"(level={section_spec.level})\n"
        f"Content: {section_spec.description}\n\n"
        f"colors: primary={styles.colors.get('primary', '#1E40AF')} "
        f"accent={styles.colors.get('accent', '#3B82F6')} "
        f"text={styles.colors.get('text', '#1F2937')}; "
        f"body size {styles.body_size}.\n"
        f"{numbering_block}"
        f"{siblings_block}\n"
        "Be substantive: 5+ paragraphs for content, 5+ rows for tables.\n"
    )


def _render_retry_prompt(
    section_spec: SectionSpec,
    previous_error: str,
    previous_code: str,
) -> str:
    # Try to extract a position hint from the error and show a focused window
    # around it. Falls back to head + tail if no position is reported.
    pos_match = re.search(r"position\s+(\d+)", previous_error)
    if pos_match:
        pos = int(pos_match.group(1))
        window_before = max(0, pos - 400)
        window_after = min(len(previous_code), pos + 400)
        head = previous_code[:200]
        focus = previous_code[window_before:window_after]
        tail = previous_code[-200:] if len(previous_code) > 800 else ""
        snippet = (
            f"--- start of previous code ---\n{head}\n... ({window_before} chars omitted)\n"
            f"--- error window around position {pos} ---\n{focus}\n"
            f"--- end of previous code ---\n{tail}\n"
        )
    else:
        head = previous_code[:1500]
        tail = previous_code[-500:] if len(previous_code) > 2000 else ""
        snippet = (
            f"--- start ---\n{head}\n"
            f"--- ... truncated ... ---\n{tail}\n"
        )
    return (
        "Your previous section code was rejected. Regenerate it from scratch, "
        "do not just copy fragments back.\n\n"
        f"SECTION: [{section_spec.type}] {section_spec.title}\n"
        f"Description: {section_spec.description}\n\n"
        f"PARSER ERROR: {previous_error}\n\n"
        f"FAILED CODE (DO NOT repeat these mistakes):\n"
        f"```javascript\n{snippet}\n```\n\n"
        "Common fixes: remove trailing ;, balance every ( [ { with ) ] }, "
        "no \\n in TextRun.text (use new Paragraph instead), "
        "no WidthType.PERCENTAGE for tables.\n"
        "Return ONLY raw JavaScript, no markdown fences.\n"
    )


def _dump_section_attempt(
    section_id: str,
    attempt: int,
    code: str,
    response: object,  # LLMResponse, not imported here to keep cycle clean
) -> None:
    """Dump the raw normalized model output to MINT_DEBUG_DUMP_SECTIONS dir.

    Activated by setting MINT_DEBUG_DUMP_SECTIONS=<dir>; otherwise no-op.
    Writes one file per (section_id, attempt) plus a sidecar `.meta` with
    finish_reason, content/reasoning lengths, and first 4KB of reasoning so
    bracket-mismatch false-positives can be reproduced and fixed offline.
    """
    import os as _os
    target = _os.environ.get("MINT_DEBUG_DUMP_SECTIONS")
    if not target:
        return
    from pathlib import Path as _Path

    out = _Path(target)
    try:
        out.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", section_id)
        base = out / f"{safe}__attempt{attempt}"
        base.with_suffix(".js").write_text(code)
        meta = (
            f"section_id={section_id}\n"
            f"attempt={attempt}\n"
            f"content_len={len(code)}\n"
            f"finish_reason={getattr(response, 'finish_reason', '')}\n"
            f"reasoning_len={len(getattr(response, 'reasoning', '') or '')}\n"
            f"--- reasoning (first 4KB) ---\n"
            f"{(getattr(response, 'reasoning', '') or '')[:4096]}\n"
        )
        base.with_suffix(".meta").write_text(meta)
    except OSError as exc:
        logger.warning(
            f"[{_LOG_PREFIX}][generate] section dump failed: %s", exc
        )


def _trim_trailing_garbage(code: str) -> str:
    """Truncate the section code at the first point where a closing bracket
    has no matching open. gpt-oss in particular tends to emit a few extra
    `})` / `]` after the well-formed body when finishing a long section.

    Walks forward tracking string/comment state and bracket stack. The first
    close that would underflow the stack marks the end of the well-formed
    portion — we keep everything before it (and add the implied closes via
    the assembler's _repair_brackets if anything was missing). String quotes
    and JS comments are honored so closures inside text content do not trip
    the heuristic.
    """
    open_to_close = {"(": ")", "[": "]", "{": "}"}
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    in_string: str | None = None
    escape_next = False
    in_line_comment = False
    in_block_comment = False

    safe_end = len(code)
    i = 0
    while i < len(code):
        ch = code[i]

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and i + 1 < len(code) and code[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if escape_next:
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            i += 1
            continue
        if not in_string and ch == "/" and i + 1 < len(code):
            if code[i + 1] == "/":
                in_line_comment = True
                i += 2
                continue
            if code[i + 1] == "*":
                in_block_comment = True
                i += 2
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
        if ch in open_to_close:
            stack.append(ch)
        elif ch in pairs:
            expected = pairs[ch]
            if stack and stack[-1] == expected:
                stack.pop()
                # Whenever the stack returns to empty we have a candidate end
                # of well-formed body — record it so we can later truncate the
                # garbage tail.
                if not stack:
                    safe_end = i + 1
            else:
                # Mismatched close — truncate here, drop everything from i on.
                return code[:safe_end].rstrip()
        i += 1

    return code


def _strip_code_fences(text: str) -> str:
    match = re.search(r"```(?:javascript|js)?\s*\n(.*?)```", text, re.DOTALL)
    text = match.group(1).strip() if match else text.strip()

    text = re.sub(r"\)\s*\n\s*(new\s)", r"),\n\1", text)
    text = re.sub(r"\)\s*\n\s*(\/\*)", r"),\n\1", text)

    # Common model API confusions — auto-rewrite before injection. These
    # are docx-js prop / global names the LLM frequently invents:
    #   tableRows: [...]              →  rows: [...]   (Table prop)
    #   tableProperties: {...}        →  (drop wrapper, props were already on Table)
    #   tableCellProperties:           →  (drop wrapper)
    #   BorderType.X                  →  BorderStyle.X  (real docx-js global)
    #   AlignmentType.JUSTIFY → AlignmentType.JUSTIFIED is sometimes confused; keep as-is.
    text = re.sub(r"\btableRows\s*:", "rows:", text)
    text = re.sub(r"\btableProperties\s*:\s*\{[^}]*\},?\s*", "", text)
    text = re.sub(r"\btableCellProperties\s*:\s*", "", text)
    # docx-js global name fixups
    text = re.sub(r"\bBorderType\b", "BorderStyle", text)
    text = re.sub(r"\bBorderStyles\b", "BorderStyle", text)
    text = re.sub(r"\bAlignment\.", "AlignmentType.", text)
    text = re.sub(r"\bShadingType\.SOLID\b", "ShadingType.CLEAR", text)
    text = re.sub(r"\bnew\s+LineBreak\s*\(\s*\)", "new TextRun({ break: 1 })", text)

    # Section code is injected verbatim into the array body of `children:[ ... ]`
    # in the assembled JS. Some models (notably gpt-oss after thinking) terminate
    # the last expression with a trailing `;` as if it were a statement. Inside
    # an array literal that produces invalid JS:
    #     children:[
    #       new Paragraph({...});   // <-- ; not allowed here
    #     ],
    # Strip trailing whitespace plus any number of trailing semicolons (and the
    # whitespace between them) so the code ends cleanly on `)`, `]`, or `}`.
    text = re.sub(r"[\s;]+$", "", text)

    # gpt-oss in particular tends to emit a tail of extra closing brackets
    # after a well-formed body (matched in real model outputs:
    #     ... last new Paragraph({...})
    #     ]
    #     })
    #     ]
    #     })
    #     })
    # ). Truncate at the first close-without-matching-open.
    text = _trim_trailing_garbage(text)

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
            _dump_section_attempt(
                section_id, attempt + 1, raw_code, response
            )
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

        code_errors = validate_section_code(raw_code, section_spec.type)
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
