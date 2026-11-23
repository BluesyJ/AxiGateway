"""Playground boundaries use synthetic identities and an in-process transport."""

import asyncio
import json
from pathlib import Path

import httpx


def test_demo_gate_and_policy_changes(tmp_path: Path) -> None:
    """Cross-site writes must not alter policies; same-origin edits use revisions."""
    from axi_gateway.playground.runtime import DemoSession
    from axi_gateway.playground.server import create_app

    async def exercise() -> None:
        session = DemoSession.create(tmp_path)
        async with httpx.AsyncClient() as proxy:
            app = create_app(session, 8790, tmp_path, proxy)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8790",
            ) as client:
                headers = {"X-Playground": "1", "Origin": "http://127.0.0.1:8790"}
                state = await client.get("/api/demo/state", headers=headers)
                assert state.status_code == 200
                assert len(state.json()["apps"]) == 2
                assert "default-src 'none'" in state.headers["content-security-policy"]
                before = len(session.store.audit())
                policy = dict(
                    models=["model-b"], rpm=2, enabled=False, expected_revision=1
                )
                path = "/api/demo/apps/demo-app-a"
                for bad in (
                    {},
                    {"X-Playground": "1"},
                    headers | {"Origin": "http://evil.example"},
                    headers | {"Host": "evil.example"},
                ):
                    assert (
                        await client.put(path, headers=bad, json=policy)
                    ).status_code == 403
                assert len(session.store.audit()) == before
                result = await client.put(path, headers=headers, json=policy)
                assert result.status_code == 200
                assert result.json()["enabled"] is False
                assert (
                    await client.put(path, headers=headers, json=policy)
                ).status_code == 409
                assert (await client.delete(path, headers=headers)).status_code == 405
                assert (await client.get("/api/demo/state")).status_code == 403
                assert (
                    await client.get("/api/demo/events", headers=headers)
                ).status_code == 503

    asyncio.run(exercise())


def test_call_validation_and_privacy(tmp_path: Path) -> None:
    """Only fixed demo apps/models reach the proxy, and upstream secrets never echo."""
    from axi_gateway.playground.runtime import DemoSession
    from axi_gateway.playground.server import create_app

    async def exercise() -> None:
        session = DemoSession.create(tmp_path)
        sent = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            assert (
                session.config.model_verifier.subject(
                    request.headers["authorization"][7:]
                )
                == "demo-app-a"
            )
            return httpx.Response(403, json={"error": "private-upstream-sentinel"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream), base_url="http://127.0.0.1:1"
        ) as proxy:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=create_app(session, 8790, tmp_path, proxy)
                ),
                base_url="http://127.0.0.1:8790",
                headers={"X-Playground": "1", "Origin": "http://127.0.0.1:8790"},
            ) as client:
                payload = dict(
                    app="demo-app-a", model="model-a", prompt="synthetic input"
                )
                for bad in (
                    payload | {"app": "unregistered"},
                    payload | {"model": "external-model"},
                    payload | {"api_base": "https://example.com"},
                    payload | {"prompt": ""},
                    payload | {"prompt": "x" * 4097},
                ):
                    assert (
                        await client.post("/api/demo/call", json=bad)
                    ).status_code == 400
                assert not sent
                response = await client.post("/api/demo/call", json=payload)
                assert response.status_code == 403
                assert "private-upstream-sentinel" not in response.text
                state = await client.get("/api/demo/state")
                assert "Bearer" not in state.text and "PRIVATE KEY" not in state.text
                assert not any(
                    p.name.endswith(".pem") for p in session.directory.iterdir()
                )
                trust = json.loads(session.trust.read_text(encoding="utf8"))
                assert "PRIVATE" not in trust["public_key"]

    asyncio.run(exercise())


def test_proxy_failure_is_not_success(tmp_path: Path) -> None:
    """An unavailable proxy must remain an error the operator can act on."""
    from axi_gateway.playground.runtime import DemoSession
    from axi_gateway.playground.server import create_app

    async def exercise() -> None:
        async def offline(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sensitive-sentinel", request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(offline), base_url="http://127.0.0.1:1"
        ) as proxy:
            app = create_app(DemoSession.create(tmp_path), 8790, tmp_path, proxy)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8790"
            ) as client:
                result = await client.post(
                    "/api/demo/call",
                    headers={"X-Playground": "1", "Origin": "http://127.0.0.1:8790"},
                    json=dict(app="demo-app-a", model="model-a", prompt="demo"),
                )
                assert result.status_code == 503
                assert result.json()["error"] == "proxy_unavailable"
                assert "sensitive-sentinel" not in result.text

    asyncio.run(exercise())


def test_unexpected_model_reply_is_not_fabricated(tmp_path: Path) -> None:
    """A successful status without the expected demo reply must not invent success."""
    from axi_gateway.playground.runtime import DemoSession
    from axi_gateway.playground.server import create_app

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
            base_url="http://127.0.0.1:1",
        ) as proxy:
            app = create_app(DemoSession.create(tmp_path), 8790, tmp_path, proxy)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8790",
            ) as client:
                result = await client.post(
                    "/api/demo/call",
                    headers={"X-Playground": "1", "Origin": "http://127.0.0.1:8790"},
                    json=dict(app="demo-app-a", model="model-a", prompt="demo"),
                )
                assert result.status_code == 502
                assert "reply" not in result.json()

    asyncio.run(exercise())
