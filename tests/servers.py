"""Real loopback HTTP servers for streaming and disconnect integration tests."""

import asyncio
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from starlette.types import ASGIApp


class TestServer(uvicorn.Server):
    """Avoid changing process signal handlers in parallel test servers."""

    __test__ = False

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        """Leave signal ownership to the test runner."""
        yield


@asynccontextmanager
async def serve(app: ASGIApp) -> AsyncIterator[str]:
    """Run an ephemeral loopback server and shut it down after a test."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    port = sock.getsockname()[1]
    server = TestServer(
        uvicorn.Config(
            app,
            log_config=None,
            access_log=False,
            proxy_headers=False,
            lifespan="on",
            timeout_graceful_shutdown=1,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Test server exited before startup")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            sock.close()
