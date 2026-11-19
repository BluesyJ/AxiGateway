"""Generate synthetic P3 data at one exclusive demo destination, with no network."""

import asyncio
import json
import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from axi_gateway.events import Cost, Projector, usage_projection
from axi_gateway.secrets import private_event_directory
from axi_gateway.storage import SQLiteSink
from p3_console.server import DEMO, ROOT
from tests.fake_upstream import FakeUpstream


async def main() -> None:
    """Collect in-process fake responses and attach explicit demonstration metadata."""
    if DEMO.parent.resolve() != ROOT / "data/demo":
        raise SystemExit("demo directory must not redirect")
    if DEMO.exists() or DEMO.is_symlink():
        raise SystemExit("Demo already exists; existing database preserved.")
    private_event_directory(DEMO.parent)
    fake = FakeUpstream()
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    events = []
    sources = ("reported", "unknown", "derived", "estimated", "missing")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app),
        base_url="http://demo.invalid",
        trust_env=False,
    ) as client:
        for hour in range(24):
            for slot in range(2 + (hour * 7) % 9):
                n = len(events)
                source = sources[n % len(sources)]
                failed = source == "missing" and n % 2 == 0
                alias = ("chat-standard", "chat-reasoner", "chat-fast")[n % 3]
                prompt, completion = 80 + (n * 31) % 810, 18 + (n * 13) % 248
                raw = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                }
                fake.body = json.dumps({"usage": raw}).encode()
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": alias,
                        "test_mode": "error_500"
                        if failed
                        else "missing_usage"
                        if source == "missing"
                        else "normal",
                    },
                )
                stamp = end - timedelta(hours=23 - hour, minutes=10 - slot)
                kwargs = {
                    "litellm_call_id": str(uuid5(NAMESPACE_URL, f"axi-p3-demo-{n}")),
                    "stream": source == "estimated",
                    "model": alias,
                    "litellm_params": {
                        "metadata": {
                            "client_label": ("lab", "team-a", "team-b")[n % 3],
                            "client_disconnected": source == "estimated",
                        },
                        "proxy_server_request": {
                            "headers": {
                                "x-axi-client": ("cli", "workbench", "batch", "cli")[
                                    n % 4
                                ]
                            }
                        },
                    },
                }
                event = Projector(alias, set(), "", ()).project(
                    kwargs,
                    response.json(),
                    stamp,
                    stamp + timedelta(milliseconds=120 + (n * 37) % 780),
                    not failed,
                )
                # Fixed synthetic estimates for the demo only.
                demo_cost = (
                    Cost(0.00014, "USD", "litellm_builtin", "demo-v1"),
                    Cost(0.00029, "USD", "user_prices", "demo-v2"),
                    event.cost,
                )[n % 3]
                events.append(
                    replace(
                        event,
                        usage=usage_projection(response.json().get("usage"), source),
                        cost=event.cost if failed else demo_cost,
                    )
                )
    counts = dict(
        accepted=len(events) + 2,
        written=len(events),
        auth_rejected=3,
        queue_full=4,
        sink_failed=2,
        projection_failed=0,
        rejected_after_close=0,
        storage_failed=1,
        debug_failed=0,
    )
    gaps = [
        dict(
            run_id="demo-run",
            reason=reason,
            count=number,
            first_at=(end - timedelta(hours=2)).isoformat(),
            last_at=end.isoformat(),
        )
        for reason, number in (("queue_full", 4), ("sink_failed", 2))
    ]
    with tempfile.TemporaryDirectory(
        prefix="demo-build-", dir=DEMO.parent
    ) as directory:
        temporary = Path(directory) / "demo-events.sqlite3"
        sink = SQLiteSink(temporary)
        try:
            sink.write_batch(events, counts, gaps)
        finally:
            sink.close()
        # Publish only after close/checkpoint; hard links fail if destination exists.
        os.link(temporary, DEMO)
    print(f"data/demo/demo-events.sqlite3: {len(events)} synthetic events")


if __name__ == "__main__":
    asyncio.run(main())
