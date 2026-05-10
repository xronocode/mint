# FILE: src/mint_python/qa/visual.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Advisory post-create_document visual quality gate. Renders the
#     produced docx through soffice (-> PDF) and pdftoppm (-> PNGs), then
#     scores each page on a fixed 4-axis rubric (typography, layout,
#     visual_richness, professional) via a local VLM (Ollama qwen2.5vl:7b).
#     The aggregate score, axes, issues, page count, preset name, and the
#     advisory flag are returned as a VisualQAReport. The hook NEVER fails
#     create_document on visual-QA outcome — backend missing, network error,
#     malformed VLM JSON, etc. all degrade to a logged WARNING + None /
#     skipped report. Two-layer defense per VF-019 inv-1 ADVISORY-ONLY.
#   SCOPE: Public surface = score_document (entry point), VisualQAReport
#     dataclass. Internal helpers: backend probes, render pipeline (PDF +
#     PNG), VLM call, aggregate. Caller wiring lives in mcp/document.py;
#     this module is pure library code with no FastMCP attachments.
#   DEPENDS: stdlib only at module load (base64, json, logging, os, re,
#     shutil, subprocess, tempfile, urllib.request, urllib.error). The
#     module DOES NOT import lxml at top level — the docx is treated as
#     opaque input bytes to the render pipeline; no XML inspection.
#   LINKS: docs/development-plan.xml#MP-VISUAL-QA-HOOK,
#     docs/verification-plan.xml#V-MP-VISUAL-QA-HOOK,
#     docs/verification-plan.xml#VF-019,
#     docs/knowledge-graph.xml#MP-VISUAL-QA-HOOK
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   VisualQAReport      - frozen dataclass returned by score_document
#   score_document      - public entry; never raises; returns
#                         VisualQAReport | None
#   _probe_backends     - shutil.which checks for soffice + pdftoppm
#   _render_pdf         - soffice headless conversion docx -> pdf
#   _render_pngs        - pdftoppm conversion pdf -> page-*.png list
#   _call_vlm           - send single PNG to Ollama; parse 4-axis JSON
#   _aggregate          - mean of per-page axes -> aggregate score + axes
#   _PROMPT             - constant rubric prompt; CALLER-INPUT-FREE
#                         (VF-019 inv-7 NO-CALLER-INPUT-IN-VLM-PAYLOAD)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Phase-15 Wave-15-2 (MP-VISUAL-QA-HOOK).
#     Refactor of the repo-root e2e_qa_visual.py one-shot CLI into a
#     library module: (a) drops doc_title from the VLM prompt entirely
#     per VF-019 inv-7 (the original interpolated docx core.xml title);
#     (b) adds backend probes (shutil.which soffice / pdftoppm) for
#     graceful degradation per VF-019 inv-4 BACKEND-DEGRADATION;
#     (c) catches all VLM-call exceptions internally so callers never
#     see a raise (VF-019 inv-1 ADVISORY-ONLY); (d) tempfile cleanup
#     unconditional via try/finally with mint_qa_ prefix per VF-019
#     inv-6 TEMP-FILE-CLEANUP.
# END_CHANGE_SUMMARY
"""Advisory visual QA scorer for MINT-generated DOCX.

Public API:
    score_document(document_path, preset_name="klawd", *, threshold=70,
                   max_pages=5) -> VisualQAReport | None

The function NEVER raises into the caller. Returns None when:
  - MINT_SKIP_VISUAL_QA=1 is set in the environment (caller opt-out).

Returns a VisualQAReport with skipped=True when:
  - soffice / pdftoppm binary missing.
  - Ollama endpoint unreachable / HTTP error / timeout / connection error.
  - Any exception during rendering / VLM call / aggregation.

Returns a VisualQAReport with skipped=False on the happy path.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

logger = logging.getLogger(__name__)


# Endpoint + model are module constants; they are NOT parameterized by the
# caller (VF-019 inv-7 NO-CALLER-INPUT-IN-VLM-PAYLOAD). Override at the
# environment level if a different endpoint is needed for ops; callers
# of score_document cannot inject these.
_OLLAMA_URL = os.environ.get("MINT_VISUAL_QA_OLLAMA_URL", "http://10.128.26.10:11434/api/chat")
_VLM_MODEL = os.environ.get("MINT_VISUAL_QA_VLM_MODEL", "qwen2.5vl:7b")
_VLM_TIMEOUT_SEC = 180
_RENDER_TIMEOUT_SEC = 60

# Constant rubric prompt — parameterized ONLY by numeric page_no /
# total_pages (no caller-derived strings interpolated). VF-019 inv-7.
_PROMPT_TEMPLATE = """You are a graphic-design reviewer.
Analyze this single page from a generated DOCX.
Score 0-100 on each axis. Return STRICT JSON ONLY (no markdown fences, no commentary):

