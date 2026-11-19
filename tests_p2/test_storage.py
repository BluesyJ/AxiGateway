"""P2's single planned storage checks; no real credentials or network providers."""

import json
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from statistics import median

import pytest

from axi_gateway.collector import Collector
from axi_gateway.events import Labels, Usage, usage_projection
from axi_gateway.queries import (
    aggregate_events,
    list_events,
    list_gaps,
    read_connection,
    read_counters,
)
from axi_gateway.sinks import DevelopmentJsonlSink
from axi_gateway.storage import SQLiteSink
from tests_p2.conftest import RESULTS
from tests_p2.samples import kwargs, projector, sample

ROOT = Path(__file__).resolve().parents[1]


def eventually(check: object, seconds: float = 3) -> None:
    """Bound synchronization waits; a timeout is a failure, never a passing check."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("bounded condition not met")


def test_schema_idempotency_and_queries(tmp_path: Path) -> None:
    """Migrate/reopen; enforce null/provenance and failure idempotency."""
    path = tmp_path / "events.sqlite3"
    sink = SQLiteSink(path)
    event = sample()
    failure = replace(event, provider_outcome="error", error_category="upstream_http")
    sink.write_batch([failure, failure], {"written": 2}, [])
    assert (
        len(list_events(path)) == 1
        and list_events(path)[0]["provider_outcome"] == "error"
    )
    reported = replace(
        sample(),
        usage=usage_projection(
            {
                "prompt_tokens": 20,
                "completion_tokens": 12,
                "total_tokens": 32,
                "prompt_tokens_details": {"cached_tokens": 8},
                "completion_tokens_details": {"reasoning_tokens": 6},
            },
            "reported",
        ),
        client_label=Labels("one", "two"),
    )
    sink.write_batch(
        [reported, replace(sample(), usage=Usage(3, 4, 7, "unknown"))],
        {"written": 2},
        [],
    )
    assert (
        list_events(path, limit=1, offset=1)[0]["litellm_call_id"]
        == reported.litellm_call_id
    )
    assert (
        len(list_events(path, client="two", model="synthetic", usage_source="reported"))
        == 1
    )
    assert len(list_events(path, start=reported.started_at, end="9999")) == 2
    for group in ("time", "model", "client", "usage_source"):
        rows = aggregate_events(path, group_by=group)
        assert sum(r["event_count"] for r in rows) == 3
        assert {r["usage_source"] for r in rows} == {"reported", "unknown", "missing"}
        assert all(
            r["cost_amount"] is None and r["cost_known_count"] == 0 for r in rows
        )
    row = list_events(path, usage_source="reported")[0]
    assert row["cache_miss_tokens"] == 12 and row["cache_miss_source"] == "derived"
    assert row["reasoning_tokens"] == 6 and row["total_tokens"] == 32
    with pytest.raises(sqlite3.IntegrityError):
        sink.write_batch(
            [replace(sample(), usage=Usage(1, 1, 2, "unknown", 0))], {}, []
        )
    with read_connection(path) as reader:
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert reader.execute("PRAGMA user_version").fetchone()[0] == 2
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("DELETE FROM events")
    sink.close()
    reopened = SQLiteSink(path)
    reopened.write_batch([failure], {"written": 1}, [])
    assert len(list_events(path)) == 3 and read_counters(path)["written"] == 5
    reopened.close()
    RESULTS["schema_idempotency_queries"] = {
        "passed": True,
        "schema_version": 2,
        "failure_rows": 1,
        "readonly_enforced": True,
    }


def test_metadata_privacy_and_rejection(tmp_path: Path, caplog: object) -> None:
    """Scan database, sidecars, debug events and logs for synthetic sentinels."""
    secret, prompt, answer, reasoning = (
        "sk-P2-private-key",
        "P2-private-prompt",
        "P2-private-answer",
        "P2-private-reasoning",
    )
    from axi_gateway.events import Projector

    for mode in ("unset", "false"):
        path = tmp_path / (mode + ".sqlite3")
        debug = DevelopmentJsonlSink(tmp_path / (mode + ".jsonl"))
        collector = Collector(
            Projector("synthetic", set(), "unpriced", (secret,)),
            SQLiteSink(path),
            debug_sink=debug,
        )
        for case in ("success", "error", "stream", "disconnect"):
            data = kwargs()
            data.update(
                messages=[{"content": prompt}],
                stream=case in ("stream", "disconnect"),
                litellm_params={
                    "proxy_server_request": {
                        "headers": {"authorization": secret},
                        "body": {"content": prompt},
                    }
                },
            )
            if case == "disconnect":
                data["standard_logging_object"] = {
                    "error_information": {"error_code": 499}
                }
            collector.submit(
                data,
                {
                    "choices": [
                        {"message": {"content": answer, "reasoning_content": reasoning}}
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                },
                None,
                None,
                case != "error",
            )
        collector.submit(
            {"litellm_no_upstream_llm_call": True}, None, None, None, False
        )
        eventually(lambda collector=collector: collector.metrics()["written"] == 4)
        for file in tmp_path.iterdir():
            raw = file.read_bytes()
            assert not any(
                s.encode(encoding) in raw
                for s in (secret, prompt, answer, reasoning)
                for encoding in ("utf8", "utf-16-le")
            )
        assert not any(s in caplog.text for s in (secret, prompt, answer, reasoning))
        collector.close()
        assert len(list_events(path)) == 4 and read_counters(path)["auth_rejected"] == 1
        assert len(debug.path.read_text().splitlines()) == 4
        assert not list_gaps(path)
    RESULTS["storage_privacy"] = {
        "passed": True,
        "scenarios": 8,
        "database_sidecars_logs_debug_scanned": True,
        "pre_forward_rejection_events": 0,
    }


def test_queue_full_recovery(tmp_path: Path) -> None:
    """A blocked writer fills the bounded queue; recovery persists loss."""
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / "events.sqlite3"

    class Paused(SQLiteSink):
        def write_batch(self, events: list, counters: dict, gaps: list) -> None:
            if events and not entered.is_set():
                entered.set()
                assert release.wait(3)
            super().write_batch(events, counters, gaps)

    collector = Collector(projector(), Paused(path), capacity=1)
    collector.submit(kwargs(), None, None, None, True)
    assert entered.wait(1)
    begin = time.perf_counter()
    for _ in range(5):
        collector.submit(kwargs(), None, None, None, True)
    elapsed = time.perf_counter() - begin
    assert elapsed < 0.2 and collector.metrics()["queue_full"] == 4
    release.set()
    eventually(lambda: collector.metrics()["written"] == 2)
    collector.close()
    gap = list_gaps(path)[0]
    assert (
        gap["reason"] == "queue_full"
        and gap["count"] == 4
        and gap["first_at"] <= gap["last_at"]
    )
    assert read_counters(path)["queue_full"] == 4
    RESULTS["queue_full"] = {
        "passed": True,
        "lost": 4,
        "gap_count": 4,
        "submit_batch_ms": round(elapsed * 1000, 3),
    }


@pytest.mark.parametrize("mode", ["write_failed", "readonly", "locked"])
def test_failed_storage_recovers(tmp_path: Path, mode: str) -> None:
    """Contain real lock/read-only errors and a sink fault; record gaps."""
    path = tmp_path / "events.sqlite3"
    setup = SQLiteSink(path)
    setup.write_batch([], {}, [])
    setup.close()
    active = threading.Event()
    active.set()

    class Fault(SQLiteSink):
        def write_batch(self, events: list, counters: dict, gaps: list) -> None:
            if mode == "write_failed" and active.is_set():
                raise OSError("synthetic failure")
            if mode == "readonly":
                self._open().execute(
                    "PRAGMA query_only=" + ("ON" if active.is_set() else "OFF")
                )
            super().write_batch(events, counters, gaps)

    blocker = sqlite3.connect(path, isolation_level=None)
    if mode == "locked":
        blocker.execute("BEGIN IMMEDIATE")
    collector = Collector(projector(), Fault(path))
    begin = time.perf_counter()
    collector.submit(kwargs(), None, None, None, True)
    elapsed = time.perf_counter() - begin
    assert elapsed < 0.1
    eventually(lambda: collector.metrics()["sink_failed"] == 1)
    active.clear()
    if mode == "locked":
        blocker.rollback()
    blocker.close()
    collector.submit(kwargs(), None, None, None, True)
    eventually(lambda: collector.metrics()["written"] == 1)
    collector.close()
    assert len(list_events(path)) == 1
    assert list_gaps(path)[0]["count"] == 1
    assert read_counters(path)["sink_failed"] == 1
    RESULTS[mode] = {
        "passed": True,
        "lost": 1,
        "gap_count": 1,
        "submit_ms": round(elapsed * 1000, 3),
    }


def test_readers_and_bounded_drain(tmp_path: Path) -> None:
    """A live read transaction must not block WAL commits; exit waiting is bounded."""
    path = tmp_path / "events.sqlite3"
    collector = Collector(projector(), SQLiteSink(path))
    collector.submit(kwargs(), None, None, None, True)
    eventually(lambda: collector.metrics()["written"] == 1)
    with read_connection(path) as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        for _ in range(100):
            collector.submit(kwargs(), None, None, None, True)
        eventually(lambda: collector.metrics()["written"] == 101)
        assert reader.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert len(list_events(path, limit=1000)) == 101
        assert sum(r["event_count"] for r in aggregate_events(path)) == 101
    begin = time.perf_counter()
    collector.close(0.5)
    elapsed = time.perf_counter() - begin
    assert elapsed < 0.6
    assert read_counters(path)["written"] == 101
    RESULTS["concurrent_read_and_drain"] = {
        "passed": True,
        "commits_during_reader": 100,
        "drain_ms": round(elapsed * 1000, 3),
    }


def test_hard_kill_integrity(tmp_path: Path) -> None:
    """Kill only our child while it holds an uncommitted transaction."""
    path = tmp_path / "events.sqlite3"
    process = subprocess.Popen(
        [sys._base_executable, str(ROOT / "tests_p2/kill_writer.py"), str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert process.stdout.readline().strip() == b"transaction_pending"
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
    with read_connection(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    rows = list_events(path)
    assert len(rows) == 1 and rows[0]["model_alias"] == "synthetic"
    assert read_counters(path) == {"written": 1}
    RESULTS["hard_kill"] = {
        "passed": True,
        "integrity_ok": True,
        "committed_rows_retained": 1,
        "uncommitted_rolled_back": True,
        "memory_gaps_guaranteed": False,
    }


def test_small_measurement(tmp_path: Path) -> None:
    """One sample measures callback, commit latency and throughput; no SLA."""
    path = tmp_path / "events.sqlite3"
    commit_latencies, submitted = [], {}

    class Measured(SQLiteSink):
        def write_batch(self, events: list, counters: dict, gaps: list) -> None:
            super().write_batch(events, counters, gaps)
            now = time.perf_counter_ns()
            commit_latencies.extend(
                (now - submitted[event.litellm_call_id]) / 1e6 for event in events
            )

    collector = Collector(projector(), Measured(path))
    eventually(lambda: path.exists() and collector.sink.connection is not None)
    inputs = [kwargs() for _ in range(512)]
    baseline, callbacks = [], []
    for data in inputs:
        begin = time.perf_counter_ns()
        collector.projector.project(data, None, None, None, True)
        baseline.append((time.perf_counter_ns() - begin) / 1e6)
    begin_all = time.perf_counter()
    for data in inputs:
        begin = time.perf_counter_ns()
        submitted[data["litellm_call_id"]] = begin
        collector.submit(data, None, None, None, True)
        callbacks.append((time.perf_counter_ns() - begin) / 1e6)
    eventually(lambda: collector.metrics()["written"] == len(inputs))
    elapsed = time.perf_counter() - begin_all
    collector.close()
    assert len(list_events(path, limit=1000)) == len(inputs)
    assert collector.metrics()["loss_count"] == 0

    def distribution(values: list[float]) -> dict:
        return {
            "p50_ms": round(median(values), 4),
            "p95_ms": round(sorted(values)[int((len(values) - 1) * 0.95)], 4),
        }

    RESULTS["measurement"] = {
        "passed": True,
        "samples": len(inputs),
        "projection": distribution(baseline),
        "callback": distribution(callbacks),
        "additional_enqueue": distribution(
            [a - b for a, b in zip(callbacks, baseline, strict=True)]
        ),
        "enqueue_to_commit": distribution(commit_latencies),
        "rows_per_second": round(len(inputs) / elapsed, 1),
        "default_queue_recommendation": 1024,
        "drain_seconds_recommendation": 2,
        "sla": False,
    }


def test_windows_acl(tmp_path: Path) -> None:
    """Verify current-user-only grants on directory, SQLite and both WAL sidecars."""
    directory = tmp_path / "acl"
    child = subprocess.run(
        [
            str(ROOT / ".venv-litellm/Scripts/python.exe"),
            "-I",
            str(ROOT / "tests_p2/check_acl.py"),
            str(directory),
        ],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert child.returncode == 0
    result = json.loads(child.stdout)
    assert result["passed"] and result["files_checked"] == 4
    RESULTS["windows_acl"] = result
