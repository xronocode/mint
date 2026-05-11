# FILE: src/mint_python/mcp/telemetry.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Local persistent telemetry for MINT MCP server. Records one
#     JSONL event per tool call to <repo-root>/.mint-telemetry/. Provides
#     aggregate snapshot (totals, by-tool breakdown, latency histograms
#     with p50/p95/p99) via the mint_telemetry tool, plus a mint_version
#     tool returning the installed package version and runtime metadata.
#     Modelled after vision-sidecar-mcp telemetry but simplified for MINT:
#     single write location (no image_path/project_root dual-write),
#     MINT-specific status field, no ollama-specific metrics.
#   SCOPE: Public surface = mint_telemetry (FastMCP tool),
#     mint_version (FastMCP tool), track() decorator, track_call()
#     context manager, event_field() helper, compute_snapshot(),
#     is_enabled().
#   DEPENDS: stdlib only (json, datetime, pathlib, time, contextvars),
#     importlib.metadata (stdlib), fastmcp (Context — type annotation only).
#   LINKS: docs/development-plan.xml#MP-TELEMETRY,
#     docs/knowledge-graph.xml#MP-TELEMETRY
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   mint_telemetry              - @server.tool async fn; returns aggregate
#                                 snapshot or recent raw events
#   mint_version                - @server.tool async fn; returns version +
#                                 runtime metadata
#   track                       - decorator wrapping MCP tool functions with
#                                 track_call(); auto-extracts doc_type kwarg
#   track_call                  - context manager; times block, writes JSONL
#   event_field                 - update in-flight event from nested helpers
#   compute_snapshot            - read JSONL files, produce aggregate dict
#   is_enabled                  - check telemetry enabled via env var
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 — initial implementation. JSONL event recording,
#     aggregate snapshot with histograms, mint_telemetry + mint_version
#     tools. Architecture modelled after vision-sidecar-mcp/telemetry.py.
# END_CHANGE_SUMMARY

from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

_MINT_TELEMETRY = os.environ.get("MINT_TELEMETRY", "1") == "1"
_DIR_OVERRIDE = os.environ.get("MINT_TELEMETRY_DIR")
_DIR_NAME = ".mint-telemetry"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_current_event: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mint_current_event", default=None
)


def is_enabled() -> bool:
    return _MINT_TELEMETRY


def telemetry_dir() -> Path:
    if _DIR_OVERRIDE:
        return Path(_DIR_OVERRIDE)
    return _REPO_ROOT / _DIR_NAME


def event_field(**fields: Any) -> None:
    if not _MINT_TELEMETRY:
        return
    ev = _current_event.get()
    if ev is not None:
        ev.update(fields)


@contextmanager
def track_call(tool: str, **initial: Any) -> Iterator[dict[str, Any]]:
    if not _MINT_TELEMETRY:
        yield {}
        return

    ev: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "tool": tool,
        **{k: v for k, v in initial.items() if v is not None},
    }
    t0 = time.perf_counter()
    token = _current_event.set(ev)
    try:
        yield ev
    except Exception as e:
        ev.setdefault("error_type", type(e).__name__)
        ev.setdefault("error", str(e)[:200])
        raise
    finally:
        ev["total_wall_s"] = round(time.perf_counter() - t0, 3)
        _current_event.reset(token)
        with contextlib.suppress(Exception):
            _write_event(ev)


F = TypeVar("F", bound=Callable[..., Any])


