"""Browser fixture: a loopback-only Playground and an authenticated fake provider."""

import asyncio
import json
import os
import site
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from axi_gateway.bootstrap import verify_environment  # noqa: E402
from axi_gateway.launcher import loopback_guard  # noqa: E402


def main() -> None:
    """Create a new synthetic store without reading the user's saved models."""
    if not sys.flags.isolated or not sys.flags.no_site:
        raise SystemExit("isolated_environment_required")
    verify_environment(ROOT, ROOT / ".venv-litellm")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    sys.addaudithook(loopback_guard)
    sys.prefix = sys.exec_prefix = str(ROOT / ".venv-litellm")
    site.addsitedir(str(ROOT / ".venv-litellm/Lib/site-packages"))
    import uvicorn
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    counts = {"calls": 0, "credential_mismatches": 0}

    async def complete(request: Request) -> JSONResponse:
        data = await request.json()
        counts["calls"] += 1
        expected = (
            "synthetic-key-b" if data["model"] == "model-b" else "synthetic-key-a"
        )
        if request.headers.get("authorization") != "Bearer " + expected:
            counts["credential_mismatches"] += 1
            return JSONResponse({"error": {"message": "private-credential-error"}}, 401)
        if data["model"] == "fails":
            return JSONResponse(
                {"error": {"message": "private-upstream-error-sentinel"}}, 500
            )
        return JSONResponse(
            dict(
                id="test-" + uuid4().hex,
                object="chat.completion",
                created=1,
                model=data["model"],
                choices=[
                    dict(
                        index=0,
                        message=dict(
                            role="assistant",
                            content=(
                                f"测试回复：{data['model']}，"
                                f"收到 {len(data['messages'])} 条消息。"
                            ),
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=dict(prompt_tokens=10, completion_tokens=7, total_tokens=17),
            )
        )

    async def stats(request: Request) -> JSONResponse:
        return JSONResponse(counts)

    async def run() -> None:
        directory = ROOT / ".cache/local-model-browser" / uuid4().hex
        directory.mkdir(parents=True)
        (ROOT / ".cache/local-model-browser/current.json").write_text(
            json.dumps({"directory": str(directory)}), encoding="utf8"
        )
        fake = uvicorn.Server(
            uvicorn.Config(
                Starlette(
                    routes=[
                        Route("/v1/chat/completions", complete, methods=["POST"]),
                        Route("/__stats", stats),
                    ]
                ),
                host="127.0.0.1",
                port=18792,
                access_log=False,
                log_config=None,
            )
        )
        task = asyncio.create_task(fake.serve())
        while not fake.started:
            if task.done():
                raise RuntimeError("fake_provider_start_failed")
            await asyncio.sleep(0.05)
        process = await asyncio.create_subprocess_exec(
            str(ROOT / ".venv-litellm/Scripts/python.exe"),
            "-I",
            "-S",
            "scripts/start_playground.py",
            "--port",
            "18791",
            "--models-dir",
            str(directory),
            "--offline-models",
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            await process.communicate()
        finally:
            if process.returncode is None:
                await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            fake.should_exit = True
            await task

    asyncio.run(run())


if __name__ == "__main__":
    main()
