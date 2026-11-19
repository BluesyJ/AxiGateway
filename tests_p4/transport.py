"""Experiment-only bounded upstream observation; never installed by product startup."""

import json
import os
import time
from pathlib import Path

import httpx

from spikes.litellm_p01.bounded_transport import NETWORK_ALLOWED, UsageTap

MODELS = ("deepseek-v4-pro", "deepseek-flash")
MAX_CALLS = 12


class Budget:
    """Reserve atomic persistent slots before network I/O, including failures."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def claim(self) -> int:
        """Never reuse a reserved slot, including after crash or script restart."""
        for number in range(1, MAX_CALLS + 1):
            try:
                with (self.directory / f"attempt-{number}").open("x") as file:
                    file.write("reserved")
                    file.flush()
                    os.fsync(file.fileno())
                return number
            except FileExistsError:
                continue
        raise RuntimeError("call_budget_exhausted")


class ClientTransport(httpx.AsyncHTTPTransport):
    """Charge each outgoing model POST, whether direct or sent to the proxy."""

    def __init__(
        self, endpoint: str, budget: Budget, direct: bool, output: Path
    ) -> None:
        super().__init__(retries=0, trust_env=False)
        self.endpoint = endpoint
        self.budget = budget
        self.direct = direct
        self.output = output

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Reserve before I/O. Only the direct baseline uses the original usage tap."""
        if str(request.url) != self.endpoint:
            raise RuntimeError("unexpected_model_endpoint")
        body = json.loads(request.content)
        limit = body.get("max_tokens")
        if (
            request.method != "POST"
            or body.get("model") not in MODELS
            or type(limit) is not int
            or not 1 <= limit <= 64
        ):
            raise RuntimeError("request_outside_budget")
        number = self.budget.claim()
        if not self.direct:
            return await super().handle_async_request(request)
        evidence = {
            "attempt": number,
            "model": body["model"],
            "max_tokens": limit,
            "raw_usage": None,
            "raw_usage_seen": False,
            "upstream_done": False,
            "upstream_exhausted": False,
            "raw_bytes": 0,
            "parse_errors": 0,
            "buffer_overflow": False,
            "start": time.perf_counter(),
        }

        def emit(value: dict) -> None:
            (self.output / f"usage-{number}.json").write_text(
                json.dumps(value), encoding="utf8"
            )

        token = NETWORK_ALLOWED.set(True)
        try:
            response = await super().handle_async_request(request)
        except Exception:
            evidence.pop("start")
            emit({**evidence, "transport_failed": True})
            raise
        finally:
            NETWORK_ALLOWED.reset(token)
        evidence["status_code"] = response.status_code
        response.stream = UsageTap(
            response.stream,
            body.get("stream") is True,
            evidence,
            emit,
            response.headers.get("content-encoding", "identity"),
        )
        return response
