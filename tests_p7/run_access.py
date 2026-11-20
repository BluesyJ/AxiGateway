"""Bounded TCP demonstration: real locked Proxy, synthetic identities and upstream."""

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from axi_gateway.access.identity import read_demo_config
from axi_gateway.access.policy import PolicyStore
from axi_gateway.access.server import create_app
from tests.deepseek_fixture import ANSWER, PROMPT, SECRET, DeepSeekFixture
from tests.servers import TestServer, serve

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def management(config_path: Path) -> AsyncIterator[tuple[str, int]]:
    """Bind once to an available local port before constructing the Host policy."""
    config = read_demo_config(config_path, ROOT)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        port = sock.getsockname()[1]
        server = TestServer(
            uvicorn.Config(
                create_app(
                    PolicyStore(config.database, config.models),
                    config.admin_verifier,
                    config.admins,
                    port,
                ),
                log_config=None,
                access_log=False,
                proxy_headers=False,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}", port
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)


async def run(*, rejections_only: bool = False) -> dict:
    """Exercise a fixed 100 model admissions and at most 20 rejected probes."""
    directory = ROOT / ".cache/access-demo" / str(time.time_ns()) / "demo"
    directory.mkdir(parents=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    trust = directory / "trust.json"
    trust.write_text(
        json.dumps(
            dict(
                demo=True,
                models=["model-a", "model-b"],
                database="demo-policy.sqlite3",
                admins=["demo-admin"],
                issuer="demo-issuer",
                model_audience="demo-models",
                admin_audience="demo-admin",
                public_key=public.decode(),
            )
        ),
        encoding="utf8",
    )
    store = PolicyStore(directory / "demo-policy.sqlite3", ("model-a", "model-b"))
    store.initialize()
    now = int(time.time())

    def signed(subject: str, audience: str) -> str:
        return jwt.encode(
            dict(
                sub=subject,
                aud=audience,
                iss="demo-issuer",
                iat=now,
                nbf=now,
                exp=now + 900,
            ),
            private,
            algorithm="RS256",
        )

    admin = signed("demo-admin", "demo-admin")
    tokens = {app: signed(app, "demo-models") for app in ("demo-app-a", "demo-app-b")}
    sentinels = [admin, *tokens.values(), SECRET, PROMPT, ANSWER, "synthetic reasoning"]
    fixture = DeepSeekFixture()
    process = None
    output = None
    result = {
        "passed": False,
        "real_calls": 0,
        "allowed_fake_calls": 0,
        "rejected_probes": 0,
    }
    try:
        async with (
            serve(fixture.app) as upstream,
            management(trust) as (admin_url, admin_port),
            httpx.AsyncClient(timeout=20, trust_env=False) as client,
        ):
            auth = {"Authorization": "Bearer " + admin}
            for app in tokens:
                response = await client.put(
                    admin_url + "/api/apps/" + app,
                    headers=auth,
                    json=dict(
                        models=["model-a", "model-b"],
                        rpm=1000,
                        enabled=True,
                        expected_revision=0,
                    ),
                )
                assert response.status_code == 200, "create_application"
            config = directory / "models.yaml"
            config.write_text(
                "models:\n  - model_alias: model-a\n"
                "    provider_model: deepseek/deepseek-chat\n"
                "  - model_alias: model-b\n"
                "    provider_model: deepseek/deepseek-reasoner\n"
                + f"api_base: {upstream}/v1\nqueue_capacity: 256\n",
                encoding="utf8",
            )
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            env = {
                k: v
                for k, v in os.environ.items()
                if k.upper()
                in {
                    "SYSTEMROOT",
                    "WINDIR",
                    "PATH",
                    "TEMP",
                    "TMP",
                    "COMSPEC",
                    "PATHEXT",
                    "SYSTEMDRIVE",
                    "USERPROFILE",
                    "APPDATA",
                    "LOCALAPPDATA",
                }
            }
            env.update(UPSTREAM_API_KEY=SECRET, PYTHONUTF8="1")
            process = await asyncio.create_subprocess_exec(
                str(ROOT / ".venv-litellm/Scripts/python.exe"),
                "-I",
                "-S",
                str(ROOT / "scripts/start_p1.py"),
                "--offline",
                "--access-demo",
                str(trust),
                "--config",
                str(config),
                "--data-dir",
                str(directory / "proxy"),
                "--port",
                str(port),
                env=env,
                cwd=ROOT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            output = asyncio.create_task(process.communicate())
            base = f"http://127.0.0.1:{port}"
            async with asyncio.timeout(90):
                while True:
                    if process.returncode is not None:
                        raise AssertionError("proxy_start_failed")
                    try:
                        if (
                            await client.get(base + "/health/liveliness", timeout=1)
                        ).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            listeners = await asyncio.to_thread(
                subprocess.run,
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Get-NetTCPConnection -State Listen "
                    f"-LocalPort {port},{admin_port} | "
                    "Select-Object LocalAddress | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            addresses = json.loads(listeners.stdout)
            assert len(addresses) == 2 and all(
                row["LocalAddress"] == "127.0.0.1" for row in addresses
            ), "loopback_binding"
            request = dict(
                model="model-a",
                messages=[dict(role="user", content=PROMPT)],
                max_tokens=32,
            )
            for extension in (
                "fallbacks",
                "context_window_fallbacks",
                "content_policy_fallbacks",
                "num_retries",
            ):
                response = await client.post(
                    base + "/v1/chat/completions",
                    headers={"Authorization": "Bearer " + tokens["demo-app-a"]},
                    json=request | {extension: ["model-b"]},
                )
                assert response.status_code == 403, "routing_override_denied"
                assert not any(value in response.text for value in sentinels)
                result["rejected_probes"] += 1
            assert len(fixture.seen) == 0, "routing_rejection_reached_upstream"
            if rejections_only:
                result.update(passed=True, routing_overrides_denied=True, loopback=True)
                return result
            for index in range(100):
                app = "demo-app-a" if index % 2 == 0 else "demo-app-b"
                payload = request | {
                    "model": "model-a" if index % 4 < 2 else "model-b",
                    "metadata": {
                        "client_label": "self-reported",
                        "user_api_key_alias": "forged-admin",
                    },
                    "litellm_metadata": {"user_api_key_alias": "forged-admin"},
                }
                if index == 99:
                    payload.update(stream=True, stream_options={"include_usage": True})
                response = await client.post(
                    base + "/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer " + tokens[app],
                        "x-axi-client": "untrusted-label",
                    },
                    json=payload,
                )
                assert response.status_code == 200, "allowed_forwarding"
                result["allowed_fake_calls"] += 1
            assert len(fixture.seen) == 100, "upstream_count"
            events = directory / "proxy/events/events.sqlite3"
            async with asyncio.timeout(20):
                while True:
                    with sqlite3.connect(events.as_uri() + "?mode=ro", uri=True) as db:
                        count = db.execute("SELECT count(*) FROM events").fetchone()[0]
                    if count == 100:
                        break
                    await asyncio.sleep(0.1)
            with sqlite3.connect(events.as_uri() + "?mode=ro", uri=True) as db:
                assert dict(
                    db.execute(
                        "SELECT key_alias,count(*) FROM events GROUP BY key_alias"
                    )
                ) == {"demo-app-a": 50, "demo-app-b": 50}, "trusted_identity_projection"
                assert dict(
                    db.execute(
                        "SELECT model_alias,count(*) FROM events GROUP BY model_alias"
                    )
                ) == {"model-a": 50, "model-b": 50}, "model_projection"

            async def denied(
                credential: str, payload: dict, status: int | None = None
            ) -> None:
                response = await client.post(
                    base + "/v1/chat/completions",
                    headers={"Authorization": "Bearer " + credential},
                    json=payload,
                )
                assert (
                    response.status_code == status
                    if status
                    else response.status_code >= 400
                )
                assert not any(value in response.text for value in sentinels), (
                    "rejection_privacy"
                )
                result["rejected_probes"] += 1
                assert len(fixture.seen) == 100, "rejected_reached_upstream"

            await denied(admin, request)
            await denied("synthetic-invalid", request)
            response = await client.put(
                admin_url + "/api/apps/demo-app-a",
                headers=auth,
                json=dict(models=["model-a"], rpm=1, enabled=True, expected_revision=1),
            )
            assert response.status_code == 200
            # Keep the RPM test deterministic if wall time crossed a minute boundary.
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "UPDATE rpm SET minute=?,used=1 WHERE app=?",
                    (int(time.time() // 60), "demo-app-a"),
                )
            await denied(tokens["demo-app-a"], request, 429)
            await denied(tokens["demo-app-a"], request | {"model": "model-b"}, 403)
            visible = await client.get(
                base + "/v1/models",
                headers={"Authorization": "Bearer " + tokens["demo-app-a"]},
            )
            assert visible.status_code == 200 and {
                m["id"] for m in visible.json()["data"]
            } == {"model-a"}, "model_catalog_filter"
            response = await client.put(
                admin_url + "/api/apps/demo-app-b",
                headers=auth,
                json=dict(
                    models=["model-a", "model-b"],
                    rpm=1000,
                    enabled=False,
                    expected_revision=1,
                ),
            )
            assert response.status_code == 200
            await denied(tokens["demo-app-b"], request, 403)
            for route in ("/api/apps", "/api/audit"):
                response = await client.get(admin_url + route, headers=auth)
                assert response.status_code == 200 and not any(
                    value in response.text for value in sentinels
                ), "management_privacy"
            assert len(store.audit()) == 4
            for path in directory.rglob("*.sqlite3*"):
                raw = path.read_bytes()
                assert not any(value.encode() in raw for value in sentinels), (
                    "stored_privacy"
                )
            result.update(
                passed=True,
                trusted_identity=True,
                model_filter=True,
                rpm=True,
                disable=True,
                audit_rows_verified=True,
                privacy=True,
                loopback=True,
            )
    finally:
        if process is not None and process.returncode is None:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if output is not None:
            raw = (await asyncio.wait_for(output, 10))[0]
            assert not any(value.encode() in raw for value in sentinels), (
                "diagnostic_privacy"
            )
        result["upstream_calls"] = len(fixture.seen)
        (directory / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf8"
        )
        print(json.dumps(result), flush=True)
    return result