{{
  "typography": <int>,
  "layout": <int>,
  "visual_richness": <int>,
  "professional": <int>,
  "issues": ["<short concrete problem 1>", "<short concrete problem 2>", "..."],
  "highlights": ["<what works 1>", "<what works 2>"]
}}

Axes (rate by appropriateness for the page TYPE - recognize cover/TOC/section-break pages):
- typography: font consistency, weight/size hierarchy, no jumping styles
- layout: balance, alignment; whitespace is GOOD on cover/TOC, BAD when content was expected
- visual_richness: tables/callouts/images/decorative elements; weight LESS for cover pages
- professional: passes for a corporate/technical report, not amateur

Issues: 3-7 short concrete problems (<100 chars each).
Highlights: 1-3 things this page does well.

Page context: page {page_no} of {total_pages}.
Treat page 1 as the COVER, page 2 as TOC if applicable, pages 3+ as body content.

Return ONLY the JSON object.
"""

_AXES: tuple[str, ...] = ("typography", "layout", "visual_richness", "professional")


@dataclass(frozen=True)
class VisualQAReport:
    """Advisory visual-QA report attached to create_document's
    structured_content. `advisory=True` is the contractual signal that
    callers (and downstream consumers) MUST NOT treat the score as a
    pass/fail gate."""

    score: float
    axes: dict[str, int]
    issues: list[str]
    pages_scored: int
    preset_name: str
    advisory: bool = True
    skipped: bool = False
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (asdict copies the contained mappings
        and lists, so the returned dict is safe to mutate)."""
        return asdict(self)


@dataclass
class _SkipMarker:
    """Internal sentinel used to flow a backend-skipped result up to the
    public boundary. score_document converts this into a skipped
    VisualQAReport (or None for env-skip)."""

    reason: str
    issues: list[str] = field(default_factory=list)


def _probe_backends() -> _SkipMarker | None:
    """Verify soffice + pdftoppm are on PATH. Returns a _SkipMarker on
    first miss, None on full success. shutil.which is the chosen probe
    (no exec round-trip; cheap; consistent with VF-019 inv-4)."""
    for binary in ("soffice", "pdftoppm"):
        if shutil.which(binary) is None:
            return _SkipMarker(reason=f"{binary}_unavailable")
    return None


