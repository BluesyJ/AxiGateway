"""Independent, deterministic demo-only data and SQL oracle for P3.1."""

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from axi_gateway.events import Cost, Labels, Usage
from axi_gateway.storage import SQLiteSink
from tests_p2.samples import sample

NOW = datetime(2026, 11, 6, 8, tzinfo=UTC)


def build(directory: Path) -> dict:
    """Write new synthetic databases once; never reuse a product/demo destination."""
    directory.mkdir(parents=True, exist_ok=True)
    demo, empty = directory / "p31-demo.sqlite3", directory / "p31-demo-empty.sqlite3"
    if demo.exists() or empty.exists():
        raise ValueError("synthetic_database_already_exists")
    rows = []
    for n in range(144):
        source = ("reported", "derived", "estimated", "missing", "unknown")[n % 5]
        total = None if source == "missing" else 0 if n == 139 else 30 + n % 11
        prompt = total // 2 if total is not None else None
        completion = total - prompt if total is not None else None
        failed = source == "missing" and n % 2 == 0
        prices = (
            Cost(0.1, "USD", "litellm_builtin", "demo-v1"),
            Cost(0.2, "USD", "user_prices", "demo-v1"),
            Cost(0.05, "USD", "user_prices", "demo-v2"),
            Cost(None, None, None, None),
            Cost(0 if n == 139 else 0.3, "CNY", "user_prices", "demo-v1"),
        )
        stamp = NOW - timedelta(minutes=(144 - n) * 20)
        rows.append(
            replace(
                sample(),
                litellm_call_id=str(uuid5(NAMESPACE_URL, f"p31-demo-{n}")),
                started_at=stamp.isoformat(),
                ended_at=(stamp + timedelta(milliseconds=100 + n)).isoformat(),
                duration_ms=100 + n,
                model_alias=("chat-standard", "chat-reasoner", "chat-fast")[n % 3],
                client_label=Labels(
                    None if n % 11 == 0 else ("cli", "workbench")[n % 2],
                    None if n % 13 == 0 else ("team-a", "team-b", "lab")[n % 3],
                ),
                stream=source == "estimated",
                provider_outcome="error" if failed else "success",
                client_outcome="client_disconnected"
                if source == "estimated"
                else "unknown",
                usage=Usage(
                    prompt,
                    completion,
                    total,
                    source,
                    3 if source == "reported" else None,
                    prompt - 3 if source == "reported" else None,
                    "derived" if source == "reported" else None,
                    2 if source == "reported" else None,
                ),
                cost=prices[n % 5],
            )
        )
    counters = dict(
        accepted=144,
        written=144,
        auth_rejected=3,
        queue_full=4,
        sink_failed=2,
        projection_failed=0,
        rejected_after_close=0,
        storage_failed=1,
    )
    gaps = [
        dict(
            run_id="p31-demo",
            reason="queue_full",
            count=4,
            first_at=rows[-2].started_at,
            last_at=rows[-1].started_at,
        )
    ]
    for path, events, counts, windows in (
        (demo, rows, counters, gaps),
        (empty, [], {}, []),
    ):
        sink = SQLiteSink(path)
        sink.write_batch(events, counts, windows)
        sink.close()
    # Independent direct SQL, not the service's projection functions.
    with sqlite3.connect(demo) as db:
        db.row_factory = sqlite3.Row
        oracle = {
            "now": NOW.isoformat(),
            "counters": counters,
            "rows": [
                dict(r)
                for r in db.execute(
                    "SELECT litellm_call_id,started_at,model_alias,"
                    "client_header,client_metadata,"
                    "provider_outcome,client_outcome,usage_source,total_tokens,"
                    "cost_currency,cost_source,price_version,cost_amount FROM events "
                    "ORDER BY started_at DESC,litellm_call_id DESC"
                )
            ],
            "costs": [
                dict(r)
                for r in db.execute(
                    "SELECT cost_currency,cost_source,price_version,"
                    "SUM(cost_amount) AS amount,COUNT(cost_amount) AS known,"
                    "COUNT(*) AS requests FROM events GROUP BY 1,2,3"
                )
            ],
        }
    manifest = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (demo, empty)
    }
    (directory / "fixture.json").write_text(json.dumps(oracle), encoding="utf8")
    (directory / "database-sha256.json").write_text(
        json.dumps(manifest), encoding="utf8"
    )
    (directory / "mode.txt").write_text("normal", encoding="utf8")
    return oracle
