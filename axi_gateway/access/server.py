"""Loopback-only management API, independent of the read-only event console."""

import asyncio
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from axi_gateway.access.identity import IdentityError, Verifier
from axi_gateway.access.policy import PolicyError, PolicyStore

CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


class ManagementGate:
    """Check local authority and administrator identity before any route or body."""

    def __init__(
        self, app: ASGIApp, verifier: Verifier, admins: frozenset[str], port: int
    ) -> None:
        self.app, self.verifier, self.admins = app, verifier, admins
        self.host = f"127.0.0.1:{port}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return

        async def secured(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"content-security-policy", CSP.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(message)

        def values(name: bytes) -> list[str]:
            return [
                v.decode("latin1") for k, v in scope["headers"] if k.lower() == name
            ]

        try:
            origins = values(b"origin")
            if values(b"host") != [self.host] or (
                origins and origins != ["http://" + self.host]
            ):
                raise PolicyError(403, "origin_or_host_denied")
            if scope["method"] not in {"GET", "PUT"}:
                raise PolicyError(405, "method_denied")
            auth = values(b"authorization")
            if len(auth) != 1 or not auth[0].startswith("Bearer "):
                raise IdentityError("identity_rejected")
            subject = self.verifier.subject(auth[0][7:])
            if subject not in self.admins:
                raise PolicyError(403, "administrator_required")
            scope.setdefault("state", {})["admin_subject"] = subject
        except IdentityError:
            await JSONResponse({"error": "identity_rejected"}, 401)(
                scope, receive, secured
            )
            return
        except PolicyError as error:
            await JSONResponse({"error": error.code}, error.status)(
                scope, receive, secured
            )
            return
        await self.app(scope, receive, secured)


def create_app(
    store: PolicyStore, verifier: Verifier, admins: frozenset[str], port: int
) -> ASGIApp:
    """Build the bounded API; the launcher always binds to IPv4 loopback."""
    if not 1 <= port <= 65535 or not admins:
        raise ValueError("invalid_management_configuration")

    async def apps(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "apps": await asyncio.to_thread(store.apps),
                "models": store.models,
                "demo": True,
            }
        )

    async def audit(request: Request) -> JSONResponse:
        try:
            limit = int(request.query_params.get("limit", "100"))
        except ValueError:
            raise PolicyError(400, "invalid_limit") from None
        return JSONResponse({"audit": await asyncio.to_thread(store.audit, limit)})

    async def update(request: Request) -> JSONResponse:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise PolicyError(400, "json_required")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise PolicyError(413, "request_too_large")
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) != {
                "models",
                "rpm",
                "enabled",
                "expected_revision",
            }:
                raise ValueError
        except (ValueError, UnicodeError):
            raise PolicyError(400, "invalid_policy") from None
        result = await asyncio.to_thread(
            store.put, request.state.admin_subject, request.path_params["app"], **data
        )
        return JSONResponse(result)

    async def policy_error(request: Request, error: PolicyError) -> JSONResponse:
        return JSONResponse({"error": error.code}, error.status)

    app = Starlette(
        routes=[
            Route("/api/apps", apps, methods=["GET"]),
            Route("/api/apps/{app}", update, methods=["PUT"]),
            Route("/api/audit", audit, methods=["GET"]),
        ],
        exception_handlers={PolicyError: policy_error},
    )
    return ManagementGate(app, verifier, admins, port)
