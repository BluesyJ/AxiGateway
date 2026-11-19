"""Exact public surface only; authentication and forwarding remain in LiteLLM."""

from starlette.types import ASGIApp, Receive, Scope, Send

HEALTH_PATHS = frozenset(
    {"/health/liveliness", "/health/liveness", "/health/readiness"}
)
ALLOWED = frozenset(
    {("POST", "/v1/chat/completions"), ("GET", "/v1/models")}
    | {(method, path) for path in HEALTH_PATHS for method in ("GET", "HEAD", "OPTIONS")}
)


class ExposureGate:
    """Pass permitted ASGI calls untouched; reject before any vendor handler."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] == "http":
            path = scope.get("path", "")
            allowed = (scope.get("method"), path) in ALLOWED
            if allowed:
                raw = path.encode("ascii")
                allowed = scope.get("raw_path", raw) == raw and not scope.get(
                    "root_path"
                )
            if not allowed:
                await send(
                    {"type": "http.response.start", "status": 404, "headers": []}
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)
