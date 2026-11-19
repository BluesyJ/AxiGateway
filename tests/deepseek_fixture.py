"""Synthetic DeepSeek fixture extracted from the historical P0 probe."""

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from tests.fake_upstream import FakeUpstream

USAGE = {
    "prompt_tokens": 20,
    "completion_tokens": 12,
    "total_tokens": 32,
    "prompt_cache_hit_tokens": 8,
    "prompt_cache_miss_tokens": 12,
    "completion_tokens_details": {"reasoning_tokens": 6},
}

PROMPT = "P0_PRIVATE_PROMPT_" + secrets.token_hex(10)

ANSWER = "P0_PRIVATE_RESPONSE_" + secrets.token_hex(10)

SECRET = "sk-p0-synthetic-" + secrets.token_hex(20)


class DeepSeekFixture(FakeUpstream):
    """Reuse legacy fake-server lifecycle with DeepSeek-compatible synthetic data."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[dict[str, Any]] = []
        self.closed_at: float | None = None
        self.stream_completed = False

    async def chat(self, request: Request) -> Response:
        """Return fixed usage, fault responses, or a cancellable stream."""
        body = await request.json()
        mode = self.mode
        self.started.set()
        self.seen.append(
            {
                "model": body.get("model"),
                "path": request.url.path,
                "stream": body.get("stream") is True,
                "include_usage": (body.get("stream_options") or {}).get(
                    "include_usage"
                ),
                "upstream_credential_correct": request.headers.get("authorization")
                == "Bearer " + SECRET,
                "client_metadata_forwarded": "metadata" in body,
                "client_header_forwarded": "x-axi-client" in request.headers,
            }
        )
        if mode in {"error429", "error500"}:
            return JSONResponse(
                {"error": {"message": "synthetic error", "type": "server_error"}},
                status_code=int(mode[-3:]),
            )
        if mode == "abort-before":
            try:
                await asyncio.sleep(2)
            finally:
                self.closed.set()
                self.closed_at = time.perf_counter()
        if body.get("stream"):
            return StreamingResponse(
                self.stream(mode, body["model"]), media_type="text/event-stream"
            )
        response = {
            "id": "p0-upstream-response",
            "object": "chat.completion",
            "created": 1800000000,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": ANSWER,
                        "reasoning_content": "synthetic reasoning",
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        if mode != "missing":
            response["usage"] = USAGE
        return JSONResponse(response)

    async def stream(self, mode: str, model: str) -> AsyncIterator[bytes]:
        """Delay a long stream so the client can close its real TCP connection."""
        base = {
            "id": "p0-upstream-stream",
            "object": "chat.completion.chunk",
            "created": 1800000000,
            "model": model,
        }
        try:
            first = {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ANSWER},
                        "finish_reason": None,
                    }
                ],
            }
            yield ("data: " + json.dumps(first) + "\n\n").encode()
            self.first_sent.set()
            await asyncio.sleep(1.5 if mode == "abort-mid" else 0.01)
            last = {
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield ("data: " + json.dumps(last) + "\n\n").encode()
            if mode != "missing":
                yield (
                    "data: "
                    + json.dumps({**base, "choices": [], "usage": USAGE})
                    + "\n\n"
                ).encode()
            yield b"data: [DONE]\n\n"
            self.stream_completed = True
        finally:
            self.closed.set()
            self.closed_at = time.perf_counter()
