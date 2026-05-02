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
    prompt = registry.render_prompt(skill, request.design_tokens)

    code_or_json = (
        "JavaScript code using docx-js/pptxgenjs"
        if request.tier != "small"
        else "JSON content"
    )
    system_prompt = (
        f"You are a document generation assistant. "
        f"Generate {code_or_json} "
        f"to create a {request.format.upper()} document. "
        f"Return ONLY the code/JSON, no explanations."
    )

    client = LLMClient(
        base_url=base_url,
        api_key=request.llm_api_key or "",
        model=request.llm_model or "glm-5",
    )

    try:
        response = client.call(prompt, system=system_prompt)
    except LLMCallError as e:
        raise ModelCallFailedError(str(e)) from e

    logger.info(
        "[Create][llm] Model responded: model=%s, tokens=%s, duration=%dms",
        response.model,
        response.usage,
        response.duration_ms,
    )
    return response.text


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
