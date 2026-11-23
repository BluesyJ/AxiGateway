"""Own a fresh synthetic policy store, fake upstream and isolated LiteLLM process."""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from axi_gateway.access.identity import read_demo_config
from axi_gateway.access.policy import PolicyStore

APPS = ("demo-app-a", "demo-app-b")
MODELS = ("model-a", "model-b")
REPLY = "这是离线演示回复。请求已通过网关；本次没有调用真实模型。"


class DemoSession:
    """Keep signing material in memory and all durable data in a fresh demo folder."""

    @classmethod
    def create(cls, root: Path) -> "DemoSession":
        """Build synthetic identities and policy; never read any existing runtime."""
        session = cls()
        session.directory = root / ".cache/playground" / uuid4().hex / "demo"
        session.directory.mkdir(parents=True)
        session.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = session.private.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        )
        session.trust = session.directory / "demo-trust.json"
        session.trust.write_text(
            json.dumps(
                dict(
                    demo=True,
                    models=list(MODELS),
                    database="demo-policy.sqlite3",
                    admins=["demo-admin"],
                    issuer="playground-demo",
                    model_audience="demo-models",
                    admin_audience="demo-admin",
                    public_key=public.decode("ascii"),
                )
            ),
            encoding="utf8",
        )
        session.config = read_demo_config(session.trust, root)
        session.store = PolicyStore(session.config.database, MODELS)
        session.store.initialize()
        session.store.put("demo-admin", APPS[0], [MODELS[0]], 10, True, 0)
        session.store.put("demo-admin", APPS[1], list(MODELS), 10, True, 0)
        session.events = session.directory / "proxy/events/events.sqlite3"
        session.upstream_calls = 0
        return session

    def signed(self, subject: str, audience: str) -> str:
        """Issue only internal short-lived demo credentials, never return them to UI."""
        now = int(time.time())
        return jwt.encode(
            dict(
                sub=subject,
                aud=audience,
                iss="playground-demo",
                iat=now,
                nbf=now,
                exp=now + 900,
            ),
            self.private,
            algorithm="RS256",
        )

    async def completion(self, request: Request) -> JSONResponse:
        """Return synthetic usage and a fixed reply; retain no input or output text."""
        data = await request.json()
        self.upstream_calls += 1
        return JSONResponse(
            dict(
                id="demo-" + uuid4().hex,
                object="chat.completion",
                created=int(time.time()),
                model=data.get("model", "deepseek-chat"),
                choices=[
                    dict(
                        index=0,
                        message=dict(role="assistant", content=REPLY),
                        finish_reason="stop",
                    )
                ],
                usage=dict(prompt_tokens=10, completion_tokens=7, total_tokens=17),
            )
        )


@asynccontextmanager
async def runtime(root: Path) -> AsyncIterator[tuple[DemoSession, httpx.AsyncClient]]:
    """Start the fake endpoint and original proxy, then release only our processes."""
    session = DemoSession.create(root)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    upstream_port = sock.getsockname()[1]
    upstream = uvicorn.Server(
        uvicorn.Config(
            Starlette(
                routes=[
                    Route("/v1/chat/completions", session.completion, methods=["POST"])
                ]
            ),
            host="127.0.0.1",
            port=upstream_port,
            access_log=False,
            log_config=None,
        )
    )
    task = asyncio.create_task(upstream.serve(sockets=[sock]))
    process = None
    output = None
    try:
        async with asyncio.timeout(10):
            while not upstream.started:
                if task.done():
                    raise RuntimeError("fake_upstream_start_failed")
                await asyncio.sleep(0.05)
        model_config = session.directory / "demo-models.yaml"
        model_config.write_text(
            "models:\n  - model_alias: model-a\n"
            "    provider_model: deepseek/deepseek-chat\n"
            "  - model_alias: model-b\n"
            "    provider_model: deepseek/deepseek-reasoner\n"
            f"api_base: http://127.0.0.1:{upstream_port}/v1\nqueue_capacity: 256\n",
            encoding="utf8",
        )
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            proxy_port = available.getsockname()[1]
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
        env.update(UPSTREAM_API_KEY="demo-" + secrets.token_urlsafe(24), PYTHONUTF8="1")
        process = await asyncio.create_subprocess_exec(
            str(root / ".venv-litellm/Scripts/python.exe"),
            "-I",
            "-S",
            str(root / "scripts/start_p1.py"),
            "--offline",
            "--access-demo",
            str(session.trust),
            "--config",
            str(model_config),
            "--data-dir",
            str(session.directory / "proxy"),
            "--port",
            str(proxy_port),
            cwd=root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = asyncio.create_task(process.communicate())
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{proxy_port}",
            timeout=20,
            trust_env=False,
        ) as client:
            async with asyncio.timeout(90):
                while True:
                    if process.returncode is not None:
                        raise RuntimeError("demo_proxy_start_failed")
                    try:
                        if (
                            await client.get("/health/liveliness", timeout=1)
                        ).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            yield session, client
    finally:
        if process is not None and process.returncode is None:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if output is not None:
            await asyncio.wait_for(output, 10)
        upstream.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()
