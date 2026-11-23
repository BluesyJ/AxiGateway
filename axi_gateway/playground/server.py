"""Same-origin demo UI; credentials never cross the browser boundary."""

import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from axi_gateway.access.policy import PolicyError
from axi_gateway.access.server import create_app as management_app
from axi_gateway.playground.runtime import APPS, MODELS, REPLY, DemoSession
from axi_gateway.queries import read_connection
from p3_console.server import CSP


class DemoGate:
    """Allow local assets and same-origin API access to synthetic data only."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app, self.host = app, f"127.0.0.1:{port}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        def values(name: bytes) -> list[str]:
            return [
                v.decode("latin1") for k, v in scope["headers"] if k.lower() == name
            ]

        async def secured(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"content-security-policy", CSP.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                ]
            await send(message)

        origins = values(b"origin")
        status = 0
        if values(b"host") != [self.host] or (
            origins and origins != ["http://" + self.host]
        ):
            status = 403
        elif scope["method"] not in {"GET", "POST", "PUT"}:
            status = 405
        elif scope["path"].startswith("/api/") and values(b"x-playground") != ["1"]:
            status = 403
        elif scope["method"] != "GET" and origins != ["http://" + self.host]:
            status = 403
        if status:
            await JSONResponse({"error": "demo_request_denied"}, status)(
                scope, receive, secured
            )
        else:
            await self.app(scope, receive, secured)


async def body(request: Request) -> dict:
    """Bound the browser's JSON input before it reaches management or inference."""
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise PolicyError(400, "json_required")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 32768:
            raise PolicyError(413, "request_too_large")
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError):
        raise PolicyError(400, "invalid_request") from None


def create_app(
    session: DemoSession, port: int, dist: Path, proxy: httpx.AsyncClient
) -> ASGIApp:
    """Mount the small demo bridge, existing governance API and compiled UI."""
    manager = management_app(
        session.store, session.config.admin_verifier, session.config.admins, port
    )

    async def manage(
        method: str, path: str, data: dict | None = None
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager),
            base_url=f"http://127.0.0.1:{port}",
            headers={
                "Authorization": "Bearer " + session.signed("demo-admin", "demo-admin")
            },
        ) as client:
            return await client.request(method, path, json=data)

    async def state(request: Request) -> JSONResponse:
        apps = await manage("GET", "/api/apps")
        if apps.status_code != 200:
            return JSONResponse(apps.json(), apps.status_code)
        audit = await manage("GET", "/api/audit?limit=50")
        if audit.status_code != 200:
            return JSONResponse(audit.json(), audit.status_code)
        return JSONResponse(
            apps.json() | audit.json() | {"upstream_calls": session.upstream_calls}
        )

    async def update(request: Request) -> JSONResponse:
        app = request.path_params["app"]
        if app not in APPS:
            raise PolicyError(400, "invalid_demo_application")
        response = await manage("PUT", "/api/apps/" + app, await body(request))
        return JSONResponse(response.json(), response.status_code)

    async def call(request: Request) -> JSONResponse:
        data = await body(request)
        if (
            set(data) != {"app", "model", "prompt"}
            or data.get("app") not in APPS
            or data.get("model") not in MODELS
            or not isinstance(data.get("prompt"), str)
            or not 1 <= len(data["prompt"].strip()) <= 4096
        ):
            raise PolicyError(400, "invalid_demo_request")
        started = time.monotonic()
        try:
            response = await proxy.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": "Bearer "
                    + session.signed(data["app"], "demo-models")
                },
                json=dict(
                    model=data["model"],
                    messages=[dict(role="user", content=data["prompt"])],
                    max_tokens=64,
                    stream=False,
                ),
            )
        except httpx.HTTPError:
            raise PolicyError(503, "proxy_unavailable") from None
        result = dict(
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000),
            app=data["app"],
            model=data["model"],
        )
        if response.status_code != 200:
            codes = {
                401: "identity_rejected",
                403: "access_denied",
                429: "rpm_exceeded",
            }
            return JSONResponse(
                result
                | {"error": codes.get(response.status_code, "model_request_failed")},
                response.status_code,
            )
        try:
            reply = response.json()["choices"][0]["message"]["content"]
            if reply != REPLY:
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise PolicyError(502, "unexpected_demo_response") from None
        return JSONResponse(result | {"reply": reply, "demo": True})

    def event_rows() -> list[dict]:
        with read_connection(session.events) as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT litellm_call_id,started_at,key_alias,model_alias,"
                    "provider_outcome,total_tokens,usage_source,duration_ms "
                    "FROM events ORDER BY rowid DESC LIMIT 50"
                )
            ]

    async def events(request: Request) -> JSONResponse:
        try:
            return JSONResponse({"events": await asyncio.to_thread(event_rows)})
        except (sqlite3.Error, ValueError, OSError):
            raise PolicyError(503, "event_store_unavailable") from None

    async def error(request: Request, exc: PolicyError) -> JSONResponse:
        return JSONResponse({"error": exc.code}, exc.status)

    def index(request: Request) -> Response:
        path = dist / "playground.html"
        return (
            FileResponse(path)
            if path.is_file()
            else JSONResponse({"error": "frontend_build_required"}, 503)
        )

    def asset(request: Request) -> Response:
        name = request.path_params["name"]
        path = dist / "assets" / name
        if not re.fullmatch(r"[\w-]+\.(js|css|svg)", name) or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path)

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/assets/{name}", asset),
            Route("/api/demo/state", state),
            Route("/api/demo/events", events),
            Route("/api/demo/apps/{app}", update, methods=["PUT"]),
            Route("/api/demo/call", call, methods=["POST"]),
        ],
        exception_handlers={PolicyError: error},
    )
    app.router.redirect_slashes = False
    return DemoGate(app, port)
