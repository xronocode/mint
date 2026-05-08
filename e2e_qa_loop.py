"""Master quality loop: generate → strict-validate → VLM-score → diagnose.

One invocation = one iteration. The driver writes per-iteration results to
e2e_results/quality_review/iter_log.jsonl and prints a concise summary so an
outer agent can decide what to fix next.

Usage:
    python e2e_qa_loop.py --label q2
    # exit 0 if visual score ≥ threshold, exit 1 if needs more work, 2 on error
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent
RESULTS = ROOT / "e2e_results"
QR_DIR = RESULTS / "quality_review"
LOG_PATH = QR_DIR / "iter_log.jsonl"


def run_generate() -> tuple[bool, Path | None, str]:
    """Run e2e_sidecar_report.py and return (ok, output_path, summary)."""
    proc = subprocess.run(
        ["uv", "run", "--quiet", "python", "e2e_sidecar_report.py"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=3000,
    )
    out = proc.stdout
    err = proc.stderr
    if proc.returncode != 0:
        return False, None, f"generate failed: rc={proc.returncode} | {err[-300:]}"
    # Parse "Output: <path>" line
    output_path = None
    for line in out.splitlines():
        if line.startswith("Output: ") and not line.endswith("None"):
            output_path = Path(line.split("Output: ", 1)[1].strip())
            break
    if output_path is None or not output_path.exists():
        return False, None, f"generate produced no file. stdout: {out[-300:]}"
    return True, output_path, out[-300:]


def run_strict(docx: Path) -> dict:
    proc = subprocess.run(
        ["uv", "run", "--quiet", "python", "e2e_qa_strict.py", str(docx)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    if proc.stdout:
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    return {"ok": False, "issues": [{"severity": "fatal", "code": "QA_STRICT_PARSE",
                                     "msg": proc.stderr[:300]}]}


def run_visual(docx: Path, pages: int, threshold: float) -> dict:
    proc = subprocess.run(
        ["uv", "run", "--quiet", "python", "e2e_qa_visual.py",
         str(docx), "--pages", str(pages), "--threshold", str(threshold)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=900,
    )
    if proc.stdout:
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    return {"ok": False, "error": proc.stderr[-300:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None,
                    help="suffix for e2e_results/sidecar_report_<label>.docx")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=70.0)
    ap.add_argument("--no-archive", action="store_true",
                    help="don't copy output to e2e_results/")
    args = ap.parse_args()

    QR_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now(UTC).isoformat()
    print(f"=== qa-loop {started} label={args.label or '-'} ===", flush=True)

    t0 = time.monotonic()
    print("→ generating...", flush=True)
    ok, out_path, gen_summary = run_generate()
    if not ok:
        print(f"✗ generate failed: {gen_summary}", flush=True)
        return 2
    gen_secs = time.monotonic() - t0
    print(f"  generated in {gen_secs:.1f}s: {out_path}", flush=True)

    archive_path = None
    if not args.no_archive and args.label:
        archive_path = RESULTS / f"sidecar_report_{args.label}.docx"
        shutil.copy(out_path, archive_path)
        print(f"  archived: {archive_path}", flush=True)
    target = archive_path or out_path

    print("→ strict validate...", flush=True)
    strict = run_strict(target)
    sc = strict.get("counts", {})
    print(f"  strict: ok={strict.get('ok')} fatal={sc.get('fatal',0)} "
          f"high={sc.get('high',0)} medium={sc.get('medium',0)} "
          f"low={sc.get('low',0)}", flush=True)

    print("→ VLM scoring (this takes ~5min)...", flush=True)
    visual = run_visual(target, args.pages, args.threshold)
    if not visual.get("ok"):
        print(f"  ✗ visual scoring failed: {visual.get('error', '')[-200:]}",
              flush=True)
        scores = {"overall": 0}
        per_page = []
        issues = []
    else:
        scores = visual["scores"]
        per_page = visual["per_page"]
        issues = visual["issues"]
        print(f"  visual: overall={scores['overall']} (range "
              f"{visual['min_overall']}-{visual['max_overall']})", flush=True)
        print(f"    typography={scores['typography']} "
              f"layout={scores['layout']} "
              f"visual_richness={scores['visual_richness']} "
              f"professional={scores['professional']}", flush=True)
        for p in per_page:
            print(f"    p{p['page']}: {p.get('overall', 0)}", flush=True)

    # Append to iter log
    log_entry = {
        "timestamp": started,
        "label": args.label,
        "output_path": str(target),
        "gen_secs": round(gen_secs, 1),
        "strict_ok": strict.get("ok"),
        "strict_counts": sc,
        "visual_scores": scores,
        "visual_min_overall": visual.get("min_overall", 0),
        "visual_max_overall": visual.get("max_overall", 0),
        "issues": issues,
    }
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    # Top de-duped issues
    seen = set()
    print("\n→ TOP issues:", flush=True)
    for it in issues:
        if it not in seen:
            print(f"  - {it}", flush=True)
            seen.add(it)
        if len(seen) >= 12:
            break

    overall = scores.get("overall", 0)
    if overall >= args.threshold and strict.get("ok"):
        print(f"\n✅ TARGET REACHED: visual={overall} ≥ {args.threshold}, "
              "strict ok", flush=True)
        return 0
    print(f"\n● needs more work: visual={overall}, target={args.threshold}",
          flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
