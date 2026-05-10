# MINT Article Experiment — Results

Total wall time: **2557.3s** across 8 cells.

## Per-cell summary

| Cell | Model | Mode | Time | Tok in/out | Retry | JSON | Schema | Docx lenient | Output |
|---|---|---|---:|---|---:|---|---|---|---|
| 01_heavy_gemma4_31b | `gemma4:31b` | mint_pipeline | 625.4s | 6813/19986 | 1 | ✓ | ✓ | ✓ | [01_heavy_gemma4_31b.docx](01_heavy_gemma4_31b.docx) (39KB) |
| glm-4.7-flash_latest | `glm-4.7-flash:latest` | mint_pipeline | 600.1s | 0/0 | 0 | ✗ | ✗ | — | — |
| qwen3.5_35b | `qwen3.5:35b` | mint_pipeline | 1104.5s | 6772/51423 | 2 | ✓ | ✗ | — | — |
| 04_light_gemma3_4b | `gemma3:4b` | mint_pipeline | 28.6s | 3387/3676 | 0 | ✓ | ✓ | ✓ | [04_light_gemma3_4b.docx](04_light_gemma3_4b.docx) (40KB) |
| gemma4_e2b | `gemma4:e2b` | mint_pipeline | 85.6s | 10351/13761 | 2 | ✗ | ✗ | — | — |
| 06_light_qwen3_5 | `qwen3.5:latest` | mint_pipeline | 50.4s | 3345/4482 | 0 | ✓ | ✓ | ✓ | [06_light_qwen3_5.docx](06_light_qwen3_5.docx) (40KB) |
| 07_baseline_qwen3_5_35b | `qwen3.5:35b` | baseline_raw | 42.0s | 2457/3694 | 0 | — | — | — | [07_baseline_qwen3_5_35b.md](07_baseline_qwen3_5_35b.md) (10KB) |
| 08_baseline_gemma3_4b | `gemma3:4b` | baseline_raw | 20.4s | 2493/2929 | 0 | — | — | — | [08_baseline_gemma3_4b.md](08_baseline_gemma3_4b.md) (11KB) |

## Mint pipeline cells

Each cell below ran the same prompt through the same pipeline; the only delta is the model.

### `gemma4:31b`
- duration: 625.4s
- tokens (in/out): 6813 / 19986
- retries: 1
- json parsed: yes
- schema valid: yes
- docx: `01_heavy_gemma4_31b.docx` (39 KB)
- lenient validation: passed=True, hard=0

### `glm-4.7-flash:latest`
- **error**: `timeout after 600s`
- duration: 600.1s
- tokens (in/out): 0 / 0
- retries: 0
- json parsed: no
- schema valid: no

### `qwen3.5:35b`
- **error**: `timeout after 600s`
- duration: 1104.5s
- tokens (in/out): 6772 / 51423
- retries: 2
- json parsed: yes
- schema valid: no
  - spec.title is required and must be a non-empty string

### `gemma3:4b`
- duration: 28.6s
- tokens (in/out): 3387 / 3676
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `04_light_gemma3_4b.docx` (40 KB)
- lenient validation: passed=True, hard=0

### `gemma4:e2b`
- **error**: `JSON parse failed: Expecting ',' delimiter: line 395 column 7 (char 14948)`
- duration: 85.6s
- tokens (in/out): 10351 / 13761
- retries: 2
- json parsed: no
- schema valid: no

### `qwen3.5:latest`
- duration: 50.4s
- tokens (in/out): 3345 / 4482
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `06_light_qwen3_5.docx` (40 KB)
- lenient validation: passed=True, hard=0

## Baseline cells (no MINT pipeline)

Same prompt-class but no schema, no builder. Output is whatever the model returned.

### `qwen3.5:35b` (baseline)
- duration: 42.0s
- tokens (in/out): 2457 / 3694
- raw output: `07_baseline_qwen3_5_35b.md` (10 KB)
- preview: `# MINT: Model-Independent Normalization Toolkit ### Ensuring Consistent Document Quality Across Any LLM  ## Prologue: The Broken Document  Imagine this scenario…`

### `gemma3:4b` (baseline)
- duration: 20.4s
- tokens (in/out): 2493 / 2929
- raw output: `08_baseline_gemma3_4b.md` (11 KB)
- preview: ````word # MINT: Model-Independent Normalization Toolkit  ## Article Draft (WIP — for alpha/beta release)  **Working Title**: “MINT: How We Taught Any LLM to Gen…`
