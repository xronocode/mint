# FILE: tests/_helpers/fake_vlm.py
# START_MODULE_CONTRACT
#   PURPOSE: Deterministic VLM response generator for V-MP-VISUAL-QA-HOOK +
#     VF-019 tests. Replaces urllib.request.urlopen calls to the Ollama
#     qwen2.5vl:7b endpoint with canned responses across 4 variants.
#     Controller-owned helper per docs/verification-plan.xml
#     SwarmExecutionReadiness/target-15/controller-pre-flight (Phase-15).
#   SCOPE: Pure test helper. No production code consumes this module. Not
#     a pytest fixture — consumed by tests via monkeypatch.setattr to
#     replace the real urlopen call. The companion fixture
#     `backend_probe_patcher` (tests/unit/conftest.py) handles availability;
#     this module supplies the response BODY.
#   DEPENDS: stdlib only (json, io, contextlib).
#   LINKS: docs/verification-plan.xml#VF-019, docs/verification-plan.xml#V-MP-VISUAL-QA-HOOK
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   VlmVariant - Literal["above_threshold", "below_threshold", "malformed_json", "http_error"]
#   FAKE_VLM_RESPONSES - dict[VlmVariant, dict] - canned axis scores per variant
#   fake_vlm_urlopen - callable factory: variant -> drop-in urlopen replacement
#   FakeVlmRecorder - records request bodies for VF-019 inv-7 introspection
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: Phase-15 pre-Wave-15-2: initial provisioning per
#     docs/verification-plan.xml SwarmExecutionReadiness/target-15/controller-pre-flight
#     item-(a). Four variants cover VF-019 inv-1 (advisory-only across all
#     outcomes) + inv-7 (NO-CALLER-INPUT-IN-VLM-PAYLOAD via FakeVlmRecorder).
# END_CHANGE_SUMMARY
"""Deterministic VLM mock for VF-019 / V-MP-VISUAL-QA-HOOK tests.

Usage:
    from tests._helpers.fake_vlm import fake_vlm_urlopen, FakeVlmRecorder

    def test_below_threshold(monkeypatch):
        recorder = FakeVlmRecorder()
        monkeypatch.setattr(
            "urllib.request.urlopen",
            fake_vlm_urlopen("below_threshold", recorder=recorder),
        )
        # ... exercise create_document; hook will receive below-threshold scores ...
        # then introspect what was sent to the VLM:
        for body in recorder.captured_bodies:
            assert b"MINT_TEST_INTENT_SENTINEL" not in body  # VF-019 inv-7
"""
from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Literal

VlmVariant = Literal["above_threshold", "below_threshold", "malformed_json", "http_error"]

# Canned axis scores. Aggregate (mean of axes) drives the threshold logic
# in MP-VISUAL-QA-HOOK. The default threshold is 70 per VF-019 expected-outcome.
FAKE_VLM_RESPONSES: dict[str, dict[str, int]] = {
    "above_threshold": {
        "typography": 85,
        "layout": 80,
        "visual_richness": 78,
        "professional": 88,
    },
    "below_threshold": {
        "typography": 55,
        "layout": 60,
        "visual_richness": 50,
        "professional": 58,
    },
}


class FakeVlmRecorder:
    """Captures request bodies sent to the fake VLM endpoint.

    Used by VF-019 inv-7 NO-CALLER-INPUT-IN-VLM-PAYLOAD assertions to confirm
    no caller-supplied data (intent / source_md / fields_elicited) is interpolated
    into the prompt sent to the VLM.
    """

    def __init__(self) -> None:
        self.captured_bodies: list[bytes] = []
        self.captured_urls: list[str] = []

    def record(self, *, url: str, body: bytes) -> None:
        self.captured_bodies.append(body)
        self.captured_urls.append(url)


def _make_chat_response(axes: dict[str, int]) -> bytes:
    """Build a fake Ollama /api/chat response body."""
    return json.dumps(
        {
            "model": "qwen2.5vl:7b",
            "message": {
                "role": "assistant",
                "content": json.dumps(axes),
            },
            "done": True,
        }
    ).encode("utf-8")


class _FakeUrlopenContext:
    """Drop-in replacement for the context-manager returned by urllib.request.urlopen."""

    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self) -> _FakeUrlopenContext:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status


def fake_vlm_urlopen(
    variant: VlmVariant,
    *,
    recorder: FakeVlmRecorder | None = None,
) -> Callable[..., _FakeUrlopenContext]:
    """Return a callable that mimics urllib.request.urlopen for the chosen variant.

    above_threshold / below_threshold → 200 OK with axis scores.
    malformed_json                    → 200 OK with un-parseable JSON in content.
    http_error                        → raises urllib.error.HTTPError(503).
    """
    import urllib.error

    def _fake(request: object, *args: object, **kwargs: object) -> _FakeUrlopenContext:
        # Capture URL + body if recorder provided; supports both string URLs and
        # urllib.request.Request objects (the latter is what the real client uses).
        if recorder is not None:
            url = getattr(request, "full_url", request) if not isinstance(request, str) else request
            body = getattr(request, "data", b"") or b""
            recorder.record(url=str(url), body=body)

        if variant == "above_threshold":
            return _FakeUrlopenContext(_make_chat_response(FAKE_VLM_RESPONSES["above_threshold"]))
        if variant == "below_threshold":
            return _FakeUrlopenContext(_make_chat_response(FAKE_VLM_RESPONSES["below_threshold"]))
        if variant == "malformed_json":
            malformed = {
                "model": "qwen2.5vl:7b",
                "message": {"content": "not-json{"},
                "done": True,
            }
            return _FakeUrlopenContext(json.dumps(malformed).encode("utf-8"))
        if variant == "http_error":
            raise urllib.error.HTTPError(
                url="http://fake/api/chat",
                code=503,
                msg="Service Unavailable",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )
        raise ValueError(f"Unknown VlmVariant: {variant!r}")

    return _fake
