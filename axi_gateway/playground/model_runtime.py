"""Run user-selected models through the original locked LiteLLM Proxy."""

import asyncio
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from axi_gateway.access.policy import PolicyError
from axi_gateway.playground.models import ModelStore
from axi_gateway.secrets import master_key, private_event_directory


class ModelRuntime:
    """Own one active model process; serialize selection changes with calls."""

    def __init__(self, root: Path, store: ModelStore, offline: bool = False) -> None:
        self.root, self.store, self.offline = root, store, offline
        self.lock = asyncio.Lock()
        self.process = self.output = self.client = None
        self.active = None
        self.master = ""

    async def close(self) -> None:
        """Stop only the process tree created by this instance."""
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        if self.process is not None and self.process.returncode is None:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if self.output is not None:
            await self.output
        self.process = self.output = self.active = None
        self.master = ""

    async def _start(self, model: dict) -> None:
        identity = (model["id"], model["revision"])
        if (
            self.active == identity
            and self.process is not None
            and self.process.returncode is None
        ):
            return
        await self.close()
        if self.offline and urlsplit(model["base_url"]).hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise PolicyError(400, "offline_requires_loopback_upstream")
        directory = self.store.directory / "proxies" / model["id"]
        await asyncio.to_thread(private_event_directory, directory)
        self.master = await asyncio.to_thread(
            master_key, directory / "master.key.dpapi"
        )
        config = directory / "model.yaml"
        config.write_text(
            yaml.safe_dump(
                dict(
                    model_alias=model["id"],
                    provider_model="openai/" + model["model"],
                    api_base=model["base_url"],
                    queue_capacity=256,
                )
            ),
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
        env.update(UPSTREAM_API_KEY=model["key"] or "local-no-key", PYTHONUTF8="1")
        self.process = await asyncio.create_subprocess_exec(
            str(self.root / ".venv-litellm/Scripts/python.exe"),
            "-I",
            "-S",
            str(self.root / "scripts/start_p1.py"),
            "--config",
            str(config),
            "--data-dir",
            str(directory),
            "--port",
            str(port),
            *(["--offline"] if self.offline else []),
            cwd=self.root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.output = asyncio.create_task(self.process.communicate())
        self.client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=65, trust_env=False
        )
        try:
            async with asyncio.timeout(90):
                while True:
                    if self.process.returncode is not None:
                        raise PolicyError(503, "model_proxy_start_failed")
                    try:
                        if (
                            await self.client.get("/health/liveliness", timeout=1)
                        ).status_code == 200:
                            self.active = identity
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
        except BaseException:
            await self.close()
            raise

    async def chat(self, data: dict) -> dict:
        """Send one explicit request; do not probe, retry or persist message bodies."""
        async with self.lock:
            model = await asyncio.to_thread(self.store.selected, data["model"])
            if data["revision"] != model["revision"]:
                raise PolicyError(409, "model_configuration_changed")
            started = time.monotonic()
            try:
                await self._start(model)
                response = await self.client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.master},
                    json=dict(
                        model=model["id"],
                        messages=data["messages"],
                        max_tokens=data["max_tokens"],
                        stream=False,
                    ),
                )
            except (httpx.HTTPError, TimeoutError, OSError):
                raise PolicyError(503, "model_connection_failed") from None
            if response.status_code != 200:
                code = {
                    401: "upstream_auth_failed",
                    403: "upstream_access_denied",
                    429: "upstream_rate_limited",
                }.get(response.status_code, "upstream_request_failed")
                raise PolicyError(
                    response.status_code if 400 <= response.status_code <= 599 else 502,
                    code,
                )
            try:
                reply = response.json()["choices"][0]["message"]["content"]
                if not isinstance(reply, str) or not reply:
                    raise ValueError
            except (ValueError, KeyError, IndexError, TypeError):
                raise PolicyError(502, "model_no_text_reply") from None
            for secret in (model["key"], self.master):
                if secret:
                    reply = reply.replace(secret, "[已隐藏凭据]")
            return dict(
                reply=reply,
                model=model["id"],
                duration_ms=round((time.monotonic() - started) * 1000),
            )
