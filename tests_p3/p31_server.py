"""Serve only this run's synthetic sources; no proxy or model process is started."""

import json
import os
import sys
from pathlib import Path

import uvicorn
from starlette.responses import Response

from axi_gateway.launcher import loopback_guard
from p3_console.server import HOST, ROOT, create_app
from tests_p3.p31_fixture import build

PORT = 18789


def main() -> None:
    """Select fixtures out of band; the production GET service stays unchanged."""
    directory = Path(os.environ["P31_RUN_DIR"]).resolve()
    if not directory.is_relative_to(ROOT / ".cache/p31"):
        raise ValueError("private_fixture_directory_required")
    sys.addaudithook(loopback_guard)
    build(directory)
    paths = {
        "normal": directory / "p31-demo.sqlite3",
        "empty": directory / "p31-demo-empty.sqlite3",
        "error": directory / "p31-demo-missing.sqlite3",
    }
    apps = {
        mode: create_app(PORT, demo=p, live=paths["error"], dist=directory / "dist")
        for mode, p in paths.items()
    }
    allowed = {p.as_uri() + "?mode=ro" for p in paths.values()}
    counts = dict(
        readonly_connections=0,
        forbidden_database_connections=0,
        live_source_requests=0,
        model_requests=0,
    )

    def audit(event: str, args: tuple) -> None:
        if event == "sqlite3.connect":
            if args[0] not in allowed:
                counts["forbidden_database_connections"] += 1
                raise PermissionError("synthetic_readonly_connections_only")
            counts["readonly_connections"] += 1

    sys.addaudithook(audit)

    async def app(scope: dict, receive: object, send: object) -> None:
        if scope["type"] == "http":
            path = scope["path"]
            if path.startswith("/api/live/") or path.startswith("/v1/"):
                counts[
                    "live_source_requests"
                    if path.startswith("/api/live/")
                    else "model_requests"
                ] += 1
                await Response(status_code=403)(scope, receive, send)
                return
        mode = (directory / "mode.txt").read_text(encoding="utf8")
        await apps[mode](scope, receive, send)
        (directory / "isolation.json").write_text(json.dumps(counts), encoding="utf8")

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
