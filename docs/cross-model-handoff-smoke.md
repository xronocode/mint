# Cross-model handoff smoke (V-MP-TEMPLATES-WRITE scenario-5)

Manual procedure to verify the Phase-14 W3 cross-model handoff demo. Not
pytest-runnable — needs two MCP clients on different models and at least
one piece of sensitive content the local model is allowed to see but the
frontier model is not.

## Why this matters

The differentiator over `mcp-pandoc` / Anthropic Skills / flat-tool docx-
MCP servers is **template governance under a real audit trail**. A frontier
model edits a template in low-sensitivity context; a local model later
consumes that template against sensitive client data; the produced docx's
GRACE manifest names the entire chain. No competitor today does this end-
to-end through MCP.

This smoke is the closest thing we have to a wow moment against ChatGPT
Pro / Anthropic Skills. Run it before any external demo.

## Prerequisites

- MINT MCP server installed and discoverable via
  `from mint_python.mcp.memo import server` (Phase-13 setup; see
  `docs/memo-poc-smoke.md`).
- Claude Desktop (frontier role).
- A second MCP client connected to a local model. Options that work as of
  2026-05-10:
  - **OpenWebUI + bank-Ollama** running an `openai-3b` or `llama-3b` (the
    matrix from `project_article_experiment.md`). Tool-call support is
    spotty; falls back to the chat-driven path documented in the smoke.
  - **Cursor** with a local-model provider configured.
- `templates/_audit.jsonl` is gitignored — the chain lives in produced
  docs' GRACE manifests, not in version control.

## Procedure

### Step 1 — frontier role: edit the memo template

In Claude Desktop, ask Claude to read the canonical `memo` template and
propose a v1.1 that adds a "Confidentiality" callout. Claude should call
`mint_get_template("memo")` first, then `mint_update_template("memo", new_yaml,
"Claude-Opus-4.7")`.

Expected: Claude returns

```json
{
  "name": "memo",
  "version": "1.1",
  "predecessor_version": "1.0",
  "audit_id": "<uuid>",
  "written_to": ".../templates/memo_v1.1.yaml"
}
```

Verify on disk:

```bash
ls templates/memo_v1.1.yaml
cat templates/_audit.jsonl
```

`_audit.jsonl` should have one line; `name=memo`, `version=1.1`,
`author=Claude-Opus-4.7`, `predecessor_version=1.0`, `content_sha256=...`.

### Step 2 — local role: produce a memo with sensitive content

Switch to the local-model client. Do **NOT** carry the chat history from
step 1 over — the handoff runs through the filesystem (templates/ + the
audit log), not through model context.

Ask the local model to produce a memo with realistic sensitive content
(e.g. salary figures, client names, internal financials). The local model
should call `mint_create_document(intent, doc_type="memo", ...)` — which now
serves the v1.1 template authored in step 1.

The "Confidentiality" callout introduced by Claude appears in the
produced docx because `_load_template` resolves to the highest semver.

### Step 3 — verify the chain in the GRACE manifest

Open the produced docx with python-docx or unzip directly:

```bash
unzip -p ~/Documents/MINT/memo_<date>_<subject>_<short>.docx 'grace/manifest_*.xml' \
  | grep -E 'template|audit_id'
```

Expected output:

```
audit_id=<uuid for THIS document>
template=memo.yaml
template_version=1.1
template_author=Claude-Opus-4.7
```

The `audit_id` here is the document's, not the template's. The
template's `audit_id` lives in `_audit.jsonl` and is cross-referenced by
the `template_author` + `template_version` pair. Together: every
sensitive document carries provenance for the template chain that built
it, signed by the model that authored each link.

### Step 4 — anti-checklist

The smoke fails if:

- `_load_template` serves v1.0 instead of v1.1 (resolution didn't honor
  the latest version)
- The local model bypasses the template registry (e.g. inlines its own
  layout) — the GRACE manifest would then NOT carry the template_author
  field, breaking the chain
- `_audit.jsonl` is empty or missing — write path silently no-op'd
- The frontier model's `mint_update_template` returned `version=1.0` (semver
  bump didn't fire)
- `template/memo_v1.1.yaml` was overwritten on a re-run instead of
  surfacing TEMPLATE_VERSION_CONFLICT (forbidden-1 violated)

If any of those happen, file under
`docs/development-plan.xml#MP-TEMPLATES-WRITE` as a regression and stop
demoing the chain until W3 is patched.
