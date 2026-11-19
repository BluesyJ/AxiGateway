"""Serve the local P3 console. Only the operator chooses the two database paths."""

import argparse
import re
import sqlite3
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from p3_console.read_model import VIEWS, dashboard

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data/demo/demo-events.sqlite3"
LIVE = ROOT / "data/p1/events/events.sqlite3"
DIST = ROOT / "console_ui/dist"
HOST = "127.0.0.1"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


class LocalReadOnly:
    """Check the authority before routing, including failures and static assets."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope["headers"]
        hosts = [v.decode("latin1") for k, v in headers if k == b"host"]
        origins = [v.decode("latin1") for k, v in headers if k == b"origin"]

        async def secured(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message["headers"],
                    (b"content-security-policy", CSP.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                ]
            await send(message)

        status = 0
        if (
            len(hosts) != 1
            or hosts[0] not in self.hosts
            or (origins and origins != ["http://" + hosts[0]])
        ):
            status = 403
        elif scope["method"] != "GET":
            status = 405
        if status:
            await Response(
                status_code=status, headers={"Allow": "GET"} if status == 405 else {}
            )(scope, receive, secured)
        else:
            await self.app(scope, receive, secured)


def create_app(
    port: int = 8788, *, demo: Path = DEMO, live: Path = LIVE, dist: Path = DIST
) -> ASGIApp:
    """Mount only the two named read sources and the compiled application."""
    sources = {"demo": demo, "live": live}

    def api(request: Request) -> Response:
        source, view = request.path_params["source"], request.path_params["view"]
        if source not in sources or view not in VIEWS:
            return Response(status_code=404)
        params = request.query_params
        if len(params.multi_items()) != len(params):
            return JSONResponse({"error": "invalid_filter"}, status_code=400)
        try:
            result = dashboard(sources[source], view, params)
        except (sqlite3.Error, OSError):
            return JSONResponse({"error": "database_unavailable"}, status_code=503)
        except ValueError as exc:
            if str(exc) == "unsupported schema version":
                return JSONResponse({"error": "schema_unavailable"}, status_code=503)
            return JSONResponse({"error": "invalid_filter"}, status_code=400)
        return JSONResponse(result)

    def index(request: Request) -> Response:
        path = dist / "index.html"
        return (
            FileResponse(path)
            if path.is_file()
            else Response("Build console_ui first.", status_code=503)
        )

    def asset(request: Request) -> Response:
        name = request.path_params["name"]
        if not re.fullmatch(r"[\w-]+\.(?:js|css|svg)", name):
            return Response(status_code=404)
        file = (dist / "assets" / name).resolve()
        if not file.is_relative_to(dist.resolve() / "assets") or not file.is_file():
            return Response(status_code=404)
        return FileResponse(file)

    routes = [
        Route("/", index),
        Route("/dashboard", index),
        Route("/assets/{name}", asset),
        Route("/api/{source}/{view}", api),
    ]
    app = Starlette(routes=routes)
    app.router.redirect_slashes = False
    return LocalReadOnly(app, port)


def main() -> None:
    """Start one read-only service, bound exclusively to IPv4 loopback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port in {4000, 8787}:
        parser.error("choose a dedicated console port")
    print(f"P3 console http://{HOST}:{args.port}/", flush=True)
    uvicorn.run(
        create_app(args.port),
        host=HOST,
        port=args.port,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
