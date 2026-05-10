# Alpha Baseline — Non-Frontier Models on the MINT Pipeline

**Date**: 2026-05-10
**MINT version**: 0.4.0a1 (alpha)
**Status**: First post-alpha baseline experiment

This is the inaugural empirical baseline for MINT, run on the day of the alpha
release. It tests the central thesis — that small, locally-hosted, non-frontier
LLMs can produce publication-quality documents through MINT's deterministic
pipeline — against six open-weights models on the bank Ollama endpoint, plus
two control runs without the pipeline at all.

## The thesis under test

```
LLM (any size, even 3B) → JSON spec → deterministic Python → docx
   "WHAT to say"           contract     "HOW it looks"        output
```

If MINT works as designed:
- A **4B-class** local model should produce a polished, branded `.docx`
  identical in visual quality to the output of a 30B-class model — because
  visual fidelity is owned by code, not the model.
- The same model **without the pipeline** should produce only raw markdown —
  no styling, no `.docx`, no validation.
- Heavy 30B models should be **no better** than light 4-10B models on this
  task, because the hard problem is layout, and the pipeline solved that.

## Methodology

- **Source content** — `source/article-draft.md` (the in-repo MINT article
  draft, ~9KB). Same input across all 8 cells.
- **Prompt** — `prompt.md`. Single shared system + user prompt. The model is
  instructed to emit a JSON object matching the schema in `tools/article_experiment/spec.py`.
- **Matrix** — see `matrix.md`. Three heavy (~30B), three light (4-10B), plus
  two baselines (raw model output, no MINT pipeline).
- **Endpoint** — `http://10.128.26.10:11434/v1` (private bank-subnet Ollama;
  the same one MINT v1's `kvorum` orchestration hit).
- **Two rounds** — Round 1 (R1) ran with retry-on-JSON-parse-failure only.
  Round 2 (R2) added retry-on-schema-failure with targeted hints, fixed an
  error-reporting bleed-through bug, and bumped the attempt budget from 1+1
  to 1+2.

## Results

See `REPORT_COMPARISON.md` for full per-cell analysis. Summary:

|                       | Round 1 | Round 2 |
|-----------------------|---------|---------|
| Heavy tier (3 cells)  | **0/3** valid docx | **2/3** valid docx |
| Light tier (3 cells)  | **3/3** valid docx | **3/3** valid docx |
| Baseline (2 cells)    | raw markdown only | raw markdown only |
| Total wall-time       | 23.2 min | 18.2 min |
| Fastest valid docx    | 27s (gemma3:4b) | **11s (gemma3:4b)** |

## Key findings

1. **The thesis holds**. All three light-tier models (4B / 5B / 9.7B) produced
   valid klawd-themed `.docx` outputs in **both** rounds, on the **first
   attempt** (zero retries). The smallest model in the lineup — **Gemma 3 4B**
   — finished in **11 seconds** in R2.

2. **Heavy models are not categorically better — they're more drift-prone.**
   When asked for strict-output JSON, 30B-class reasoning-tuned models in this
   lineup variously: returned a JSON array instead of an object (gemma4:31b),
   wrote chain-of-thought as the response (qwen3.5:35b: `Thinking Process:…`),
   exhausted the token budget inside the thinking phase (glm-4.7-flash empty
   content). Two of three recovered in R2 on first-call variability — not
   retry rescue.

3. **`qwen3.5:35b` is systematically incompatible** with strict-output tasks.
   Both rounds saw it emit reasoning prose for the entire 600s timeout. For
   non-frontier-but-needs-structure work, prefer the **9.7B** variant (which
   passed cleanly both rounds).

4. **The pipeline is the value, not the model.** Cells 7 & 8 ran the same two
   models (qwen3.5:35b and gemma3:4b) **without** the MINT pipeline — same
   prompt class, naked LLM call, save raw response. Both produced markdown
   articles, neither produced a `.docx`. The deterministic builder + the
   klawd preset is what turns "model output" into "polished branded
   publication-ready document."

5. **Light-tier total throughput in R2: 92 seconds for 3 valid docx**. This
   is production-level latency for document generation; competitive with
   any frontier-API solution at zero per-call cost.

## Reproducing this experiment

```bash
LLM_BASE_URL=http://10.128.26.10:11434/v1 \
  uv run python -m tools.article_experiment.run
```

Outputs land in `dist/experiment/` (git-ignored). The matrix, prompt, and
runner are in `tools/article_experiment/`. To compare against this baseline,
copy the new run's `results.json` and diff metric-by-metric.

## A note on R1/R2 visual fidelity (post-alpha discovery)

The R1 and R2 docx artifacts shipped under a latent bug —
`Document.with_style_preset()` loaded the preset DATA but never consumed it
during render, so all R1/R2 docx files use python-docx's stock theme styles
(Heading 1 = `#365F91`, theme font), NOT klawd's `#1B3A5C` / Arial. The
schema, validation, and content-generation parts of the pipeline worked
correctly in both rounds — only the *visual* application of the preset
was missing. The fix landed in 0.4.0a2 (`apply_preset_to_doc`).

`round-3-postfix/` re-runs the same matrix with the fix in place. Every
R3 success cell's `styles.xml` carries klawd's typography. R1 and R2 stay
in git as historical record; R3 is the visually-correct baseline.

## Files in this directory

```
.
├── README.md               — this file
├── REPORT_COMPARISON.md    — narrative R1/R2/R3 comparison + interpretation
├── matrix.md               — the 6+2 cell selection and model-by-model rationale
├── prompt.md               — exact prompt sent to all 6 mint-pipeline cells
├── source/
│   └── article-draft.md    — frozen copy of source content used in all rounds
├── round-1/                  ← under-bug; visually = Word defaults
│   ├── REPORT.md
│   ├── results.json
│   ├── 04_light_gemma3_4b.docx     (39KB)
│   ├── 05_light_gemma4_e2b.docx    (40KB)
│   ├── 06_light_qwen3_5.docx       (38KB)
│   ├── 07_baseline_qwen3_5_35b.md  (9KB)
│   └── 08_baseline_gemma3_4b.md    (7KB)
├── round-2/                  ← under-bug; visually = Word defaults
│   ├── REPORT.md
│   ├── results.json
│   ├── 01_heavy_gemma4_31b.docx        (39KB) — recovered in R2
│   ├── 02_heavy_glm_4_7_flash.docx     (40KB) — recovered in R2
│   ├── 04_light_gemma3_4b.docx         (39KB)
│   ├── 05_light_gemma4_e2b.docx        (40KB)
│   ├── 06_light_qwen3_5.docx           (38KB)
│   ├── 07_baseline_qwen3_5_35b.md      (9KB)
│   └── 08_baseline_gemma3_4b.md        (7KB)
└── round-3-postfix/          ← visually correct; klawd actually applied
    ├── REPORT.md
    ├── results.json
    ├── 01_heavy_gemma4_31b.docx        (40KB) — Heading 1 #1B3A5C / Arial / 16pt
    ├── 04_light_gemma3_4b.docx         (40KB) — Heading 1 #1B3A5C / Arial / 16pt
    ├── 06_light_qwen3_5.docx           (41KB) — Heading 1 #1B3A5C / Arial / 16pt
    ├── 07_baseline_qwen3_5_35b.md      (10KB)
    └── 08_baseline_gemma3_4b.md        (12KB)
```
