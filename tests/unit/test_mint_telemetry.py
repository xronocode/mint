# FILE: tests/unit/test_mint_telemetry.py
# VERSION: 1.0.0
# START_MODULE_CONTRACT
#   PURPOSE: Achieve 100% code coverage for mint_python.mcp.telemetry
#   SCOPE: All public + private functions in telemetry.py
#   DEPENDS: pytest, mint_python.mcp.telemetry
#   LINKS: docs/knowledge-graph.xml#MP-TELEMETRY
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   test_is_enabled - env var toggle
#   test_telemetry_dir - default + override paths
#   test_event_field - contextvar update / no-op
#   test_track_call - context manager normal / exception / disabled
#   test_track - decorator async / sync / doc_type extraction
#   test_compute_snapshot - empty / populated directory
#   test_recent_events - limit parameter
#   test_mint_version_tool - async tool response
#   test_mint_telemetry_tool - snapshot + raw modes
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import mint_python.mcp.telemetry as _sut


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINT_TELEMETRY", raising=False)
    monkeypatch.delenv("MINT_TELEMETRY_DIR", raising=False)
    importlib.reload(_sut)


class TestIsEnabled:
    def test_default_enabled(self) -> None:
        assert _sut.is_enabled() is True

    def test_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY", "0")
        importlib.reload(_sut)
        assert _sut.is_enabled() is False


class TestTelemetryDir:
    def test_default(self) -> None:
        d = _sut.telemetry_dir()
        assert d.name == ".mint-telemetry"

    def test_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        override = tmp_path / "custom"
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(override))
        importlib.reload(_sut)
        assert _sut.telemetry_dir() == override


class TestEventField:
    def test_updates_active_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY", "1")
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        with _sut.track_call("test_tool") as ev:
            _sut.event_field(extra_key="extra_val")
            assert ev["extra_key"] == "extra_val"

    def test_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY", "0")
        importlib.reload(_sut)
        _sut.event_field(x=1)

    def test_noop_outside_context(self) -> None:
        _sut.event_field(x=1)


