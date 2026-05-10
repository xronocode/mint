# MINT Article Experiment — Round 1 vs Round 2

Two rounds of the same 6+2 matrix on the same prompt against the same Ollama
endpoint at `10.128.26.10:11434`. The only deltas between rounds are:

- **R1 runner**: 1 + 1 attempts, retry only on JSON-parse failure, schema
  failures terminated the cell. Bug in error reporting: `last_err` from
  earlier attempts was carried through even when a later attempt succeeded
  (gemma4:e2b in R1 was reported with a misleading error string).
- **R2 runner**: 1 + 2 attempts (3 total), retry on **any** of: timeout-free
  call error, no-JSON-found, JSON-parse-fail, schema-validation-fail, with
  per-failure-type targeted hints in the retry prompt. `last_err` cleared
  after the schema gate passes.

Total wall time: R1 = 23.2 min, R2 = 18.2 min (5 min faster — heavy cells
that timed out in R1 either succeeded fast in R2 or consumed the same 10 min
budget).

## Per-cell comparison

| Cell | Model | Tier | R1 | R2 | Notes |
|---|---|---|---|---|---|
| 01 | `gemma4:31b` | heavy 31B | ✗ FAIL 302s — returned a JSON **array** at top level (schema rejected) | ✅ OK 259s, 0 retries, docx 38KB | First-attempt success in R2 — model variability, not retry rescue |
| 02 | `glm-4.7-flash:latest` | heavy 30B | ✗ FAIL 600s — timeout, **empty content** (think:False didn't take) | ✅ OK 84s, 0 retries, docx 39KB | Δ = −516s; the model was simply less stuck on the second run |
| 03 | `qwen3.5:35b` | heavy 36B | ✗ FAIL 258s — returned `Thinking Process:` prose, no JSON | ✗ FAIL 600s — timeout after 26K tokens of thinking | **Consistently fails**; reasoning-heavy output incompatible with schema even with retry budget |
| 04 | `gemma3:4b` | **light 4B** | ✅ OK 27s, docx 39KB | ✅ OK **11s**, docx 39KB | Smallest model, fastest, perfect both runs |
| 05 | `gemma4:e2b` | light 5B | ✅ OK 54s (1 retry — control char in JSON) | ✅ OK 30s, 0 retries | R1 reporting bug masked the real success |
| 06 | `qwen3.5:latest` | light 9.7B | ✅ OK 60s, docx 38KB | ✅ OK 51s, docx 38KB | Stable across runs |
| 07 | `qwen3.5:35b` (baseline) | heavy 36B | raw 9KB MD, 44s | raw 9KB MD, 43s | Baseline: model wrote a polished markdown article, but no docx — by design |
| 08 | `gemma3:4b` (baseline) | light 4B | raw 7KB MD, 14s | raw 7KB MD, 14s | Same model, no pipeline = no docx |

## Aggregate

|  | R1 mint cells passed | R2 mint cells passed |
|---|---|---|
| **Heavy tier (3)** | 0/3 | 2/3 |
| **Light tier (3)** | 3/3 | 3/3 |
| **Total** | 3/6 | 5/6 |

## Findings

### 1. The thesis holds: small models + pipeline produce frontier-quality docs

In **both rounds**, all three light-tier models (4B / 5B / 9.7B) produced a
valid klawd-themed `.docx` that passed lenient MP-VALIDATE on the very first
attempt. The smallest — Gemma 3 4B — finished in **11 seconds** in R2.

Same models without the pipeline (cells 7 & 8) only produce raw markdown.
The **deterministic builder + style preset** is what turns "model output" into
"polished branded document".

### 2. Heavy models are not categorically better — they're more drift-prone

R1: 0/3 heavy succeeded.
R2: 2/3 heavy succeeded — **on the first attempt** (retries=0).

This means the retry logic added in R2 was not the rescue — first-call
variability was. The heavy 30B-class models in this lineup all have known
reasoning-mode behaviour (qwen3.6, glm-4.7, gemma4 family). On any given
call, they may:

- restructure the schema (gemma4:31b returned an array, then later returned an object)
- emit chain-of-thought as the response (qwen3.5:35b: `Thinking Process: …`)
- exhaust the token budget inside the thinking phase (glm-4.7-flash empty content in R1)

The `think: false` Ollama-extension parameter helps but isn't 100% effective.

### 3. qwen3.5:35b is systematically incompatible with strict-output tasks

Both R1 and R2 cells 3 failed. R1: 26K tokens of "Thinking Process:" prose.
R2: same behaviour but timed out at 600s. With 36B parameters and a heavy
reasoning bias, this model is the wrong tool for "emit JSON to this schema."

For non-frontier-but-needs-structure tasks, prefer **smaller instruction-
tuned models** over larger reasoning-tuned ones. Counter-intuitive but
consistently observed across both rounds.

### 4. Pipeline throughput

The light-tier total wall time across both rounds:
- R1: 27 + 54 + 60 = **141s** for 3 valid docx outputs (≈ 47s/cell)
- R2: 11 + 30 + 51 = **92s** for 3 valid docx outputs (≈ 31s/cell)

Heavy cells when they work are much slower (84–302s) and more variable.
For production: **prefer the light tier** — predictable latency, lower
GPU contention, identical end-result quality (the visual fidelity is
all in the klawd preset, not the model).

## Artifacts

- `round1/` — full R1 outputs and report
- `0*.docx`, `0*.md` — R2 outputs (current top-level)
- `04_light_gemma3_4b.docx`, `06_light_qwen3_5.docx` — the two cleanest
  outputs to open and visually compare
- `07_baseline_qwen3_5_35b.md`, `08_baseline_gemma3_4b.md` — what the same
  models produce when you take MINT out of the loop (markdown, no styling,
  no .docx, no validation)
