# MINT Article Experiment — Results

Total wall time: **1390.6s** across 8 cells.

## Per-cell summary

| Cell | Model | Mode | Time | Tok in/out | Retry | JSON | Schema | Docx lenient | Output |
|---|---|---|---:|---|---:|---|---|---|---|
| gemma4_31b | `gemma4:31b` | mint_pipeline | 302.2s | 3389/9582 | 0 | ✓ | ✗ | — | — |
| glm-4.7-flash_latest | `glm-4.7-flash:latest` | mint_pipeline | 600.2s | 0/0 | 0 | ✗ | ✗ | — | — |
| qwen3.5_35b | `qwen3.5:35b` | mint_pipeline | 257.6s | 6716/26550 | 1 | ✗ | ✗ | — | — |
| 04_light_gemma3_4b | `gemma3:4b` | mint_pipeline | 26.6s | 3387/3340 | 0 | ✓ | ✓ | ✓ | [04_light_gemma3_4b.docx](04_light_gemma3_4b.docx) (39KB) |
| 05_light_gemma4_e2b | `gemma4:e2b` | mint_pipeline | 54.4s | 6820/8969 | 0 | ✓ | ✓ | ✓ | [05_light_gemma4_e2b.docx](05_light_gemma4_e2b.docx) (40KB) |
| 06_light_qwen3_5 | `qwen3.5:latest` | mint_pipeline | 59.9s | 3345/5304 | 0 | ✓ | ✓ | ✓ | [06_light_qwen3_5.docx](06_light_qwen3_5.docx) (38KB) |
| 07_baseline_qwen3_5_35b | `qwen3.5:35b` | baseline_raw | 44.2s | 2457/4111 | 0 | — | — | — | [07_baseline_qwen3_5_35b.md](07_baseline_qwen3_5_35b.md) (9KB) |
| 08_baseline_gemma3_4b | `gemma3:4b` | baseline_raw | 13.9s | 2493/1791 | 0 | — | — | — | [08_baseline_gemma3_4b.md](08_baseline_gemma3_4b.md) (7KB) |

## Mint pipeline cells

Each cell below ran the same prompt through the same pipeline; the only delta is the model.

### `gemma4:31b`
- **error**: `schema validation failed: spec.title is required and must be a non-empty string`
- duration: 302.2s
- tokens (in/out): 3389 / 9582
- retries: 0
- json parsed: yes
- schema valid: no
  - spec.title is required and must be a non-empty string

### `glm-4.7-flash:latest`
- **error**: `timeout after 600s`
- duration: 600.2s
- tokens (in/out): 0 / 0
- retries: 0
- json parsed: no
- schema valid: no

### `qwen3.5:35b`
- **error**: `no JSON object found in response`
- duration: 257.6s
- tokens (in/out): 6716 / 26550
- retries: 1
- json parsed: no
- schema valid: no

### `gemma3:4b`
- duration: 26.6s
- tokens (in/out): 3387 / 3340
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `04_light_gemma3_4b.docx` (39 KB)
- lenient validation: passed=True, hard=0

### `gemma4:e2b`
- **error**: `JSON parse failed: Invalid control character at: line 358 column 68 (char 13432)`
- duration: 54.4s
- tokens (in/out): 6820 / 8969
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `05_light_gemma4_e2b.docx` (40 KB)
- lenient validation: passed=True, hard=0

### `qwen3.5:latest`
- duration: 59.9s
- tokens (in/out): 3345 / 5304
- retries: 0
- json parsed: yes
- schema valid: yes
- docx: `06_light_qwen3_5.docx` (38 KB)
- lenient validation: passed=True, hard=0

## Baseline cells (no MINT pipeline)

Same prompt-class but no schema, no builder. Output is whatever the model returned.

### `qwen3.5:35b` (baseline)
- duration: 44.2s
- tokens (in/out): 2457 / 4111
- raw output: `07_baseline_qwen3_5_35b.md` (9 KB)
- preview: `# MINT: Model-Independent Normalization Toolkit ## Ensuring Consistent Document Quality Across Any LLM  ### Introduction  Integrating Large Language Models (LLM…`

### `gemma3:4b` (baseline)
- duration: 13.9s
- tokens (in/out): 2493 / 1791
- raw output: `08_baseline_gemma3_4b.md` (7 KB)
- preview: ````word # MINT: Model-Independent Normalization Toolkit  ## Article Draft (WIP — for alpha/beta release)  **Working Title:** “MINT: How We Taught Any LLM to Gen…`
