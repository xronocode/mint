# FILE: tests/_helpers/fake_mcp_context.py
"""FakeMCPContext — drop-in replacement for fastmcp.Context in MEMO-POC tests.

Real `fastmcp.Context.elicit(message, response_type)` opens a structured form
in the connected MCP client. In tests we don't have a real client, so we
provide a fake that replays scripted answers and records every elicit call
for verification.

Usage:
    ctx = FakeMCPContext(answers={"recipient": "Board of Directors"})
    result = await create_memo(intent=..., source_md=None, ctx=ctx)
    assert ctx.elicited_calls == [("recipient", <message text>)]

Decline path:
    ctx = FakeMCPContext(answers={"recipient": "__DECLINE__"})

Cancel path:
    ctx = FakeMCPContext(answers={"recipient": "__CANCEL__"})

The fake matches fastmcp's elicit return-type contract: AcceptedElicitation
on accept (with .data), DeclinedElicitation on decline, CancelledElicitation
on cancel. Tests assert against `ctx.elicited_calls` (a list of (field_name,
message) tuples in invocation order) and against the result-shape returned
by the tool.

The field name we record comes from the response_title kwarg if the caller
provides it; otherwise we fall back to the first 40 chars of the message.
The MEMO-POC tool always passes response_title=field_name (sender, recipient,
date, subject, body) so the recorded list is always (field_name, message).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)

_DECLINE_SENTINEL = "__DECLINE__"
_CANCEL_SENTINEL = "__CANCEL__"


@dataclass
class FakeMCPContext:
    """Stand-in for fastmcp.Context with scripted elicit responses.

    Args:
        answers: Mapping of field-name → answer string. Special sentinel
            values "__DECLINE__" and "__CANCEL__" trigger the decline /
            cancel paths instead of returning data.
    """

    answers: dict[str, str] = field(default_factory=dict)
    elicited_calls: list[tuple[str, str]] = field(default_factory=list)

    async def elicit(
        self,
        message: str,
        response_type: Any = None,
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> AcceptedElicitation[Any] | DeclinedElicitation | CancelledElicitation:
        # Record the call. Field name = response_title if present, else
        # message snippet.
        field_name = response_title or message[:40]
        self.elicited_calls.append((field_name, message))

        if field_name not in self.answers:
            raise KeyError(
                f"FakeMCPContext: no scripted answer for field {field_name!r}; "
                f"scripted={list(self.answers.keys())}"
            )
        answer = self.answers[field_name]
        if answer == _DECLINE_SENTINEL:
            return DeclinedElicitation()
        if answer == _CANCEL_SENTINEL:
            return CancelledElicitation()
        return AcceptedElicitation(data=answer)
