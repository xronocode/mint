# MINT Article Experiment — Results

Total wall time: **1091.7s** across 8 cells.

## Per-cell summary

| Cell | Model | Mode | Time | Tok in/out | Retry | JSON | Schema | Docx lenient | Output |
|---|---|---|---:|---|---:|---|---|---|---|
| 01_heavy_gemma4_31b | `gemma4:31b` | mint_pipeline | 259.3s | 3389/9319 | 0 | ✓ | ✓ | ✓ | [01_heavy_gemma4_31b.docx](01_heavy_gemma4_31b.docx) (38KB) |
| 02_heavy_glm_4_7_flash | `glm-4.7-flash:latest` | mint_pipeline | 83.8s | 3188/7276 | 0 | ✓ | ✓ | ✓ | [02_heavy_glm_4_7_flash.docx](02_heavy_glm_4_7_flash.docx) (39KB) |
| qwen3.5_35b | `qwen3.5:35b` | mint_pipeline | 600.2s | 0/0 | 0 | ✗ | ✗ | — | — |
| 04_light_gemma3_4b | `gemma3:4b` | mint_pipeline | 11.3s | 3387/3340 | 0 | ✓ | ✓ | ✓ | [04_light_gemma3_4b.docx](04_light_gemma3_4b.docx) (39KB) |
| 05_light_gemma4_e2b | `gemma4:e2b` | mint_pipeline | 29.8s | 3389/4611 | 0 | ✓ | ✓ | ✓ | [05_light_gemma4_e2b.docx](05_light_gemma4_e2b.docx) (40KB) |
| 06_light_qwen3_5 | `qwen3.5:latest` | mint_pipeline | 50.5s | 3345/4521 | 0 | ✓ | ✓ | ✓ | [06_light_qwen3_5.docx](06_light_qwen3_5.docx) (38KB) |
| 07_baseline_qwen3_5_35b | `qwen3.5:35b` | baseline_raw | 42.7s | 2457/3861 | 0 | — | — | — | [07_baseline_qwen3_5_35b.md](07_baseline_qwen3_5_35b.md) (9KB) |
| 08_baseline_gemma3_4b | `gemma3:4b` | baseline_raw | 14.0s | 2493/1803 | 0 | — | — | — | [08_baseline_gemma3_4b.md](08_baseline_gemma3_4b.md) (7KB) |

## Mint pipeline cells

Each cell below ran the same prompt through the same pipeline; the only delta is the model.

### `gemma4:31b`
- duration: 259.3s
- tokens (in/out): 3389 / 9319
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `01_heavy_gemma4_31b.docx` (38 KB)
- lenient validation: passed=True, hard=0

### `glm-4.7-flash:latest`
- duration: 83.8s
- tokens (in/out): 3188 / 7276
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `02_heavy_glm_4_7_flash.docx` (39 KB)
- lenient validation: passed=True, hard=0

### `qwen3.5:35b`
- **error**: `timeout after 600s`
- duration: 600.2s
- tokens (in/out): 0 / 0
- retries: 0
- json parsed: no
- schema valid: no

### `gemma3:4b`
- duration: 11.3s
- tokens (in/out): 3387 / 3340
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `04_light_gemma3_4b.docx` (39 KB)
- lenient validation: passed=True, hard=0

### `gemma4:e2b`
- duration: 29.8s
- tokens (in/out): 3389 / 4611
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `05_light_gemma4_e2b.docx` (40 KB)
- lenient validation: passed=True, hard=0

### `qwen3.5:latest`
- duration: 50.5s
- tokens (in/out): 3345 / 4521
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `06_light_qwen3_5.docx` (38 KB)
- lenient validation: passed=True, hard=0

## Baseline cells (no MINT pipeline)

Same prompt-class but no schema, no builder. Output is whatever the model returned.

### `qwen3.5:35b` (baseline)
- duration: 42.7s
- tokens (in/out): 2457 / 3861
- raw output: `07_baseline_qwen3_5_35b.md` (9 KB)
- preview: `# MINT: Model-Independent Normalization Toolkit ## Ensuring Consistent Document Quality Across Any LLM  ### Prologue: The Broken Document  It started with a sim…`

### `gemma3:4b` (baseline)
- duration: 14.0s
- tokens (in/out): 2493 / 1803
- raw output: `08_baseline_gemma3_4b.md` (7 KB)
- preview: ````word # MINT: Model-Independent Normalization Toolkit  ## Article Draft (WIP — for alpha/beta release)  **Working Title:** “MINT: How We Taught Any LLM to Gen…`
