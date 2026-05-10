# Per-cell comparison

| Cell | Model | R1 | R2 | R3 (post-fix) |
|---|---|---|---|---|
| 01 | gemma4:31b | FAIL 302s | OK 259s | OK 625s |
| 02 | glm-4.7-flash | FAIL 600s | OK 84s | FAIL 600s |
| 03 | qwen3.5:35b | FAIL 258s | FAIL 600s | FAIL 1104s |
| 04 | gemma3:4b | OK 27s | OK 11s | OK 29s |

## Aggregate

|  | R1 | R2 | R3 |
|---|---|---|---|
| Heavy tier (3) | 0/3 | 2/3 | 1/3 |
| Light tier (3) | 3/3 | 3/3 | 2/3 |

## Visual-fidelity delta

| Cell | R2 (under-bug) Heading 1 | R3 (post-fix) Heading 1 |
|---|---|---|
| 01_heavy_gemma4_31b | color #365F91, theme font, 14pt | **color #1B3A5C, Arial, 16pt** |
| 04_light_gemma3_4b  | color #365F91, theme font, 14pt | **color #1B3A5C, Arial, 16pt** |
| 06_light_qwen3_5    | color #365F91, theme font, 14pt | **color #1B3A5C, Arial, 16pt** |
