# FILE: tools/article_experiment/run.py
# VERSION: 0.1.0
"""Main experiment orchestrator — runs the 6+2 matrix sequentially.

Usage:
    LLM_BASE_URL=http://10.128.26.10:11434/v1 \\
    uv run python -m tools.article_experiment.run

Environment overrides (optional):
    ARTICLE_SOURCE — path to source markdown (default docs/archive/article-draft.md)
    EXPERIMENT_DIR — output dir (default dist/experiment)
    SKIP_WARMUP    — set to 1 to skip per-model cold-load ping

The 6+2 matrix is settled and lives in MATRIX below — see the
'Article experiment' memory note for the rationale.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .runner import CellResult, run_baseline, run_mint, warmup

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# (cell_id, model_tag, mode) — cell_id keeps the output filenames stable
# and report-row order deterministic. "mode" is only mint or baseline.
MATRIX: list[tuple[str, str, str]] = [
    # Heavy tier (~30B)
    ("01_heavy_gemma4_31b", "gemma4:31b", "mint"),
    ("02_heavy_glm_4_7_flash", "glm-4.7-flash:latest", "mint"),
    ("03_heavy_qwen3_5_35b", "qwen3.5:35b", "mint"),
    # Light tier (4–10B)
    ("04_light_gemma3_4b", "gemma3:4b", "mint"),
    ("05_light_gemma4_e2b", "gemma4:e2b", "mint"),
    ("06_light_qwen3_5", "qwen3.5:latest", "mint"),
    # Baselines (no MINT pipeline)
    ("07_baseline_qwen3_5_35b", "qwen3.5:35b", "baseline"),
    ("08_baseline_gemma3_4b", "gemma3:4b", "baseline"),
]


def main() -> int:
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    if not base_url:
        print("ERROR: LLM_BASE_URL not set", file=sys.stderr)
        return 2

    source_path = Path(
        os.environ.get(
            "ARTICLE_SOURCE",
            str(REPO_ROOT / "docs" / "archive" / "article-draft.md"),
        )
    )
    if not source_path.exists():
        print(f"ERROR: source markdown not found at {source_path}", file=sys.stderr)
        return 2

    out_dir = Path(
        os.environ.get(
            "EXPERIMENT_DIR", str(REPO_ROOT / "dist" / "experiment")
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    article = source_path.read_text(encoding="utf-8")
    skip_warmup = os.environ.get("SKIP_WARMUP") == "1"

    print(f"[experiment] base_url={base_url}")
    print(f"[experiment] source={source_path} ({len(article)} chars)")
    print(f"[experiment] out={out_dir}")
    print(f"[experiment] cells={len(MATRIX)}, skip_warmup={skip_warmup}")
    print()

    results: list[CellResult] = []
    overall_start = time.monotonic()

    # Warm each unique model once before the timed runs so the first
    # cell isn't penalized with cold-load latency.
    if not skip_warmup:
        seen: set[str] = set()
        for _, model, _ in MATRIX:
            if model in seen:
                continue
            seen.add(model)
            print(f"[warmup] {model}", flush=True)
            dt = warmup(base_url, model)
            print(f"[warmup]   {model}: {dt:.1f}s", flush=True)
        print()

    for cell_id, model, mode in MATRIX:
        print(f"[cell] {cell_id} ({model}, {mode})", flush=True)
        if mode == "mint":
            result = run_mint(
                model=model,
                base_url=base_url,
                article_markdown=article,
                out_dir=out_dir,
                cell_id=cell_id,
            )
        else:
            result = run_baseline(
                model=model,
                base_url=base_url,
                article_markdown=article,
                out_dir=out_dir,
                cell_id=cell_id,
            )
        results.append(result)
        _print_cell_summary(cell_id, result)

    total = time.monotonic() - overall_start
    print(f"\n[experiment] total wall time: {total:.1f}s")

    # Persist results.json
    results_path = out_dir / "results.json"
    results_path.write_text(
        json.dumps([asdict(r) for r in results], indent=2),
        encoding="utf-8",
    )
    print(f"[experiment] wrote {results_path}")

    # Render report.md
    report_path = out_dir / "REPORT.md"
    report_path.write_text(_render_report(results, total), encoding="utf-8")
    print(f"[experiment] wrote {report_path}")
    return 0


def _print_cell_summary(cell_id: str, r: CellResult) -> None:
    if r.error:
        print(
            f"[cell]   {cell_id}: FAIL ({r.duration_s:.1f}s, "
            f"json={r.json_parse_ok} schema={r.schema_valid}) — {r.error[:120]}"
        )
        return
    if r.mode == "mint_pipeline":
        print(
            f"[cell]   {cell_id}: ok ({r.duration_s:.1f}s, "
            f"in={r.tokens_in} out={r.tokens_out} retries={r.retries}, "
            f"docx={r.output_size_bytes // 1024}KB lenient={r.docx_lenient_passed})"
        )
    else:
        print(
            f"[cell]   {cell_id}: ok ({r.duration_s:.1f}s, "
            f"in={r.tokens_in} out={r.tokens_out}, "
            f"raw={r.output_size_bytes // 1024}KB)"
        )


def _render_report(results: list[CellResult], total_seconds: float) -> str:
    lines: list[str] = []
    lines.append("# MINT Article Experiment — Results")
    lines.append("")
    lines.append(f"Total wall time: **{total_seconds:.1f}s** across {len(results)} cells.")
    lines.append("")
    lines.append("## Per-cell summary")
    lines.append("")
    lines.append(
        "| Cell | Model | Mode | Time | Tok in/out | Retry | JSON | Schema | "
        "Docx lenient | Output |"
    )
    lines.append("|---|---|---|---:|---|---:|---|---|---|---|")
    for r in results:
        cell_id = (
            Path(r.output_path).stem if r.output_path else r.model.replace(":", "_")
        )
        out_repr = (
            f"[{Path(r.output_path).name}]({Path(r.output_path).name}) ({r.output_size_bytes // 1024}KB)"
            if r.output_path
            else "—"
        )
        lenient = (
            "✓" if r.docx_lenient_passed
            else "✗" if r.docx_lenient_passed is False
            else "—"
        )
        json_ok = "✓" if r.json_parse_ok else ("—" if r.mode == "baseline_raw" else "✗")
        schema_ok = "✓" if r.schema_valid else ("—" if r.mode == "baseline_raw" else "✗")
        lines.append(
            f"| {cell_id} | `{r.model}` | {r.mode} | {r.duration_s:.1f}s | "
            f"{r.tokens_in}/{r.tokens_out} | {r.retries} | {json_ok} | {schema_ok} | "
            f"{lenient} | {out_repr} |"
        )

    lines.append("")
    lines.append("## Mint pipeline cells")
    lines.append("")
    lines.append("Each cell below ran the same prompt through the same pipeline; the only delta is the model.")
    for r in results:
        if r.mode != "mint_pipeline":
            continue
        lines.append("")
        lines.append(f"### `{r.model}`")
        if r.error:
            lines.append(f"- **error**: `{r.error}`")
        lines.append(f"- duration: {r.duration_s:.1f}s")
        lines.append(f"- tokens (in/out): {r.tokens_in} / {r.tokens_out}")
        lines.append(f"- retries: {r.retries}")
        lines.append(f"- json parsed: {'yes' if r.json_parse_ok else 'no'}")
        lines.append(f"- schema valid: {'yes' if r.schema_valid else 'no'}")
        if r.schema_violations:
            for v in r.schema_violations:
                lines.append(f"  - {v}")
        if r.output_path:
            lines.append(f"- docx: `{Path(r.output_path).name}` ({r.output_size_bytes // 1024} KB)")
            if r.docx_lenient_passed is not None:
                lines.append(
                    f"- lenient validation: passed={r.docx_lenient_passed}, "
                    f"hard={r.docx_lenient_hard_count}"
                )
    lines.append("")
    lines.append("## Baseline cells (no MINT pipeline)")
    lines.append("")
    lines.append("Same prompt-class but no schema, no builder. Output is whatever the model returned.")
    for r in results:
        if r.mode != "baseline_raw":
            continue
        lines.append("")
        lines.append(f"### `{r.model}` (baseline)")
        if r.error:
            lines.append(f"- **error**: `{r.error}`")
        lines.append(f"- duration: {r.duration_s:.1f}s")
        lines.append(f"- tokens (in/out): {r.tokens_in} / {r.tokens_out}")
        if r.output_path:
            lines.append(f"- raw output: `{Path(r.output_path).name}` ({r.output_size_bytes // 1024} KB)")
        if r.raw_text_first_200:
            preview = r.raw_text_first_200.replace("\n", " ")
            lines.append(f"- preview: `{preview[:160]}…`")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
