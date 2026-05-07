# FILE: src/mint/create.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Orchestrate full document generation: skill → model → execute → validate → output
#   SCOPE: Create pipeline with code execution and template fill modes
#   DEPENDS: M-SKILLS, M-SANDBOX, M-TEMPLATES, M-VALIDATE, M-CONFIG
#   LINKS: docs/knowledge-graph.xml#M-CREATE, docs/verification-plan.xml#V-M-CREATE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CreateRequest - input dataclass for document generation
#   CreateResult - output dataclass with validation report and metadata
#   create - main orchestration entry point
# END_MODULE_MAP

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mint.config import SeverityMode, Tier
from mint.sandbox import SandboxResult
from mint.sandbox import execute as sandbox_execute
from mint.skills import SkillRef, SkillRegistry
from mint.templates import TemplateEngine
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
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    self_evaluate: bool = False
    max_refine_rounds: int = 1


@dataclass
class CreateResult:
    output_path: Path | None = None
    validation_report: ValidationReport | None = None
    qa_report: Any = None
    execution_mode: str = ""
    duration_ms: int = 0
    error: str | None = None
    success: bool = False


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
        skills_dir = Path(__file__).parent.parent.parent / "skills"
    if rules_dir is None:
        rules_dir = Path(__file__).parent.parent.parent / "rules"

    skill_registry = SkillRegistry(skills_dir)
    try:
        skill = skill_registry.select_skill(request.tier, request.format)
    except Exception as e:
        return CreateResult(
            error=f"Skill selection failed: {e}",
            execution_mode="none",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    if tier == Tier.SMALL:
        result = _create_template_mode(
            request, skill, templates_dir, rules_dir
        )
    else:
        result = _create_code_mode(request, skill, rules_dir)

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


def _call_model(request: CreateRequest, skill: SkillRef) -> str:
    from mint.llm import LLMCallError, LLMClient

    base_url = request.llm_base_url
    if not base_url:
        raise ModelCallFailedError(
            "No model response provided and no LLM_BASE_URL configured. "
            "Set model_response_override or configure LLM endpoint."
        )

    registry = SkillRegistry(Path(__file__).parent.parent.parent / "skills")
    skill_prompt = registry.render_prompt(skill, request.design_tokens)

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
            "1. Do NOT use import/require. Pre-loaded globals: "
            "Document, Packer, Paragraph, TextRun, HeadingLevel, "
            "AlignmentType, Table, TableRow, TableCell, WidthType, "
            "BorderStyle, ImageRun, ExternalHyperlink, writeFileSync.\n"
            "2. Tables MUST use new Table({ rows: [...] }). "
            "NEVER put TableRow directly in sections.children.\n"
            "3. TableCell width MUST be an object: "
            "{ size: NUMBER, type: WidthType.DXA }. Never a bare number.\n"
            "4. Do NOT wrap code in async IIFE (runtime does this).\n"
            "5. Save: writeFileSync('output.docx', buffer).\n"
            "6. Return ONLY raw JavaScript code, no markdown fences."
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


def _create_code_mode(
    request: CreateRequest,
    skill: SkillRef,
    rules_dir: Path,
) -> CreateResult:
    code = request.model_response_override
    if code is None:
        try:
            code = _call_model(request, skill)
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
    templates_dir: Path | None,
    rules_dir: Path,
) -> CreateResult:
    import json

    content_text = request.model_response_override
    if content_text is None:
        try:
            content_text = _call_model(request, skill)
        except ModelCallFailedError as e:
            return CreateResult(
                error=str(e),
                execution_mode="template",
                success=False,
            )

    if templates_dir is None:
        templates_dir = Path(__file__).parent.parent.parent / "templates"

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
