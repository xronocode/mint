# MINT Pure Python Style Preset Schema

**Status:** normative for Phase-7 (Pure Python Edition Phase 1) — V-MP-STYLE references this document.
**JSON Schema:** Draft 2020-12.
**Loader:** `mint_python.core.style.load_preset(name | path)`.

Style presets are JSON files (or in-process dicts in the built-in registry) that bundle typography, color palette, and spacing defaults. A preset MUST validate against the schema below; loaders raise `STYLE_PRESET_INVALID_SCHEMA` (with the JSON Pointer of the failing field) on mismatch.

## Top-level shape

```jsonc
{
  "$schema": "https://mint.dev/schema/style-preset-1.0.json",
  "name": "alga_corporate",
  "version": "1.0",
  "description": "Alga Group corporate identity for documents and decks",
  "color_palette": { /* see §1 */ },
  "typography": { /* see §2 */ },
  "spacing": { /* see §3 */ }
}
```

All five top-level keys (`name`, `version`, `color_palette`, `typography`, `spacing`) are **required**. `description` and `$schema` are optional.

## §1 — `color_palette`

```jsonc
{
  "primary":    "#0F4C81",
  "secondary":  "#5B8DBE",
  "accent":     "#FFB400",
  "text":       "#1A1A1A",
  "text_muted": "#6E6E6E",
  "background": "#FFFFFF",
  "border":     "#D4D4D4",
  "success":    "#2E7D32",
  "warning":    "#F9A825",
  "error":      "#C62828"
}
```

- All values are 6-digit hex `#RRGGBB`. No alpha; no 3-digit shorthand.
- The seven keys `primary`, `secondary`, `accent`, `text`, `text_muted`, `background`, `border` are **required**. The three semantic keys `success`, `warning`, `error` are optional but recommended.
- `ColorPalette(name).resolve(key)` returns the hex string. `KeyError` on unknown key with the palette name and the missing key in the message.

## §2 — `typography`

A dict whose required keys are the named styles MP-CONTENT and MP-TABLE consume:

```jsonc
{
  "heading1":     { /* StyleSpec */ },
  "heading2":     { /* StyleSpec */ },
  "heading3":     { /* StyleSpec */ },
  "body":         { /* StyleSpec */ },
  "table_header": { /* StyleSpec */ },
  "caption":      { /* StyleSpec */ }
}
```

The six keys above are **required**. Additional keys (`subtitle`, `code`, `quote`, ...) are allowed but Phase-7 tests only assert on the required six.

### `StyleSpec` shape

```jsonc
{
  "font":              "Inter",          // string, required
  "size_pt":           24,               // number > 0, required (points)
  "color":             "#0F4C81",        // hex OR palette token "@primary"
  "bold":              true,             // boolean, default false
  "italic":            false,            // boolean, default false
  "alignment":         "left",           // "left" | "center" | "right" | "justify", default "left"
  "spacing_before_pt": 12,               // number ≥ 0, default 0
  "spacing_after_pt":  6,                // number ≥ 0, default 0
  "line_height":       1.4,              // number > 0, multiplier of size_pt; default 1.15
  "keep_with_next":    true              // boolean, default false (true on heading*)
}
```

- `color` accepts EITHER a literal `#RRGGBB` hex OR a palette token of the form `@<key>` (e.g. `@primary`). Tokens resolve through the preset's own `color_palette`.
- `font` is a string; the loader does NOT verify font availability on the system (deferred to Phase-3 fix engine).
- `font`, `size_pt`, `color` are required. All other StyleSpec fields have defaults listed above.

## §3 — `spacing`

Document-wide defaults applied when a StyleSpec does not override:

```jsonc
{
  "paragraph_default_before_pt": 0,
  "paragraph_default_after_pt":  6,
  "default_line_height":         1.15,
  "table_cell_padding_pt":       4
}
```

All four keys required. Numbers ≥ 0; `default_line_height` > 0.

## Built-in registry (Phase-7)

`mint_python.sdk.presets` ships three names:

| Name              | Personality |
|-------------------|-------------|
| `alga_corporate`  | Corporate identity per handover §3 examples (Inter font, Alga blue palette) |
| `minimal`         | Plain text-first preset, near-neutral palette, Inter only |
| `compact`         | Tight spacing for one-page memos (smaller fonts, reduced spacing_after) |

All three preset JSONs ship under `src/mint_python/core/presets/*.json` and are validated against this schema at module import time.

## Versioning

The `version` field is a SemVer-style string. The loader matches major version: a Phase-7 loader reads `1.x` presets; future schema changes that break compatibility bump to `2.0` and require an explicit migration step.

## Validation in code

```python
from mint_python.core.style import load_preset
ns = load_preset("alga_corporate")  # registry lookup
ns = load_preset(path=Path("custom.json"))  # external file
# Both return: SimpleNamespace(heading1=..., heading2=..., body=..., table_header=..., caption=...)
# heading1 is a frozen Style instance.
```

On schema violation: raises `STYLE_PRESET_INVALID_SCHEMA` whose message contains both the JSON Pointer of the failing field and the constraint that failed (e.g. `"/typography/heading1/color: expected hex #RRGGBB or @palette-token, got 'rgb(15,76,129)'"`).
