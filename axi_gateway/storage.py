"""SQLite metadata sink. Connections and transactions belong to one writer thread."""

import sqlite3
from pathlib import Path

from axi_gateway.events import Event

SCHEMA_VERSION = 2
CACHE_V1 = """    CHECK((cache_hit_tokens IS NULL AND cache_miss_tokens IS NULL AND
cache_miss_source IS NULL)
        OR (prompt_tokens IS NOT NULL AND cache_hit_tokens IS NOT NULL AND
cache_hit_tokens>=0
        AND cache_hit_tokens<=prompt_tokens AND cache_miss_tokens IS NOT NULL
        AND cache_miss_tokens=prompt_tokens-cache_hit_tokens AND cache_miss_source
IS NOT NULL
        AND cache_miss_source='derived')),
"""
CACHE_V2 = """    CHECK(cache_hit_tokens IS NULL OR (prompt_tokens IS NOT NULL
        AND cache_hit_tokens BETWEEN 0 AND prompt_tokens)),
    CHECK((cache_miss_tokens IS NULL AND cache_miss_source IS NULL)
        OR (cache_miss_tokens IS NOT NULL AND cache_miss_source IS NOT NULL
        AND prompt_tokens IS NOT NULL AND cache_miss_tokens BETWEEN 0 AND prompt_tokens
        AND (cache_hit_tokens IS NULL
             OR cache_miss_tokens=prompt_tokens-cache_hit_tokens)
        AND (cache_miss_source='reported' OR
             (cache_miss_source='derived' AND cache_hit_tokens IS NOT NULL)))),
"""
EVENT_TABLE = """CREATE TABLE {table} (
    litellm_call_id TEXT PRIMARY KEY NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version=0),
    started_at TEXT, ended_at TEXT, duration_ms REAL CHECK(duration_ms>=0),
    client_header TEXT, client_metadata TEXT, key_alias TEXT, model_alias TEXT NOT NULL,
    stream INTEGER NOT NULL CHECK(stream IN (0,1)),
    provider_outcome TEXT NOT NULL CHECK(provider_outcome IN
('success','error','unknown')),
    client_outcome TEXT NOT NULL CHECK(client_outcome IN
('unknown','client_disconnected')),
    prompt_tokens INTEGER CHECK(prompt_tokens>=0),
    completion_tokens INTEGER CHECK(completion_tokens>=0),
    total_tokens INTEGER CHECK(total_tokens>=0),
    usage_source TEXT NOT NULL CHECK(usage_source IN
('reported','derived','estimated','missing','unknown')),
    cache_hit_tokens INTEGER, cache_miss_tokens INTEGER, cache_miss_source TEXT,
    reasoning_tokens INTEGER,
    cost_amount REAL CHECK(cost_amount>=0), cost_currency TEXT, cost_source TEXT,
    price_version TEXT, estimate_not_invoice INTEGER NOT NULL
CHECK(estimate_not_invoice=1),
    error_category TEXT CHECK(error_category IN
('upstream_http','provider_or_transport')),
    CHECK(usage_source='reported' OR (cache_hit_tokens IS NULL AND cache_miss_tokens
IS NULL
        AND cache_miss_source IS NULL AND reasoning_tokens IS NULL)),
{cache_constraint}    CHECK(reasoning_tokens IS NULL OR (completion_tokens IS NOT NULL
        AND reasoning_tokens>=0 AND reasoning_tokens<=completion_tokens)),
    CHECK((cost_amount IS NULL AND cost_currency IS NULL AND cost_source IS NULL AND
price_version IS NULL)
        OR (cost_amount IS NOT NULL AND cost_currency IS NOT NULL AND price_version
IS NOT NULL
        AND cost_source IS NOT NULL AND cost_source IN
('litellm_builtin','user_prices')))
);
"""
EVENT_INDEXES = """CREATE INDEX events_time ON events(started_at,litellm_call_id);
CREATE INDEX events_model ON events(model_alias,started_at);
"""
MIGRATIONS = {
    1: EVENT_TABLE.format(table="events", cache_constraint=CACHE_V1)
    + EVENT_INDEXES
    + """CREATE TABLE gaps (
    run_id TEXT NOT NULL, reason TEXT NOT NULL CHECK(reason IN
        ('queue_full','projection_failed','sink_failed','rejected_after_close')),
    count INTEGER NOT NULL CHECK(count>0), first_at TEXT NOT NULL, last_at TEXT NOT
NULL,
    PRIMARY KEY(run_id,reason)
);
CREATE TABLE counters (name TEXT PRIMARY KEY NOT NULL, value INTEGER NOT NULL
CHECK(value>=0));
""",
    2: EVENT_TABLE.format(table="events_v2", cache_constraint=CACHE_V2)
    + """INSERT INTO events_v2 SELECT * FROM events ORDER BY rowid;
DROP TABLE events;
ALTER TABLE events_v2 RENAME TO events;
"""
    + EVENT_INDEXES,
}


def event_values(event: Event) -> tuple:
    """Flatten only declared fields; SQLite constraints enforce D04/D05 again."""
    if not isinstance(event, Event):
        raise TypeError("Event required")
    u, c = event.usage, event.cost
    return (
        event.litellm_call_id,
        event.schema_version,
        event.started_at,
        event.ended_at,
        event.duration_ms,
        event.client_label.header,
        event.client_label.metadata,
        event.key_alias,
        event.model_alias,
        int(event.stream),
        event.provider_outcome,
        event.client_outcome,
        u.prompt_tokens,
        u.completion_tokens,
        u.total_tokens,
        u.usage_source,
        u.cache_hit_tokens,
        u.cache_miss_tokens,
        u.cache_miss_source,
        u.reasoning_tokens,
        c.amount,
        c.currency,
        c.source,
        c.price_version,
        int(c.estimate_not_invoice),
        event.error_category,
    )


class SQLiteSink:
    """Batch events, cumulative gap windows and counter deltas in one transaction."""

    kind = "sqlite"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def _open(self) -> sqlite3.Connection:
        if self.connection is not None:
            return self.connection
        connection = sqlite3.connect(self.path, timeout=0.05, isolation_level=None)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("unsupported schema version")
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise ValueError("WAL required")
            connection.execute("PRAGMA synchronous=FULL")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                # Only repository-owned SQL participates in migrations.
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + MIGRATIONS[target]
                    + f"\nPRAGMA user_version={target};\nCOMMIT;"
                )
        except Exception:
            connection.close()
            raise
        self.connection = connection
        return connection

    def write_batch(
        self, events: list[Event], counters: dict, gaps: list[dict]
    ) -> None:
        """First observation wins on a repeated call ID; any failure rolls back all."""
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO events VALUES ("
                + ",".join("?" for _ in range(26))
                + ") ON CONFLICT(litellm_call_id) DO NOTHING",
                [event_values(event) for event in events],
            )
            connection.executemany(
                "INSERT INTO counters VALUES (?,?) ON CONFLICT(name) DO UPDATE "
                "SET value=value+excluded.value",
                counters.items(),
            )
            connection.executemany(
                "INSERT INTO gaps VALUES (:run_id,:reason,:count,:first_at,:last_at) "
                "ON CONFLICT(run_id,reason) DO UPDATE SET count=excluded.count, "
                "last_at=excluded.last_at",
                gaps,
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    def close(self) -> None:
        """Close on the writer thread, after its bounded drain has completed."""
        if self.connection is not None:
            self.connection.close()
            self.connection = None