def _render_pdf(docx_path: Path, out_dir: Path) -> Path:
    """soffice headless --convert-to pdf. Returns the produced .pdf path."""
    subprocess.run(
        [
            "soffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(docx_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_RENDER_TIMEOUT_SEC,
    )
    pdf = out_dir / (docx_path.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError(f"soffice did not produce {pdf}")
    return pdf


def _render_pngs(pdf_path: Path, out_dir: Path, max_pages: int) -> list[Path]:
    """pdftoppm -png -r 110 -f 1 -l max_pages. Returns the page-*.png list."""
    base = out_dir / "page"
    subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-r",
            "110",
            "-f",
            "1",
            "-l",
            str(max_pages),
            str(pdf_path),
            str(base),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_RENDER_TIMEOUT_SEC,
    )
    return sorted(out_dir.glob("page-*.png"))


def _call_vlm(image_path: Path, page_no: int, total_pages: int) -> dict[str, Any]:
    """Send a single rendered page to the VLM. Raises on network /
    transport / decode failure — the caller is responsible for the
    try/except boundary that maps these to skipped/None."""
    img_b64 = base64.b64encode(image_path.read_bytes()).decode()
    prompt = _PROMPT_TEMPLATE.format(page_no=page_no, total_pages=total_pages)
    payload = {
        "model": _VLM_MODEL,
        "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 2000},
    }
    req = urllib.request.Request(
        _OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_VLM_TIMEOUT_SEC) as r:
        data = json.loads(r.read())
    raw = data["message"]["content"]
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return dict(json.loads(cleaned))
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            return dict(json.loads(m.group(0)))
        # Unparseable — treat as zeroed result + record the head of the raw
        # response as an issue for ops triage.
        return {
            "typography": 0,
            "layout": 0,
            "visual_richness": 0,
            "professional": 0,
            "issues": [f"VLM returned unparseable: {raw[:200]}"],
            "highlights": [],
        }


def _aggregate(per_page: list[dict[str, Any]]) -> tuple[float, dict[str, int], list[str]]:
    """Mean axes -> aggregate score + per-axis ints + flattened issues."""
    axes_out: dict[str, int] = {}
    for axis in _AXES:
        vals = [int(p.get(axis, 0) or 0) for p in per_page]
        axes_out[axis] = round(mean(vals)) if vals else 0
    score = round(mean(axes_out.values()), 1) if axes_out else 0.0
    all_issues: list[str] = []
    for i, p in enumerate(per_page, 1):
        for it in p.get("issues") or []:
            all_issues.append(f"page {i}: {it}")
    return score, axes_out, all_issues


def score_document(
    document_path: Path,
    preset_name: str = "klawd",
    *,
    threshold: int = 70,
    max_pages: int = 5,
) -> VisualQAReport | None:
    """Advisory visual-QA scorer. NEVER raises into the caller.

    Returns:
        None when MINT_SKIP_VISUAL_QA=1 (caller opt-out).
        VisualQAReport(skipped=True, ...) on backend / network / decode failure.
        VisualQAReport(skipped=False, ...) on the happy path.
    """
    if os.environ.get("MINT_SKIP_VISUAL_QA") == "1":
        # Env-opt-out: explicit None signal so the caller omits the key entirely.
        return None

    # Backend probes — fast-fail with a graceful skipped report when the
    # rendering toolchain is missing. The hook caller treats this as a
    # successful (advisory) signal: ops should know, but the docx ships.
    probe_skip = _probe_backends()
    if probe_skip is not None:
        logger.warning(
            "[MP-VisualQA][score][BLOCK_QA_BACKEND_UNAVAILABLE] reason=%s",
            probe_skip.reason,
        )
        return VisualQAReport(
            score=0.0,
            axes=dict.fromkeys(_AXES, 0),
            issues=[],
            pages_scored=0,
            preset_name=preset_name,
            advisory=True,
            skipped=True,
            skip_reason=probe_skip.reason,
        )

    workdir = Path(tempfile.mkdtemp(prefix="mint_qa_"))
    try:
        try:
            pdf = _render_pdf(document_path, workdir)
            pngs = _render_pngs(pdf, workdir, max_pages)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
            RuntimeError,
        ) as exc:
            reason = f"render_failed:{type(exc).__name__}"
            logger.warning(
                "[MP-VisualQA][score][BLOCK_QA_BACKEND_UNAVAILABLE] reason=%s",
                reason,
            )
            return VisualQAReport(
                score=0.0,
                axes=dict.fromkeys(_AXES, 0),
                issues=[],
                pages_scored=0,
                preset_name=preset_name,
                advisory=True,
                skipped=True,
                skip_reason=reason,
            )

        if not pngs:
            logger.warning(
                "[MP-VisualQA][score][BLOCK_QA_BACKEND_UNAVAILABLE] reason=no_pages_rendered",
            )
            return VisualQAReport(
                score=0.0,
                axes=dict.fromkeys(_AXES, 0),
                issues=[],
                pages_scored=0,
                preset_name=preset_name,
                advisory=True,
                skipped=True,
                skip_reason="no_pages_rendered",
            )

        per_page: list[dict[str, Any]] = []
        total = len(pngs)
        for idx, png in enumerate(pngs, 1):
            try:
                per_page.append(_call_vlm(png, idx, total))
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                ConnectionError,
                TimeoutError,
            ) as exc:
                reason = f"ollama_unreachable:{type(exc).__name__}"
                logger.warning(
                    "[MP-VisualQA][score][BLOCK_QA_BACKEND_UNAVAILABLE] reason=%s",
                    reason,
                )
                return VisualQAReport(
                    score=0.0,
                    axes=dict.fromkeys(_AXES, 0),
                    issues=[],
                    pages_scored=0,
                    preset_name=preset_name,
                    advisory=True,
                    skipped=True,
                    skip_reason=reason,
                )

        score, axes_out, issues = _aggregate(per_page)
        logger.info(
            "[MP-VisualQA][score][BLOCK_QA_SCORE] "
            "preset=%s pages_scored=%d score=%d axes=%s",
            preset_name,
            total,
            round(score),
            axes_out,
        )
        if score < threshold:
            logger.warning(
                "[MP-VisualQA][score][BLOCK_QA_BELOW_THRESHOLD] "
                "score=%d threshold=%d preset=%s",
                round(score),
                threshold,
                preset_name,
            )
        return VisualQAReport(
            score=score,
            axes=axes_out,
            issues=issues,
            pages_scored=total,
            preset_name=preset_name,
            advisory=True,
            skipped=False,
            skip_reason=None,
        )
    except Exception as exc:
        # Final guard — the contract promises score_document NEVER raises.
        # Any unexpected exception (parse error, KeyError on a malformed
        # response shape, etc.) collapses into a skipped report.
        reason = f"unexpected:{type(exc).__name__}"
        logger.warning(
            "[MP-VisualQA][score][BLOCK_QA_BACKEND_UNAVAILABLE] reason=%s",
            reason,
        )
        return VisualQAReport(
            score=0.0,
            axes=dict.fromkeys(_AXES, 0),
            issues=[],
            pages_scored=0,
            preset_name=preset_name,
            advisory=True,
            skipped=True,
            skip_reason=reason,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["VisualQAReport", "score_document"]
