"""Monitoring contracts use synthetic data, without provider connections."""

import asyncio
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from axi_gateway.access.policy import PolicyError
from axi_gateway.playground.models import ModelStore
from axi_gateway.storage import MIGRATIONS
from tests_p7.test_local_models import PROFILE, locked_windows_modules  # noqa: F401


def event(directory: Path, model: str, call_id: str, tokens: int | None = 17) -> None:
    path = directory / "proxies" / model / "events/events.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            db.executescript(MIGRATIONS[1] + MIGRATIONS[2])
            db.execute("PRAGMA user_version=2")
        db.execute(
            "INSERT INTO events (litellm_call_id,schema_version,started_at,"
            "model_alias,stream,provider_outcome,client_outcome,usage_source,"
            "prompt_tokens,completion_tokens,total_tokens,duration_ms,"
            "estimate_not_invoice) "
            "VALUES (?,0,'2026-11-23T05:00:00+00:00',?,0,'success','unknown',"
            "?,?,?,?,100,1)",
            (
                call_id,
                model,
                "reported" if tokens else "missing",
                10 if tokens else None,
                7 if tokens else None,
                tokens,
            ),
        )


def test_atomic_audit_legacy_and_delete(tmp_path: Path) -> None:
    store = ModelStore(tmp_path)
    saved = store.create(PROFILE)
    store.put(saved["id"], PROFILE | {"expected_revision": 1, "name": "renamed"})
    store.delete(saved["id"], 2)
    audit = ModelStore(tmp_path).audit()
    assert [r["action"] for r in audit] == ["delete", "update", "create"]
    assert {r["actor"] for r in audit} == {"local-operator"}
    assert PROFILE["key"] not in json.dumps(audit)
    assert "base_url" not in json.dumps(audit)


def test_audit_failure_does_not_commit_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ModelStore(tmp_path)
    saved = store.create(PROFILE)

    def fail(data: dict) -> None:
        raise PolicyError(503, "model_store_unavailable")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(PolicyError):
        store.put(saved["id"], PROFILE | {"expected_revision": 1})
    assert ModelStore(tmp_path).list()[0]["revision"] == 1
    assert len(ModelStore(tmp_path).audit()) == 1


def test_body_opt_in_encrypted_redacted_and_default_off(tmp_path: Path) -> None:
    from axi_gateway.playground.monitor import Monitor

    monitor = Monitor(tmp_path)
    model = {"id": "local-" + "a" * 16, "name": "Model", "model": "test", "revision": 1}
    ids = [str(uuid4()), str(uuid4())]
    for i, call_id in enumerate(ids):
        monitor.begin(call_id, model)
        monitor.finish(
            call_id,
            "success",
            123,
            [{"role": "user", "content": "body-sentinel secret-sentinel"}],
            "reply-sentinel",
            bool(i),
            ("secret-sentinel",),
        )
    assert monitor.content(ids[0]) == {
        "status": "disabled",
        "messages": None,
        "reply": None,
    }
    content = Monitor(tmp_path).content(ids[1])
    assert content["status"] == "saved"
    assert content["messages"][0]["content"] == "body-sentinel [已隐藏凭据]"
    for file in tmp_path.glob("monitor.sqlite3*"):
        assert b"body-sentinel" not in file.read_bytes()
        assert b"secret-sentinel" not in file.read_bytes()


def test_cross_model_counts_filter_pagination_null_and_history(tmp_path: Path) -> None:
    from axi_gateway.playground.monitor import Monitor
    from axi_gateway.playground.monitor_queries import MonitorQueries

    store = ModelStore(tmp_path)
    a, b = store.create(PROFILE), store.create(PROFILE | {"name": "Second"})
    ids = [str(uuid4()) for _ in range(3)]
    event(tmp_path, a["id"], ids[0])
    event(tmp_path, b["id"], ids[1], None)
    monitor = Monitor(tmp_path)
    monitor.begin(ids[0], a)
    monitor.finish(ids[0], "success", 150, [], None, False, ())
    monitor.begin(ids[2], b)
    monitor.finish(ids[2], "error", 80, [], None, False, (), "model_proxy_start_failed")
    store.delete(a["id"], 1)
    query = MonitorQueries(store)
    result = query.query({"size": "2"})
    assert result["total"] == 3 and len(result["items"]) == 2
    assert sum(g["requests"] for g in result["models"]) == 3
    by_source = {r["usage_source"]: r for r in result["usage"]}
    assert by_source["reported"]["total_tokens"] == 17
    assert by_source["missing"]["total_tokens"] is None
    assert query.query({"model": a["id"]})["total"] == 1
    assert query.query({"result": "error"})["total"] == 1
    assert query.query({"caller": "unknown"})["total"] == 1
    assert query.query({"start": "2027-01-01T00:00:00Z"})["total"] == 0
    assert len(query.query({"size": "2", "page": "2"})["items"]) == 1
    detail = query.detail(a["id"], ids[0])
    assert detail["record"]["actor"] == "local-operator"
    assert detail["record"]["name"] == PROFILE["name"]
    assert detail["content"]["status"] == "disabled"


def test_invalid_filters_and_missing_details(tmp_path: Path) -> None:
    from axi_gateway.playground.monitor_queries import MonitorQueries

    query = MonitorQueries(ModelStore(tmp_path))
    for params in (
        {"size": "101"},
        {"page": "0"},
        {"usage_source": "forged"},
        {"start": "2026-01-01"},
        {"result": "oops"},
    ):
        with pytest.raises((ValueError, PolicyError)):
            query.query(params)
    with pytest.raises(PolicyError):
        query.detail("../../outside", "not-a-uuid")