class TestTrackCall:
    def test_normal_flow(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        with _sut.track_call("my_tool", status="ok") as ev:
            ev["custom"] = 42
        files = list(tmp_path.glob("events-*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text().strip()
        ev = json.loads(line)
        assert ev["tool"] == "my_tool"
        assert ev["status"] == "ok"
        assert ev["custom"] == 42
        assert "total_wall_s" in ev
        assert "ts" in ev

    def test_exception_flow(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        with pytest.raises(ValueError, match="boom"):
            with _sut.track_call("bad_tool") as ev:
                raise ValueError("boom")
        line = (tmp_path / _sut._events_filename()).read_text().strip()
        ev = json.loads(line)
        assert ev["tool"] == "bad_tool"
        assert ev["error_type"] == "ValueError"
        assert "boom" in ev["error"]

    def test_disabled_yields_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MINT_TELEMETRY", "0")
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        with _sut.track_call("ghost") as ev:
            assert ev == {}
        assert not list(tmp_path.iterdir())

    def test_none_values_filtered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        with _sut.track_call("t", keep="yes", drop=None) as ev:
            pass
        line = (tmp_path / _sut._events_filename()).read_text().strip()
        ev = json.loads(line)
        assert "keep" in ev
        assert "drop" not in ev


class TestTrackDecorator:
    def test_async_function(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)

        @_sut.track("async_tool")
        async def do_async(**kw: Any) -> str:
            return "ok"

        result = asyncio.run(do_async(doc_type="report"))
        assert result == "ok"
        line = (tmp_path / _sut._events_filename()).read_text().strip()
        ev = json.loads(line)
        assert ev["tool"] == "async_tool"
        assert ev["doc_type"] == "report"

    def test_sync_function(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)

        @_sut.track("sync_tool")
        def do_sync(**kw: Any) -> str:
            return "done"

        result = do_sync(preset="blue")
        assert result == "done"
        line = (tmp_path / _sut._events_filename()).read_text().strip()
        ev = json.loads(line)
        assert ev["tool"] == "sync_tool"
        assert ev["doc_type"] == "blue"

    def test_extracts_name_kwarg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)

        @_sut.track()
        async def my_fn(**kw: Any) -> None:
            pass

        asyncio.run(my_fn(name="my_doc"))
        line = (tmp_path / _sut._events_filename()).read_text().strip()
        ev = json.loads(line)
        assert ev["tool"] == "my_fn"
        assert ev["doc_type"] == "my_doc"

    def test_no_relevant_kwargs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)

        @_sut.track()
        def plain(x: int) -> int:
            return x

        result = plain(5)
        assert result == 5
        line = (tmp_path / _sut._events_filename()).read_text().strip()
        ev = json.loads(line)
        assert "doc_type" not in ev

    def test_default_tool_name_from_function(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)

        @_sut.track()
        def special_fn() -> None:
            pass

        special_fn()
        line = (tmp_path / _sut._events_filename()).read_text().strip()
        ev = json.loads(line)
        assert ev["tool"] == "special_fn"


class TestComputeSnapshot:
    def test_empty_directory(self, tmp_path: Path) -> None:
        snap = _sut.compute_snapshot(tmp_path)
        assert snap["totals"]["calls"] == 0
        assert snap["totals"]["errors"] == 0
        assert snap["first_event_ts"] is None
        assert snap["last_event_ts"] is None
        assert snap["wall_seconds"]["count"] == 0

    def test_populated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        events = [
            {"ts": "2026-01-01T00:00:00Z", "tool": "a", "total_wall_s": 0.5, "doc_type": "docx"},
            {"ts": "2026-01-01T00:01:00Z", "tool": "a", "total_wall_s": 1.5, "error_type": "ValueError"},
            {"ts": "2026-01-01T00:02:00Z", "tool": "b", "total_wall_s": 2.0, "doc_type": "pptx"},
            {"ts": "2026-01-01T00:03:00Z", "tool": "b", "doc_type": "docx"},
        ]
        fname = tmp_path / "events-2026-01-01.jsonl"
        with fname.open("w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        snap = _sut.compute_snapshot(tmp_path)
        assert snap["totals"]["calls"] == 4
        assert snap["totals"]["errors"] == 1
        assert snap["totals"]["by_tool"]["a"] == 2
        assert snap["totals"]["by_tool"]["b"] == 2
        assert snap["totals"]["by_status"]["error"] == 1
        assert snap["totals"]["by_status"]["ok"] == 3
        assert snap["totals"]["by_doc_type"]["docx"] == 2
        assert snap["totals"]["by_doc_type"]["pptx"] == 1
        assert snap["wall_seconds"]["count"] == 3
        assert snap["wall_seconds"]["min"] == 0.5
        assert snap["wall_seconds"]["max"] == 2.0
        assert snap["first_event_ts"] == "2026-01-01T00:00:00Z"
        assert snap["last_event_ts"] == "2026-01-01T00:03:00Z"

    def test_error_rate(self, tmp_path: Path) -> None:
        events = [
            {"ts": "2026-01-01T00:00:00Z", "tool": "a", "error_type": "Err"},
        ]
        fname = tmp_path / "events-2026-01-01.jsonl"
        with fname.open("w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        snap = _sut.compute_snapshot(tmp_path)
        assert snap["rates"]["error_rate"] == 1.0


class TestRecentEvents:
    def test_limit(self, tmp_path: Path) -> None:
        events = [{"ts": f"2026-01-01T00:00:0{i}Z", "tool": "t"} for i in range(5)]
        fname = tmp_path / "events-2026-01-01.jsonl"
        with fname.open("w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        result = _sut.recent_events(tmp_path, limit=2)
        assert len(result) == 2
        assert result[-1]["ts"] == "2026-01-01T00:00:04Z"
        assert result[0]["ts"] == "2026-01-01T00:00:03Z"

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        result = _sut.recent_events(tmp_path / "nope", limit=10)
        assert result == []


class TestMintVersionTool:
    @pytest.mark.asyncio
    async def test_returns_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY", "1")
        importlib.reload(_sut)
        ctx = MagicMock()
        result = await _sut.mint_version(ctx)
        assert "version" in result
        assert result["protocol"] == "stdio"
        assert result["telemetry_enabled"] is True
        assert "telemetry_dir" in result


class TestMintTelemetryTool:
    @pytest.mark.asyncio
    async def test_snapshot_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        ctx = MagicMock()
        result = await _sut.mint_telemetry(ctx, raw=False)
        assert isinstance(result, dict)
        assert "totals" in result

    @pytest.mark.asyncio
    async def test_raw_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINT_TELEMETRY_DIR", str(tmp_path))
        importlib.reload(_sut)
        ev = {"ts": "2026-01-01T00:00:00Z", "tool": "x"}
        fname = tmp_path / "events-2026-01-01.jsonl"
        with fname.open("w") as f:
            f.write(json.dumps(ev) + "\n")
        ctx = MagicMock()
        result = await _sut.mint_telemetry(ctx, raw=True, raw_limit=10)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["tool"] == "x"


class TestIterEventsEdgeCases:
    def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        fname = tmp_path / "events-2026-01-01.jsonl"
        fname.write_text("not-json\n")
        result = list(_sut._iter_events(tmp_path))
        assert result == []

    def test_empty_line_skipped(self, tmp_path: Path) -> None:
        fname = tmp_path / "events-2026-01-01.jsonl"
        fname.write_text("\n\n")
        result = list(_sut._iter_events(tmp_path))
        assert result == []

    def test_with_limit_stops_early(self, tmp_path: Path) -> None:
        events = [{"ts": f"2026-01-01T00:0{i}:00Z", "tool": "t"} for i in range(10)]
        fname = tmp_path / "events-2026-01-01.jsonl"
        with fname.open("w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        result = list(_sut._iter_events(tmp_path, limit=3))
        assert len(result) == 3

    def test_limit_with_malformed_and_empty_lines(self, tmp_path: Path) -> None:
        fname = tmp_path / "events-2026-01-01.jsonl"
        lines = [
            "not-json",
            "",
            json.dumps({"ts": "2026-01-01T00:00:00Z", "tool": "a"}),
            "also-bad",
            json.dumps({"ts": "2026-01-01T00:01:00Z", "tool": "b"}),
        ]
        fname.write_text("\n".join(lines) + "\n")
        result = list(_sut._iter_events(tmp_path, limit=5))
        assert len(result) == 2
        assert result[0]["tool"] == "a"
        assert result[1]["tool"] == "b"


class TestHistogramHelpers:
    def test_hist_empty(self) -> None:
        assert _sut._hist([]) == {"count": 0}

    def test_hist_single_value(self) -> None:
        result = _sut._hist([5.0])
        assert result["count"] == 1
        assert result["min"] == 5.0
        assert result["max"] == 5.0
        assert result["p50"] == 5.0

    def test_pct_single(self) -> None:
        assert _sut._pct([42.0], 99) == 42.0

    def test_events_filename_custom(self) -> None:
        dt = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)
        assert _sut._events_filename(dt) == "events-2026-03-15.jsonl"