def track(tool_name: str | None = None) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        name = tool_name or fn.__name__

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            doc_type = kwargs.get("doc_type") or kwargs.get("preset") or kwargs.get("name")
            initial: dict[str, Any] = {}
            if doc_type:
                initial["doc_type"] = doc_type
            with track_call(name, **initial):
                return await fn(*args, **kwargs)

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            doc_type = kwargs.get("doc_type") or kwargs.get("preset") or kwargs.get("name")
            initial: dict[str, Any] = {}
            if doc_type:
                initial["doc_type"] = doc_type
            with track_call(name, **initial):
                return fn(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


def _events_filename(now: datetime | None = None) -> str:
    d = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    return f"events-{d}.jsonl"


def _write_event(ev: dict[str, Any]) -> None:
    line = json.dumps(ev, ensure_ascii=False) + "\n"
    fname = _events_filename()
    d = telemetry_dir()
    d.mkdir(parents=True, exist_ok=True)
    with (d / fname).open("a", encoding="utf-8") as f:
        f.write(line)


def _iter_events(directory: Path, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
    if not directory.exists():
        return
    files = sorted(directory.glob("events-*.jsonl"))
    if limit is None:
        for fp in files:
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        return
    collected: list[dict[str, Any]] = []
    for fp in reversed(files):
        with fp.open("r", encoding="utf-8") as f:
            day_events = []
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    day_events.append(json.loads(line))
                except Exception:
                    continue
        for ev in reversed(day_events):
            collected.append(ev)
            if len(collected) >= limit:
                break
        if len(collected) >= limit:
            break
    for ev in reversed(collected):
        yield ev


def _pct(sorted_vals: list[float], p: float) -> float:
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = round((p / 100.0) * (n - 1))
    return sorted_vals[max(0, min(n - 1, idx))]


def _hist(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    s = sorted(values)
    return {
        "count": len(s),
        "min": round(s[0], 4),
        "max": round(s[-1], 4),
        "sum": round(sum(s), 4),
        "p50": round(_pct(s, 50), 4),
        "p95": round(_pct(s, 95), 4),
        "p99": round(_pct(s, 99), 4),
    }


def compute_snapshot(directory: Path) -> dict[str, Any]:
    totals_by_tool: dict[str, int] = {}
    totals_by_status: dict[str, int] = {}
    error_count = 0
    first_ts: str | None = None
    last_ts: str | None = None
    total_calls = 0
    wall_seconds: list[float] = []
    doc_types: dict[str, int] = {}

    for ev in _iter_events(directory):
        total_calls += 1
        ts = ev.get("ts")
        if ts:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
        tool = ev.get("tool", "?")
        totals_by_tool[tool] = totals_by_tool.get(tool, 0) + 1
        st = "error" if ev.get("error_type") else "ok"
        totals_by_status[st] = totals_by_status.get(st, 0) + 1
        if ev.get("error_type"):
            error_count += 1
        if ev.get("doc_type"):
            dt = ev["doc_type"]
            doc_types[dt] = doc_types.get(dt, 0) + 1
        if isinstance(ev.get("total_wall_s"), (int, float)):
            wall_seconds.append(float(ev["total_wall_s"]))

    now_iso = datetime.now(UTC).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    return {
        "computed_at": now_iso,
        "directory": str(directory),
        "first_event_ts": first_ts,
        "last_event_ts": last_ts,
        "totals": {
            "calls": total_calls,
            "by_tool": dict(sorted(totals_by_tool.items(), key=lambda kv: -kv[1])),
            "by_status": totals_by_status,
            "by_doc_type": dict(sorted(doc_types.items(), key=lambda kv: -kv[1])),
            "errors": error_count,
        },
        "rates": {
            "error_rate": round(error_count / total_calls, 4) if total_calls else 0.0,
        },
        "wall_seconds": _hist(wall_seconds),
    }


def recent_events(directory: Path, limit: int = 100) -> list[dict[str, Any]]:
    return list(_iter_events(directory, limit=limit))


# -- MCP tool registrations ----------------------------------------------------
# Lazy import to avoid circular dependency (document.py ↔ telemetry.py).
# Both tools attach to the shared FastMCP `server` from document.py.

from fastmcp import Context  # noqa: E402

import mint_python.mcp.document as _doc_mod  # noqa: E402

_server = _doc_mod.server


@_server.tool(name="mint_version")
async def mint_version(ctx: Context) -> dict[str, Any]:
    """Return MINT package version and runtime metadata."""
    from importlib.metadata import version as pkg_version

    try:
        ver = pkg_version("mint-python")
    except Exception:
        ver = "unknown"
    return {
        "version": ver,
        "protocol": "stdio",
        "telemetry_enabled": _MINT_TELEMETRY,
        "telemetry_dir": str(telemetry_dir()),
    }


@_server.tool(name="mint_telemetry")
async def mint_telemetry(
    ctx: Context,
    raw: bool = False,
    raw_limit: int = 100,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Return telemetry aggregate snapshot (default) or last N raw events.

    Set raw=True to get individual events instead of the aggregate.
    """
    d = telemetry_dir()
    if raw:
        return recent_events(d, limit=raw_limit)
    return compute_snapshot(d)
