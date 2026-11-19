"""Exact exposure, byte-transparent delegation, and cancellation contracts."""

import asyncio

import pytest

from axi_gateway.exposure import ALLOWED, ExposureGate


@pytest.mark.parametrize("method,path", sorted(ALLOWED))
def test_allowed_delegates_identical_asgi_objects(method: str, path: str) -> None:
    async def run() -> None:
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "headers": [(b"authorization", b"synthetic")],
        }
        request = {"type": "http.request", "body": b"synthetic", "more_body": False}
        messages = [
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-encoding", b"gzip"), (b"x-test", b"unchanged")],
            },
            {
                "type": "http.response.body",
                "body": b"\x1f\x8bchunk1",
                "more_body": True,
            },
            {"type": "http.response.body", "body": b"chunk2", "more_body": False},
        ]
        sent = []

        async def receive() -> dict:
            return request

        async def send(message: dict) -> None:
            sent.append(message)

        async def inner(got: dict, recv: object, emit: object) -> None:
            assert got is scope and recv is receive and emit is send
            assert await recv() is request
            for message in messages:
                await emit(message)
                assert sent[-1] is message  # Not buffered until the app returns.

        await ExposureGate(inner)(scope, receive, send)
        assert all(left is right for left, right in zip(sent, messages, strict=True))

    asyncio.run(run())


@pytest.mark.parametrize(
    "scope",
    [
        {"type": "websocket", "path": "/v1/chat/completions"},
        *[
            {"type": "http", "method": m, "path": p}
            for m, p in [
                ("GET", "/v1/chat/completions"),
                ("POST", "/v1/models"),
                ("POST", "/sso/cli/start"),
                ("GET", "/ui"),
                ("POST", "/V1/chat/completions"),
                ("POST", "/v1/chat/completions/"),
                ("POST", "//v1/chat/completions"),
                ("POST", "/v1//chat/completions"),
                ("POST", "/v1/%63hat/completions"),
                ("POST", "/chat/completions"),
            ]
        ],
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "raw_path": b"/%76%31/chat/completions",
        },
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "root_path": "/hidden",
        },
    ],
)
def test_denied_never_reads_body_or_calls_vendor(scope: dict) -> None:
    async def run() -> None:
        sent = []

        async def forbidden(*args: object) -> None:
            pytest.fail("denied scope reached receive/vendor")

        async def send(message: dict) -> None:
            sent.append(message)

        await ExposureGate(forbidden)(scope, forbidden, send)
        if scope["type"] == "websocket":
            assert sent == [{"type": "websocket.close", "code": 1008}]
        else:
            assert sent[0]["status"] == 404 and sent[0]["headers"] == []
            assert sent[1]["body"] == b""

    asyncio.run(run())


def test_cancellation_and_lifespan_are_not_intercepted() -> None:
    async def run() -> None:
        seen = []

        async def receive() -> dict:
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            seen.append(message)

        async def inner(scope: dict, recv: object, emit: object) -> None:
            assert recv is receive and emit is send
            if scope["type"] == "lifespan":
                seen.append(scope)
                return
            assert (await recv())["type"] == "http.disconnect"
            raise asyncio.CancelledError

        gate = ExposureGate(inner)
        lifecycle = {"type": "lifespan"}
        await gate(lifecycle, receive, send)
        assert seen == [lifecycle]
        with pytest.raises(asyncio.CancelledError):
            await gate(
                {"type": "http", "method": "POST", "path": "/v1/chat/completions"},
                receive,
                send,
            )

    asyncio.run(run())
