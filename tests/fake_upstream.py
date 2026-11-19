"""Local FastAPI upstream with deterministic response and disconnect scenarios."""

import asyncio
import gzip
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from starlette.responses import Response, StreamingResponse


class FakeUpstream:
    """Capture synthetic requests in memory; never write their contents to logs."""

    def __init__(self) -> None:
        self.app = FastAPI()
        self.requests: list[dict[str, object]] = []
        self.first_sent = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.started = asyncio.Event()
        self.mode = "normal"
        self.model_status = 200
        self.body = (
            b'{ "id":"fake", "usage":{"prompt_tokens":2,'
            b'"completion_tokens":3,"total_tokens":5} }'
        )
        self.sse = (
            ": keep-alive\r\n\r\n"
            'data: {"choices":[{"delta":{"content":"你好"}}]}\r\n\r\n'
            'data: {"choices":[],"usage":{"prompt_tokens":2,'
            '"completion_tokens":3,"total_tokens":5}}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        for prefix in ("", "/v1"):
            self.app.add_api_route(
                prefix + "/chat/completions", self.chat, methods=["POST"]
            )
            self.app.add_api_route(prefix + "/models", self.models, methods=["GET"])

    async def capture(self, request: Request) -> None:
        """Capture only synthetic data used by assertions in this test process."""
        self.requests.append(
            {
                "body": await request.body(),
                "headers": dict(request.headers),
                "path": request.url.path,
                "query": request.url.query,
            }
        )
        self.started.set()

    async def chat(self, request: Request) -> Response:
        """Serve normal, error, delayed, or fragmented SSE responses."""
        await self.capture(request)
        body = json.loads(self.requests[-1]["body"])
        mode = body.get("test_mode", self.mode)
        if mode == "before_headers":
            try:
                while not await request.is_disconnected():
                    await asyncio.sleep(0.01)
            finally:
                self.closed.set()
            return Response(b"disconnected")
        if mode == "timeout":
            await asyncio.sleep(0.4)
        if mode.startswith("error_"):
            return Response(
                b' {"error":{"message":"synthetic upstream error"}}\n',
                status_code=int(mode.split("_")[1]),
                headers={
                    "Content-Type": "application/json",
                    "Retry-After": "3",
                    "Set-Cookie": "synthetic=private",
                    "Authorization": "private",
                },
            )
        if mode == "redirect":
            return Response(
                b"do not follow",
                status_code=307,
                headers={"Location": "http://127.0.0.1:1/never"},
            )
        if mode == "gzip":
            return Response(
                gzip.compress(self.body), headers={"Content-Encoding": "gzip"}
            )
        if mode == "body_timeout":
            return StreamingResponse(self.delayed_body(), media_type="application/json")
        if body.get("stream"):
            return StreamingResponse(self.chunks(mode), media_type="text/event-stream")
        return Response(
            self.body if mode != "missing_usage" else b'{"choices":[]}',
            headers={"Content-Type": "application/json"},
        )

    async def chunks(self, mode: str) -> AsyncIterator[bytes]:
        """Fragment UTF-8/SSE freely and support deterministic first-byte gating."""
        try:
            if mode == "first_byte_timeout":
                await asyncio.sleep(0.4)
            if mode in ("gated", "after_first_timeout", "abort"):
                yield b": first\n\n"
                self.first_sent.set()
                await self.release.wait()
            if mode == "missing_usage":
                yield b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
            elif mode == "empty":
                return
            else:
                # Split across UTF-8 codepoints and SSE delimiters, including usage.
                for offset in range(0, len(self.sse), 7):
                    yield self.sse[offset : offset + 7]
                    await asyncio.sleep(0)
        finally:
            self.closed.set()

    async def delayed_body(self) -> AsyncIterator[bytes]:
        """Delay a non-SSE response after headers and initial bytes have arrived."""
        try:
            yield b'{"choices":'
            await asyncio.sleep(0.4)
            yield b"[]}"
        finally:
            self.closed.set()

    async def models(self, request: Request) -> Response:
        """Return an exact model-list body or an upstream error."""
        await self.capture(request)
        return Response(
            b'{ "object":"list", "data":[{"id":"fake-model"}] }',
            status_code=self.model_status,
            media_type="application/json",
        )
