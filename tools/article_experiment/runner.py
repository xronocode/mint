# FILE: tools/article_experiment/runner.py
# VERSION: 0.1.0
"""Per-cell runner for the MINT article experiment.

Two surfaces:
  - run_mint(model, base_url, source_md, out_dir, ...) — the full pipeline:
    LLM → JSON → schema → builder → docx → MP-VALIDATE. Records all metrics.
  - run_baseline(model, base_url, source_md, out_dir, ...) — naked LLM call
    with no schema, no builder; saves whatever the model returned as-is.
    Lets us compare "with mint" vs "without mint" same-prompt-class.

Both surfaces return a result dataclass with the same metric fields where
applicable, so the report layer can render them in one table.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .builder import build_document_from_spec
from .spec import SpecParseError, parse_spec

logger = logging.getLogger(__name__)


class LLMCallError(Exception):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    reasoning: str
    tokens_in: int
    tokens_out: int


class _MintExperimentClient:
    """Thin httpx wrapper tuned for the experiment.

    Differs from src/mint/llm.py::LLMClient in two ways:
      - max_tokens default 16384 — heavy 30B models in reasoning mode can
        burn 8K+ tokens before they emit a single character of content;
        the v1 client's 65536 default kills throughput, the smoke value
        of 8192 wasn't enough for gemma4:31b (we saw the 512s empty
        response trap firsthand).
      - `think=False` option — Ollama-specific extension that disables
        the thinking phase for models that support it (qwen3.5/3.6, glm-4.7,
        gemma4). Passed via `options.think` so it's a no-op on models
        that don't recognize it.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int = 600,
        max_tokens: int = 200_000,
        think: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._think = think
        self._client = httpx.Client(timeout=timeout)

    def call(self, *, prompt: str, system: str | None = None) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": self._max_tokens,
            "options": {
                "num_predict": self._max_tokens,
                "think": self._think,
            },
            # Ollama also honors a top-level `think` flag — set both
            # so this works across Ollama versions.
            "think": self._think,
        }
        url = f"{self._base_url}/chat/completions"

        try:
            resp = self._client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMCallError(f"timeout after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMCallError(f"http error: {exc}") from exc

        if resp.status_code != 200:
            raise LLMCallError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
        except Exception as exc:
            raise LLMCallError(f"invalid JSON from server: {exc}") from exc

        choices = data.get("choices", [])
        if not choices:
            raise LLMCallError("server returned no choices")
        message = choices[0].get("message", {}) or {}
        usage = data.get("usage", {})
        return LLMResponse(
            text=(message.get("content") or "").strip(),
            reasoning=(message.get("reasoning") or "").strip(),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )


# --------------------------------------------------------------------------- #
# Result dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class CellResult:
    """Per-cell metrics; common shape across mint + baseline runs."""

    model: str
    mode: str  # "mint_pipeline" | "baseline_raw"
    duration_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0
    json_parse_ok: bool = False
    schema_valid: bool = False
    schema_violations: list[str] = field(default_factory=list)
    output_path: str | None = None
    output_size_bytes: int = 0
    docx_lenient_passed: bool | None = None
    docx_lenient_hard_count: int | None = None
    error: str | None = None
    raw_text_first_200: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# JSON extraction — models sometimes wrap or prefix the output
# --------------------------------------------------------------------------- #


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(text: str) -> str | None:
    """Try a few common shapes a model might return.

    Order: bare JSON > fenced JSON > first balanced {...} block. Returns
    the candidate JSON string, or None if nothing JSON-shaped found.
    """
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fence = _JSON_FENCE_RE.search(stripped)
    if fence:
        return fence.group(1).strip()

    # Brace-balanced scan — best-effort fallback.
    start = stripped.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(stripped[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    return None


# --------------------------------------------------------------------------- #
# Prompt loader
# --------------------------------------------------------------------------- #


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_mint_prompt(article_markdown: str, schema_description: str) -> tuple[str, str]:
    """Return (system_msg, user_msg) for the mint pipeline."""
    template = (_PROMPTS_DIR / "article_to_spec.md").read_text(encoding="utf-8")
    # The template is documentation-shaped; we split on the H2 markers.
    sys_idx = template.index("## System message")
    schema_idx = template.index("## Schema")
    src_idx = template.index("## Source article (markdown)")
    guidance_idx = template.index("## Conversion guidance")

    system_msg = template[sys_idx + len("## System message") : schema_idx].strip()
    user_msg = (
        template[schema_idx:src_idx]
        .replace("{schema}", schema_description)
        .strip()
        + "\n\n"
        + template[src_idx:guidance_idx]
        .replace("{article_markdown}", article_markdown)
        .strip()
        + "\n\n"
        + template[guidance_idx:].strip()
    )
    return system_msg, user_msg


_BASELINE_SYSTEM = (
    "You are a technical writer. Produce a polished article in Word-ready "
    "format based on the source markdown the user provides. Output only the "
    "finished article — no preface, no commentary."
)
_BASELINE_USER_TEMPLATE = (
    "Below is a draft markdown article. Rewrite it as a polished, "
    "publication-ready article that I can paste directly into Microsoft Word. "
    "Use clear section headings, well-formed paragraphs, lists and tables "
    "where appropriate. Do not include diagnostic commentary or meta-notes.\n"
    "\n"
    "---\n"
    "\n"
    "{article_markdown}\n"
)


# --------------------------------------------------------------------------- #
# Runners
# --------------------------------------------------------------------------- #


def _make_client(base_url: str, model: str, timeout: int = 600) -> _MintExperimentClient:
    return _MintExperimentClient(
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_tokens=200_000,  # well above any model's natural output for this task;
                              # models stop on EOS, the cap is just a safety rail
        think=False,  # disable thinking-phase for the heavy reasoning models
    )


def warmup(base_url: str, model: str) -> float:
    """Cold-load the model into VRAM so timing on the real run is honest.

    Returns the warmup duration in seconds. Uses a tiny prompt so the
    measurement is mostly load-time, not generation-time.
    """
    client = _make_client(base_url, model, timeout=300)
    start = time.monotonic()
    try:
        client.call(prompt="Reply with the single word: ok.")
    except LLMCallError as exc:
        logger.warning("warmup failed for %s: %s", model, exc)
    return time.monotonic() - start


def run_mint(
    *,
    model: str,
    base_url: str,
    article_markdown: str,
    out_dir: Path,
    cell_id: str,
) -> CellResult:
    """Full pipeline: LLM → JSON → schema → builder → docx → MP-VALIDATE."""
    from .spec import PROMPT_SCHEMA_DESCRIPTION

    result = CellResult(model=model, mode="mint_pipeline")
    out_dir.mkdir(parents=True, exist_ok=True)

    system, user = _load_mint_prompt(article_markdown, PROMPT_SCHEMA_DESCRIPTION)
    client = _make_client(base_url, model)

    start = time.monotonic()
    response_text = ""
    last_err: str | None = None

    for attempt in range(2):  # initial + 1 retry
        try:
            resp = client.call(prompt=user, system=system)
        except LLMCallError as exc:
            last_err = str(exc)
            result.retries = attempt
            break
        result.tokens_in += resp.tokens_in
        result.tokens_out += resp.tokens_out
        response_text = resp.text or resp.reasoning  # fall back to reasoning if content empty
        result.raw_text_first_200 = response_text[:200]

        candidate = _extract_json_object(response_text)
        if candidate is None:
            last_err = "no JSON object found in response"
            result.retries = attempt
            user += (
                "\n\nIMPORTANT: your previous reply did not contain valid JSON. "
                "Reply NOW with ONLY the JSON object — no prose, no fence."
            )
            continue

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_err = f"JSON parse failed: {exc}"
            result.retries = attempt
            user += (
                f"\n\nIMPORTANT: your previous JSON was malformed ({exc}). "
                "Reply NOW with ONLY a valid JSON object."
            )
            continue

        result.json_parse_ok = True
        try:
            spec = parse_spec(data)
        except SpecParseError as exc:
            last_err = f"schema validation failed: {exc}"
            result.schema_violations = [str(exc)]
            result.retries = attempt
            break

        result.schema_valid = True

        # Build + save + validate the docx.
        try:
            doc = build_document_from_spec(spec)
            docx_path = out_dir / f"{cell_id}.docx"
            doc.save(docx_path)
            result.output_path = str(docx_path)
            result.output_size_bytes = docx_path.stat().st_size
            report = doc.validate(level="lenient")
            result.docx_lenient_passed = report.passed
            result.docx_lenient_hard_count = report.hard_count
        except Exception as exc:  # builder failure is informational, not fatal
            last_err = f"builder failed: {exc}"
        break

    result.duration_s = time.monotonic() - start
    if last_err and not result.schema_valid:
        result.error = last_err
    elif last_err:
        # Schema valid but downstream issue — still report.
        result.error = last_err
    return result


def run_baseline(
    *,
    model: str,
    base_url: str,
    article_markdown: str,
    out_dir: Path,
    cell_id: str,
) -> CellResult:
    """Naked LLM call — no schema, no builder. Saves raw response as .md."""
    result = CellResult(model=model, mode="baseline_raw")
    out_dir.mkdir(parents=True, exist_ok=True)

    user = _BASELINE_USER_TEMPLATE.format(article_markdown=article_markdown)
    client = _make_client(base_url, model)
    start = time.monotonic()
    try:
        resp = client.call(prompt=user, system=_BASELINE_SYSTEM)
    except LLMCallError as exc:
        result.duration_s = time.monotonic() - start
        result.error = str(exc)
        return result

    result.duration_s = time.monotonic() - start
    result.tokens_in = resp.tokens_in
    result.tokens_out = resp.tokens_out
    text = resp.text or resp.reasoning
    result.raw_text_first_200 = text[:200]

    out_path = out_dir / f"{cell_id}.md"
    out_path.write_text(text, encoding="utf-8")
    result.output_path = str(out_path)
    result.output_size_bytes = out_path.stat().st_size
    return result
