"""Experiment-only HTTPX transport: durable four-attempt cap and numeric usage tap."""

import contextvars
import json
import time
import zlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx

NETWORK_ALLOWED = contextvars.ContextVar("p01_network_allowed", default=False)
MAX_CALLS = 4


class Budget:
    """Reserve one immutable slot before sending; failed calls consume their slot."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def claim(self, case: int) -> None:
        """Reject duplicate cases and anything outside the four authorized slots."""
        if case not in range(1, MAX_CALLS + 1):
            raise RuntimeError("P01 budget exceeded")
        with (self.directory / f"attempt-{case}.json").open("x", encoding="utf-8") as f:
            json.dump({"case": case, "reserved_at": time.time()}, f)


def numeric_usage(value: object) -> dict[str, int | float | None] | None:
    """Allowlist numeric usage, keeping missing fields null."""
    if not isinstance(value, dict):
        return None
    result = {}
    for name in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    ):
        item = value.get(name)
        result[name] = (
            item
            if isinstance(item, (int, float)) and not isinstance(item, bool)
            else None
        )
    for group, name in (
        ("prompt_tokens_details", "cached_tokens"),
        ("completion_tokens_details", "reasoning_tokens"),
    ):
        item = (value.get(group) or {}).get(name)
        result[name] = (
            item
            if isinstance(item, (int, float)) and not isinstance(item, bool)
            else None
        )
    return result


class UsageTap(httpx.AsyncByteStream):
    """Inspect ephemeral bytes, return each original byte chunk, save only usage."""

    def __init__(
        self,
        inner: httpx.AsyncByteStream,
        stream: bool,
        evidence: dict[str, Any],
        emit: Callable[[dict[str, Any]], None],
        content_encoding: str = "identity",
    ) -> None:
        self.inner, self.streaming = inner, stream
        self.evidence, self.emit = evidence, emit
        self.buffer = b""
        self.closed = False
        self.encoding = content_encoding.lower()
        self.evidence["content_encoding"] = (
            self.encoding
            if self.encoding in {"identity", "gzip", "deflate"}
            else "unsupported"
        )
        self.decoder = (
            zlib.decompressobj(16 + zlib.MAX_WBITS)
            if self.encoding == "gzip"
            else zlib.decompressobj()
            if self.encoding == "deflate"
            else None
        )

    def feed(self, chunk: bytes) -> None:
        """Decode only the ephemeral observation copy, never alter forwarded bytes."""
        if self.evidence["content_encoding"] == "unsupported":
            return
        decoded = self.decoder.decompress(chunk, 65537) if self.decoder else chunk
        self.buffer += decoded
        if self.streaming:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if line.startswith(b"data:"):
                    self.parse(line[5:].strip())
        if len(self.buffer) > 65536:
            self.buffer = b""
            self.evidence["buffer_overflow"] = True

    def parse(self, raw: bytes) -> None:
        try:
            if raw.strip() == b"[DONE]":
                self.evidence["upstream_done"] = True
                return
            value = json.loads(raw)
            usage = numeric_usage(value.get("usage"))
            if usage is not None:
                self.evidence["raw_usage"] = usage
                self.evidence["raw_usage_seen"] = True
        except (ValueError, TypeError, AttributeError):
            self.evidence["parse_errors"] += 1

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.inner:
                self.evidence["raw_bytes"] += len(chunk)
                try:
                    self.feed(chunk)
                except Exception:
                    self.evidence["parse_errors"] += 1
                yield chunk
            self.evidence["upstream_exhausted"] = True
            if not self.streaming:
                self.parse(self.buffer)
        finally:
            self.buffer = b""
            self.publish()

    def publish(self) -> None:
        if not self.closed:
            self.closed = True
            self.evidence["elapsed_ms"] = round(
                (time.perf_counter() - self.evidence.pop("start")) * 1000, 3
            )
            try:
                self.emit(dict(self.evidence))
            except Exception:
                # Observer failure must not affect a model response.
                pass

    async def aclose(self) -> None:
        try:
            await self.inner.aclose()
        finally:
            self.buffer = b""
            self.publish()


class BoundedTransport(httpx.AsyncBaseTransport):
    """No redirect, retry, other URL, other model or unbudgeted model request."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        budget: Budget,
        case: int,
        emit: Callable[[dict[str, Any]], None],
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint, self.model = endpoint, model
        self.budget, self.case, self.emit = budget, case, emit
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        if (
            str(request.url) != self.endpoint
            or request.method != "POST"
            or data.get("model") != self.model
            or not isinstance(data.get("max_tokens"), int)
            or not 1 <= data["max_tokens"] <= 64
        ):
            raise RuntimeError("P01 request outside authorization")
        self.budget.claim(self.case)
        evidence = {
            "hook": "raw_usage",
            "case_id": f"p0-real-{self.case}",
            "attempt": self.case,
            "raw_usage": None,
            "raw_usage_seen": False,
            "upstream_done": False,
            "upstream_exhausted": False,
            "raw_bytes": 0,
            "parse_errors": 0,
            "buffer_overflow": False,
            "start": time.perf_counter(),
            "max_tokens": data["max_tokens"],
        }
        token = NETWORK_ALLOWED.set(True)
        try:
            response = await self.inner.handle_async_request(request)
            evidence["status_code"] = response.status_code
        except Exception as exc:
            evidence["exception_type"] = type(exc).__name__
            evidence["elapsed_ms"] = round(
                (time.perf_counter() - evidence.pop("start")) * 1000, 3
            )
            self.emit(evidence)
            raise
        finally:
            NETWORK_ALLOWED.reset(token)
        response.stream = UsageTap(
            response.stream,
            data.get("stream") is True,
            evidence,
            self.emit,
            response.headers.get("content-encoding", "identity"),
        )
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()
