"""Start the local Playground with synthetic identities, data and model replies."""

import argparse
import asyncio
import json
import logging
import os
import site
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from axi_gateway.bootstrap import verify_environment  # noqa: E402
from axi_gateway.launcher import loopback_guard  # noqa: E402


def main() -> int:
    """Validate the pinned environment and serve only on IPv4 loopback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--models-dir", type=Path, default=ROOT / "data/playground")
    parser.add_argument(
        "--offline-models",
        action="store_true",
        help="Test profile: model subprocesses may connect only to loopback",
    )
    args = parser.parse_args()
    model_directory = args.models_dir.resolve()
    if not any(
        model_directory.is_relative_to(ROOT / name) for name in ("data", ".cache")
    ):
        print(json.dumps({"error": "invalid_models_directory"}))
        return 1
    if not sys.flags.isolated or not sys.flags.no_site:
        print(json.dumps({"error": "use_python_isolated_no_site"}))
        return 1
    if not 1024 <= args.port <= 65535 or args.port in {4000, 8787, 8788, 8789}:
        print(json.dumps({"error": "choose_dedicated_demo_port"}))
        return 1
    if not (ROOT / "console_ui/dist/playground.html").is_file():
        print(json.dumps({"error": "frontend_build_required"}))
        return 1
    try:
        verify_environment(ROOT, ROOT / ".venv-litellm")
    except Exception:
        print(json.dumps({"error": "locked_environment_check_failed"}))
        return 1
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", args.port))
    except OSError:
        listener.close()
        print(json.dumps({"error": "demo_port_unavailable", "port": args.port}))
        return 1
    sys.addaudithook(loopback_guard)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    sys.prefix = sys.exec_prefix = str(ROOT / ".venv-litellm")
    site.addsitedir(str(ROOT / ".venv-litellm/Lib/site-packages"))
    import uvicorn

    from axi_gateway.playground.model_api import model_routes
    from axi_gateway.playground.model_runtime import ModelRuntime
    from axi_gateway.playground.models import ModelStore
    from axi_gateway.playground.runtime import runtime
    from axi_gateway.playground.server import create_app

    logging.getLogger("uvicorn").setLevel(logging.CRITICAL)

    async def run() -> None:
        async with runtime(ROOT) as (session, proxy):
            store = await asyncio.to_thread(ModelStore, model_directory)
            models = ModelRuntime(ROOT, store, args.offline_models)
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(
                        session,
                        args.port,
                        ROOT / "console_ui/dist",
                        proxy,
                        model_routes(store, models),
                    ),
                    host="127.0.0.1",
                    port=args.port,
                    access_log=False,
                    log_config=None,
                    proxy_headers=False,
                    server_header=False,
                )
            )
            print(
                json.dumps(
                    {
                        "event": "playground_ready",
                        "url": f"http://127.0.0.1:{args.port}/",
                        "modes": ["demo", "local_models"],
                    }
                ),
                flush=True,
            )
            try:
                await server.serve(sockets=[listener])
            finally:
                await models.close()

    try:
        asyncio.run(run())
        return 0
    except KeyboardInterrupt:
        return 0
    except TimeoutError:
        print(json.dumps({"error": "demo_start_timeout"}))
        return 1
    except Exception:
        print(json.dumps({"error": "demo_runtime_unavailable"}))
        return 1
    finally:
        listener.close()


if __name__ == "__main__":
    raise SystemExit(main())
