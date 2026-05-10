# Cell Matrix — Model Selection & Rationale

The 6 + 2 cells were selected to test two orthogonal axes:

1. **Model size effect** — heavy 30B vs light 4-10B
2. **Pipeline effect** — same model with and without MINT

All models live on the bank Ollama at `10.128.26.10:11434` and were inventoried
on 2026-05-09 (see `project_article_experiment` memory).

## Heavy tier (~30B, family-fair)

| Cell | Model | Params | Family | Why selected |
|---|---|---|---|---|
| 01 | `gemma4:31b` | 31.3B | Gemma4 | Modern Google open-weights, the "fast-finishing reasoning" example from the bank's own e2e_sidecar |
| 02 | `glm-4.7-flash:latest` | 29.9B | GLM-4.7 MoE | Closest open-weights "GPT-class" — `gpt-oss` was previously here but was removed before this experiment |
| 03 | `qwen3.5:35b` | 36.0B | Qwen3.5 MoE | Picked over `qwen3.6:35b` because qwen3.6 is documented to lock into thinking-mode on long prompts |

**Excluded from heavy**: `qwen3.6:35b` (thinking-mode trap); `minimax-m2.7:cloud` (cloud-routed, not local).

## Light tier (4-10B, mixed lineages)

| Cell | Model | Params | Family | Why selected |
|---|---|---|---|---|
| 04 | `gemma3:4b` | 4.3B | Gemma3 | Smallest viable instruction-tuned model on the server — the "can a 4B do this?" test |
| 05 | `gemma4:e2b` | 5.1B | Gemma4 (embedded) | Sister to gemma4:31b but at e-2b tier — tests whether Gemma4's tuning scales down |
| 06 | `qwen3.5:latest` | 9.7B | Qwen3.5 (non-MoE) | The 9.7B Qwen variant — instruction-tuned, no thinking-mode bias |

**Excluded from light**: `hauhau-qwen35:latest` (custom variant, not part of the standard family); `s44t12/coder` (14.8B, on the boundary; specifically tuned for code).

## Baselines (no MINT pipeline)

| Cell | Model | Mode | Why |
|---|---|---|---|
| 07 | `qwen3.5:35b` | naked LLM call, save raw text | Compare against cell 03 — does the model produce ANY useful text without our pipeline? |
| 08 | `gemma3:4b` | naked LLM call, save raw text | Compare against cell 04 — show what the smallest model produces unaided |

The baseline prompt is intentionally generic: "rewrite this draft as a polished
article you could paste into Word." No JSON schema, no block typing, no style
guidance. This is what someone would do if they didn't have MINT.

## Why no GPT-OSS

The earlier `e2e_sidecar_report.py` in this repo references `gpt-oss:latest`,
but as of 2026-05-09 the model is no longer pulled on the bank Ollama. The
closest open-weights GPT-class model still available is `glm-4.7-flash:latest`
(GLM-4.7 MoE, 29.9B), which fills the cell.

If gpt-oss is repulled in a future round, swap `glm-4.7-flash` for
`gpt-oss:latest` in `tools/article_experiment/run.py::MATRIX` and rerun —
the rest of the pipeline is model-agnostic.
