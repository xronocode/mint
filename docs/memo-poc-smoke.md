# MP-MEMO-POC — Manual Smoke Procedure (Claude Desktop)

V-MP-MEMO-POC scenario-9 lives here. Pytest cannot reach a real MCP client,
so the elicitation round-trip with a human user is verified manually. This
document is the procedure; failure modes are tracked as issues, not test
failures.

## Prerequisites

- Claude Desktop installed (https://claude.ai/download)
- This repo cloned, `uv sync` run, the venv activated
- `templates/memo.yaml` present (verified in module-level checks)
- A working MP-DOCUMENT save path (verified in module-level checks)

## Configure Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "mint-memo": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/myevdokimov/prj/mint",
        "run",
        "python",
        "-c",
        "from mint_python.mcp.memo import server; server.run(transport='stdio')"
      ]
    }
  }
}
```

Restart Claude Desktop. Open a fresh conversation. Look for a small wrench
or settings icon indicating MCP servers — confirm "mint-memo" appears in
the list.

## Smoke procedure

### Round 1 — full intent (no elicitation expected)

In a Claude Desktop chat:

> Use the create_memo tool with this intent: "Memo from Mikhail Yevdokimov
> (CFO) to Board of Directors on 2026-05-15 about Q2 revenue trends. Q2
> revenue grew 13%, services led the lift, margin ahead of plan."

Expected:
- Claude invokes `create_memo` with the intent string and `source_md=None`.
- No elicitation prompts shown to the user.
- Tool returns `{path: "/tmp/mint_memo_*/memo.docx", audit_id: "...", fields_elicited: []}`.
- Claude's reply mentions the saved path and shows the audit_id.

### Round 2 — partial intent (one elicitation expected)

In a fresh chat:

> Use create_memo to draft a memo from Mikhail Yevdokimov on 2026-05-15
> about Q2 revenue trends; revenue grew 13%.

Expected:
- Claude invokes `create_memo` with the intent.
- Claude Desktop renders **one** structured form — labelled "recipient" —
  asking "Who is the memo addressed to?".
- User enters "Board of Directors" and submits.
- Tool resumes, returns `{path, audit_id, fields_elicited: ["recipient"]}`.

### Round 3 — multiple elicitation rounds

> Use create_memo with intent "Memo about Q2 revenue trends. Recipient:
> Board of Directors."

Expected:
- The heuristic pulls only `recipient` and `subject`.
- Three forms appear in declaration order: **sender**, **date**, **body**.
- After the user fills all three, the tool returns successfully.

### Round 4 — declination path

Repeat round 2, but on the recipient form click **Decline / Cancel**
instead of providing a value.

Expected:
- Tool raises `MEMO_ELICITATION_REJECTED`.
- Claude Desktop reports the error to the user; no docx is produced.

## Verifying the output

For any successful round above:

```bash
# Open the saved file in Word (or LibreOffice) and visually confirm:
#  • Heading 1 "MEMORANDUM" rendered in klawd primary blue (#1B3A5C, Arial 16pt)
#  • The From/To/Date/Subject card rendered as a real table (not paragraphs)
#  • The body section rendered with klawd body color (#333333, Arial 11pt)
#  • No raw markdown markup (** ` etc) appears in the rendered text
```

GRACE manifest verification:

```bash
unzip -p /tmp/mint_memo_*/memo.docx 'grace/manifest_*.xml' | head -50
# Expected: XML with audit_id, generated_by=MP-MEMO-POC, fields_elicited,
# template=memo.yaml, preset=klawd
```

## Failure-mode tracking

If a step fails:
1. Capture the Claude Desktop log (stderr in the developer panel).
2. Open a follow-up issue against the MEMO-POC module — DO NOT mark
   V-MP-MEMO-POC scenario-9 as a CI failure (it's out of CI scope).
3. Use the V-MP-MEMO-POC `failure-packet` shape for the issue body:
   - `failed_scenario`: which round above
   - `trace_break_at`: from the Claude Desktop log
   - `elicited_calls_observed` vs `elicited_calls_expected`
   - `suggested-action`: usually "check FastMCP / Claude Desktop version
     compatibility against fastmcp.server.elicitation primitives"
