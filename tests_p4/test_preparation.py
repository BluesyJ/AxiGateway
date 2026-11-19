"""Focused offline P4.0 checks for model profiles, reconciliation and call limits."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml

from axi_gateway.bootstrap import StartupError
from axi_gateway.events import Projector, Usage
from axi_gateway.launcher import proxy_configuration, read_settings
from axi_gateway.queries import read_connection
from axi_gateway.storage import MIGRATIONS, SQLiteSink, event_values
from scripts.reconcile import reconcile
from tests_p2.samples import kwargs, sample
from tests_p4.policy import check_no_retries_or_fallbacks
from tests_p4.transport import Budget, ClientTransport

ROOT = Path(__file__).resolve().parents[1]


def test_profiles_and_aliases(tmp_path: Path) -> None:
    """Both profiles preserve routing, while unsupported fields/duplicates fail."""
    for filename, expected in (
        ("litellm.example.yaml", 1),
        ("litellm.astrbot.example.yaml", 2),
    ):
        settings = read_settings(ROOT / "config" / filename)
        config = proxy_configuration(settings)
        assert len(config["model_list"]) == expected
        assert config["router_settings"]["num_retries"] == 0
        assert config["router_settings"]["fallbacks"] == []
    path = tmp_path / "bad.yaml"
    settings["models"].append(settings["models"][0])
    path.write_text(yaml.safe_dump(settings))
    with pytest.raises(StartupError):
        read_settings(path)
    projector = Projector(
        None, set(), "test", aliases=("deepseek-v4-pro", "deepseek-flash")
    )
    for model in projector.aliases:
        values = {
            **kwargs(),
            "model": model,
            "response_cost": 99,
            "litellm_params": {"proxy_server_request": {"body": {"model": model}}},
        }
        event = projector.project(
            values, {"usage": {"total_tokens": 4}}, None, None, True
        )
        assert event.model_alias == model and event.cost.amount is None
    with pytest.raises(ValueError):
        projector.project(kwargs(), None, None, None, True)


def test_reconciliation(tmp_path: Path) -> None:
    """Check timezone windows, NULLs, source separation and reported-only verdict."""
    path = tmp_path / "events.sqlite3"
    sink = SQLiteSink(path)
    events = [
        replace(
            sample(),
            started_at="2026-11-04T00:00:00+00:00",
            usage=Usage(20, 10, 30, source),
        )
        for source in ("reported", "estimated", "unknown", "derived", "missing")
    ]
    events.append(
        replace(
            sample(),
            started_at="2026-11-04T01:00:00+00:00",
            usage=Usage(900, 100, 1000, "reported"),
        )
    )
    sink.write_batch(events, {}, [])
    sink.close()
    start, end = "2026-11-04T08:00:00+08:00", "2026-11-04T09:00:00+08:00"
    result = reconcile(path, start, end, {"total_tokens": 30})
    assert result["reported_only_target"] == "pass"
    assert all(v["requests"] == 1 for v in result["by_usage_source"].values())
    assert result["comparisons"]["total_tokens"]["difference_percent"] == 0
    assert (
        reconcile(path, start, end, {"total_tokens": 60})["reported_only_target"]
        == "fail"
    )
    zero = reconcile(path, start, end, {"total_tokens": 0})
    assert zero["reported_only_target"] == "fail"
    assert zero["comparisons"]["total_tokens"]["difference_percent"] is None
    empty = reconcile(path, end, "2026-11-04T09:30:00+08:00", {})
    assert empty["by_usage_source"]["unknown"]["total_tokens"] is None
    assert empty["reported_only_target"] == "indeterminate"
    for bad in ("2026-11-04T00:00:00", "bad"):
        with pytest.raises(ValueError):
            reconcile(path, bad, end, {})
    with read_connection(path) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM events")
    privacy = json.dumps(result)
    assert all(e.litellm_call_id not in privacy for e in events)


def test_budget_and_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The thirteenth network attempt is blocked and max_tokens cannot exceed 64."""
    sends = []

    async def underlying(self: object, request: httpx.Request) -> httpx.Response:
        sends.append(True)
        raise httpx.ConnectError("synthetic", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", underlying)
    endpoint = "http://127.0.0.1:1/v1/chat/completions"

    async def cases() -> None:
        for direct in (True, False):
            budget = Budget(tmp_path)
            transport = ClientTransport(endpoint, budget, direct, tmp_path)
            async with httpx.AsyncClient(transport=transport) as client:
                payload = {"model": "deepseek-flash", "max_tokens": 65}
                with pytest.raises(RuntimeError):
                    await client.post(endpoint, json=payload)
                payload["max_tokens"] = 64
                for _ in range(6):
                    with pytest.raises(httpx.ConnectError):
                        await client.post(endpoint, json=payload)
                if not direct:
                    with pytest.raises(RuntimeError, match="budget_exhausted"):
                        await client.post(endpoint, json=payload)

    asyncio.run(cases())
    assert len(sends) == len(list(tmp_path.glob("attempt-*"))) == 12


def test_preflight_rejects_replay() -> None:
    """Check the same effective runtime configuration as the smoke child."""
    config = proxy_configuration(
        read_settings(ROOT / "config/litellm.astrbot.example.yaml")
    )
    check_no_retries_or_fallbacks(config)
    for section in ("litellm_settings", "router_settings"):
        for key, value in (("num_retries", 1), ("fallbacks", ["spare"])):
            original = config[section][key]
            config[section][key] = value
            with pytest.raises(ValueError):
                check_no_retries_or_fallbacks(config)
            config[section][key] = original


def test_versioned_usage_and_migration(tmp_path: Path) -> None:
    """Keep original rows and counters, accept reported cache splits after migration."""
    raw = {
        "prompt_tokens": 20,
        "total_tokens": 32,
        "completion_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 0},
        "prompt_cache_hit_tokens": 8,
        "prompt_cache_miss_tokens": 12,
    }
    project = Projector("m", set(), "test", litellm_version="1.106.5").project
    event = project({**kwargs(), "stream": False}, {"usage": raw}, None, None, True)
    assert (
        event.usage.usage_source,
        event.usage.cache_hit_tokens,
        event.usage.cache_miss_tokens,
        event.usage.cache_miss_source,
    ) == ("reported", 8, 12, "reported")
    for version in ("1.106.4", "1.106.6"):
        other = Projector("m", set(), "test", litellm_version=version)
        assert (
            other.project(kwargs(), {"usage": raw}, None, None, True).usage.usage_source
            == "unknown"
        )
    assert (
        project(kwargs(), {"usage": raw}, None, None, False).usage.usage_source
        != "reported"
    )
    without_miss = dict(raw)
    del without_miss["prompt_cache_miss_tokens"]
    assert (
        project(
            kwargs(), {"usage": without_miss}, None, None, True
        ).usage.cache_miss_source
        == "derived"
    )
    path = tmp_path / "old.sqlite3"
    old = sample()
    with sqlite3.connect(path) as con:
        con.executescript(MIGRATIONS[1] + "PRAGMA user_version=1;")
        con.execute(
            "INSERT INTO events VALUES (" + ",".join("?" * 26) + ")", event_values(old)
        )
        con.execute("INSERT INTO counters VALUES ('written',1)")
    con.close()
    with read_connection(path) as reader:
        assert reader.execute("PRAGMA user_version").fetchone()[0] == 1
    sink = SQLiteSink(path)
    sink.write_batch([event], {"written": 1}, [])
    sink.close()
    with read_connection(path) as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reader.execute("SELECT value FROM counters").fetchone()[0] == 2
        assert [
            tuple(r)
            for r in reader.execute(
                "SELECT litellm_call_id,cache_miss_source FROM events ORDER BY rowid"
            )
        ] == [(old.litellm_call_id, None), (event.litellm_call_id, "reported")]
