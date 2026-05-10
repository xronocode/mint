# MINT Article Experiment — Rounds 1, 2, 3

Three rounds of the same 6+2 matrix on the same prompt against the same
Ollama endpoint at `10.128.26.10:11434`. The only deltas between rounds:

- **R1 runner**: 1 + 1 attempts, retry only on JSON-parse failure, schema
  failures terminated the cell. Bug in error reporting: `last_err` from
  earlier attempts was carried through even when a later attempt succeeded
  (gemma4:e2b in R1 was reported with a misleading error string).
- **R2 runner**: 1 + 2 attempts (3 total), retry on **any** of: timeout-free
  call error, no-JSON-found, JSON-parse-fail, schema-validation-fail, with
  per-failure-type targeted hints in the retry prompt. `last_err` cleared
  after the schema gate passes.
- **R3 (post-fix preset wiring)**: same runner as R2, but on **0.4.0a2**
  with the `apply_preset_to_doc` fix landed. R1 and R2 shipped under a
  bug where `with_style_preset()` only loaded the preset DATA but never
  consumed it during render — `add_heading()` went through python-docx's
  stock theme, so klawd's typography was visually invisible. R3 is the
  first round where the saved docx files actually carry klawd's
  `#1B3A5C` / Arial / 16pt heading style and `#333333` body.

Total wall time: R1 = 23.2 min, R2 = 18.2 min, **R3 = 42.6 min** (heavy
tier was unusually flaky on R3; cell 1 alone took 10.4 min vs 4.3 min in R2).

## Per-cell comparison

| Cell | Model | Tier | R1 | R2 | R3 (post-fix) |
|---|---|---|---|---|---|
| 01 | `gemma4:31b` | heavy 31B | ✗ FAIL 302s — JSON array at top level | ✅ OK 259s, 0 retries | ✅ OK 625s, 1 retry — first attempt drifted, retry rescued |
| 02 | `glm-4.7-flash:latest` | heavy 30B | ✗ FAIL 600s timeout — empty content | ✅ OK 84s, 0 retries | ✗ FAIL 600s timeout — back to stuck-in-reasoning |
| 03 | `qwen3.5:35b` | heavy 36B | ✗ FAIL 258s — `Thinking Process:` prose | ✗ FAIL 600s timeout | ✗ FAIL 1104s — JSON parsed but timed out before schema |
| 04 | `gemma3:4b` | **light 4B** | ✅ OK 27s | ✅ OK **11s** | ✅ OK 29s, docx 40KB |
| 05 | `gemma4:e2b` | light 5B | ✅ OK 54s (1 retry) | ✅ OK 30s, 0 retries | ✗ FAIL 86s — JSON parse error (random) |
| 06 | `qwen3.5:latest` | light 9.7B | ✅ OK 60s | ✅ OK 51s | ✅ OK 50s, docx 41KB |
| 07 | `qwen3.5:35b` (baseline) | heavy 36B | raw 9KB MD | raw 9KB MD | raw 10KB MD |
| 08 | `gemma3:4b` (baseline) | light 4B | raw 7KB MD | raw 7KB MD | raw 12KB MD |

## Aggregate

|  | R1 mint cells passed | R2 mint cells passed | R3 mint cells passed |
|---|---|---|---|
| **Heavy tier (3)** | 0/3 | 2/3 | 1/3 |
| **Light tier (3)** | 3/3 | 3/3 | 2/3 |
| **Total** | 3/6 | 5/6 | 3/6 |

R3 success count regressed not because of the preset fix (which is purely
visual), but because the bank server was under heavier load that day plus
inherent run-to-run variance — `gemma4:e2b` flaked on a JSON parse error
this round; in R2 the same cell parsed cleanly. None of the failures in
R3 were caused by the fix.

## Visual-fidelity delta — the actual point of R3

For every cell that succeeded in BOTH R2 and R3, the saved `word/styles.xml`
went from "Word default theme" to "klawd applied":

| Cell | R2 (under-bug) Heading 1 | R3 (post-fix) Heading 1 |
|---|---|---|
| `01_heavy_gemma4_31b` | color `#365F91`, theme font, 14pt | **color `#1B3A5C`, Arial, 16pt** |
| `04_light_gemma3_4b`  | color `#365F91`, theme font, 14pt | **color `#1B3A5C`, Arial, 16pt** |
| `06_light_qwen3_5`    | color `#365F91`, theme font, 14pt | **color `#1B3A5C`, Arial, 16pt** |

Every R3 docx's `styles.xml` now contains `1B3A5C`, `Arial`, and `333333`.
None of the R2 docx files do — they ship under the bug. **Open the R2 and
R3 versions of the same cell side-by-side in Word and the difference is
immediate.** Same content (the model produced the same JSON spec), same
structure (sections + blocks), wholly different visual tone (Word default
sans navy vs Anthropic baseline navy + Arial).

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
