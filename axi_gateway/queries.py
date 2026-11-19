"""Local read-only queries; no HTTP API, price math or content access."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from axi_gateway.storage import SCHEMA_VERSION


def disconnected_estimates(
    db: sqlite3.Connection, where: str, values: tuple | list
) -> dict:
    """Return the disconnected subset of estimated usage in the current snapshot."""
    predicate = "client_outcome='client_disconnected' AND usage_source='estimated'"
    row = db.execute(
        "SELECT count(*) AS requests, count(total_tokens) AS known, "
        "sum(total_tokens) AS total_tokens FROM events"
        + where
        + (" AND " if where else " WHERE ")
        + predicate,
        values,
    ).fetchone()
    return dict(row, label="估算（不可靠，通常明显偏低）", subset_of="estimated")


@contextmanager
def read_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a short-lived WAL reader that cannot create or mutate the database."""
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] not in (
            1,
            SCHEMA_VERSION,
        ):
            raise ValueError("unsupported schema version")
        yield connection
    finally:
        connection.close()


def filters(
    start: str | None,
    end: str | None,
    model: str | None,
    client: str | None,
    usage_source: str | None,
) -> tuple[str, list]:
    """Produce bound filters; the client matches either untrusted label."""
    clauses, parameters = [], []
    for sql, value in (
        ("started_at>=?", start),
        ("started_at<?", end),
        ("model_alias=?", model),
        ("usage_source=?", usage_source),
    ):
        if value is not None:
            clauses.append(sql)
            parameters.append(value)
    if client is not None:
        clauses.append("(client_header=? OR client_metadata=?)")
        parameters.extend([client, client])
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters


def list_events(
    path: Path,
    *,
    limit: int = 100,
    offset: int = 0,
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    client: str | None = None,
    usage_source: str | None = None,
) -> list[dict]:
    """Page oldest to newest by insertion order; each page is its own snapshot."""
    if (
        type(limit) is not int
        or not 1 <= limit <= 1000
        or type(offset) is not int
        or offset < 0
    ):
        raise ValueError("invalid pagination")
    where, params = filters(start, end, model, client, usage_source)
    with read_connection(path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM events" + where + " ORDER BY rowid LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
        ]


def aggregate_events(
    path: Path,
    *,
    group_by: str = "time",
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    client: str | None = None,
    usage_source: str | None = None,
) -> list[dict]:
    """Aggregate hours/model/labels; sources and price bases stay separate."""
    groups = {
        "time": "substr(started_at,1,13)",
        "model": "model_alias",
        "client": "client_header,client_metadata",
        "usage_source": "usage_source",
    }
    if group_by not in groups:
        raise ValueError("invalid grouping")
    columns = groups[group_by]
    if group_by == "time":
        columns += " AS hour_utc"
    group_keys = (
        groups[group_by].split(",") if group_by != "time" else [groups[group_by]]
    )
    group = ",".join(
        dict.fromkeys(
            [
                *group_keys,
                "usage_source",
                "cost_currency",
                "cost_source",
                "price_version",
            ]
        )
    )
    where, params = filters(start, end, model, client, usage_source)
    with read_connection(path) as connection:
        rows = connection.execute(
            "SELECT "
            + columns
            + ",usage_source,cost_currency,cost_source,price_version,"
            "count(*) AS event_count,count(prompt_tokens) AS prompt_known_count,"
            "count(completion_tokens) AS completion_known_count,"
            "count(total_tokens) AS total_known_count,"
            "sum(prompt_tokens) AS prompt_tokens,"
            "sum(completion_tokens) AS completion_tokens,"
            "sum(total_tokens) AS total_tokens,"
            "sum(cache_hit_tokens) AS cache_hit_tokens,"
            "sum(cache_miss_tokens) AS cache_miss_tokens,"
            "sum(reasoning_tokens) AS reasoning_tokens,"
            "sum(cost_amount) AS cost_amount,count(cost_amount) AS cost_known_count,"
            "1 AS estimate_not_invoice FROM events" + where + " GROUP BY " + group,
            params,
        )
        return [dict(row) for row in rows]


def list_gaps(path: Path, *, limit: int = 100, offset: int = 0) -> list[dict]:
    """Page cumulative loss windows (one reason per collector run)."""
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("invalid pagination")
    with read_connection(path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM gaps ORDER BY first_at,run_id,reason LIMIT ? OFFSET ?",
                (limit, offset),
            )
        ]


def read_counters(path: Path) -> dict[str, int]:
    """Read persisted cumulative counters; uncommitted memory is not included."""
    with read_connection(path) as connection:
        return dict(connection.execute("SELECT name,value FROM counters"))