def test_content_option_is_explicit_boolean() -> None:
    from axi_gateway.playground.model_api import chat_input

    data = dict(
        model="local-a",
        revision=1,
        messages=[dict(role="user", content="hi")],
        max_tokens=64,
    )
    assert chat_input(data | {"save_content": True})["save_content"] is True
    with pytest.raises(PolicyError):
        chat_input(data | {"save_content": "true"})


def test_legacy_document_upgrades_without_losing_models(tmp_path: Path) -> None:
    import win32crypt

    store = ModelStore(tmp_path)
    saved = store.create(PROFILE)
    legacy = {saved["id"]: saved | {"key": PROFILE["key"]}}
    store.path.write_bytes(
        win32crypt.CryptProtectData(
            json.dumps(legacy).encode(), "legacy", None, None, None, 1
        )
    )
    assert store.audit() == [] and len(store.list()) == 1
    store.put(saved["id"], PROFILE | {"expected_revision": 1})
    assert len(store.audit()) == 1 and store.list()[0]["revision"] == 2


def test_runtime_records_success_error_and_exact_correlation(tmp_path: Path) -> None:
    from axi_gateway.playground.model_runtime import ModelRuntime
    from axi_gateway.playground.monitor_queries import MonitorQueries

    store = ModelStore(tmp_path)
    saved = store.create(PROFILE)
    runtime = ModelRuntime(Path.cwd(), store, offline=True)

    async def fake_start(model: dict) -> None:
        runtime.master = "synthetic-master"

    async def fake(request: httpx.Request) -> httpx.Response:
        event(tmp_path, saved["id"], request.headers["x-litellm-call-id"])
        if json.loads(request.content)["messages"][0]["content"] == "fail":
            return httpx.Response(429, json={"error": "private-error"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "reply-sentinel"}}]}
        )

    runtime._start = fake_start

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(fake), base_url="http://127.0.0.1"
        ) as client:
            runtime.client = client
            data = dict(
                model=saved["id"],
                revision=1,
                messages=[dict(role="user", content="hello")],
                max_tokens=64,
            )
            response = await runtime.chat(data)
            query = MonitorQueries(store)
            assert query.query({})["total"] == 1
            assert (
                query.detail(saved["id"], response["call_id"])["record"]["total_tokens"]
                == 17
            )
            with pytest.raises(PolicyError):
                await runtime.chat(
                    data | {"messages": [dict(role="user", content="fail")]}
                )
            assert query.query({"result": "error"})["total"] == 1

    asyncio.run(exercise())


def test_query_routes_security_and_corruption(tmp_path: Path) -> None:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from axi_gateway.playground.model_api import model_routes
    from axi_gateway.playground.model_runtime import ModelRuntime
    from axi_gateway.playground.server import DemoGate

    store = ModelStore(tmp_path)
    store.create(PROFILE)

    async def error(request: Request, exc: PolicyError) -> JSONResponse:
        return JSONResponse({"error": exc.code}, exc.status)

    async def exercise() -> None:
        app = DemoGate(
            Starlette(
                routes=model_routes(store, ModelRuntime(Path.cwd(), store)),
                exception_handlers={PolicyError: error},
            ),
            8790,
        )
        headers = {"X-Playground": "1", "Origin": "http://127.0.0.1:8790"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8790"
        ) as client:
            for path in ("records", "audit"):
                response = await client.get("/api/local/" + path, headers=headers)
                assert (
                    response.status_code == 200 and PROFILE["key"] not in response.text
                )
                assert "content-security-policy" in response.headers
                assert (
                    await client.get(
                        "/api/local/" + path,
                        headers=headers | {"Origin": "https://evil.example"},
                    )
                ).status_code == 403
                assert (
                    await client.post("/api/local/" + path, headers=headers)
                ).status_code == 405
            (tmp_path / "monitor.sqlite3").write_bytes(b"corrupt")
            assert (
                await client.get("/api/local/records", headers=headers)
            ).status_code == 503

    asyncio.run(exercise())


def test_historical_request_does_not_inherit_new_upstream_model(tmp_path: Path) -> None:
    from axi_gateway.playground.monitor_queries import MonitorQueries

    store = ModelStore(tmp_path)
    model = store.create(PROFILE)
    call_id = str(uuid4())
    event(tmp_path, model["id"], call_id)
    store.put(model["id"], PROFILE | {"model": "new-model", "expected_revision": 1})
    record = MonitorQueries(store).detail(model["id"], call_id)["record"]
    assert record["upstream_model"] is None
    assert record["revision"] is None and record["actor"] is None


def test_audit_redacts_legacy_key_in_model_field_after_deletion(tmp_path: Path) -> None:
    store = ModelStore(tmp_path)
    alias = "local-0123456789abcdef"
    store._write({alias: PROFILE | dict(id=alias, revision=1, model=PROFILE["key"])})
    store.delete(alias, 1)
    assert store.audit()[0]["action"] == "delete"
    assert PROFILE["key"] not in json.dumps(store.audit())


def test_metadata_detail_does_not_decrypt_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axi_gateway.playground.monitor_queries import MonitorQueries

    store = ModelStore(tmp_path)
    saved = store.create(PROFILE)
    call_id = str(uuid4())
    event(tmp_path, saved["id"], call_id)
    query = MonitorQueries(store)

    def forbidden(call_id: str) -> dict:
        raise AssertionError("metadata request must not decrypt content")

    monkeypatch.setattr(query.monitor, "content", forbidden)
    result = query.detail(saved["id"], call_id, include_content=False)
    assert result["record"]["total_tokens"] == 17
    assert result["content"]["messages"] is None
