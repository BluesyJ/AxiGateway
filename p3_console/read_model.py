"""Dashboard projections over a single P2 read snapshot per response."""

import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from axi_gateway.collector import LOSS_REASONS
from axi_gateway.events import label
from axi_gateway.queries import disconnected_estimates, filters, read_connection

SOURCES = ("reported", "derived", "estimated", "missing", "unknown")
VIEWS = ("overview", "trend", "requests", "models", "clients", "health")
COUNTERS = ("accepted", "written", "auth_rejected", "storage_failed", *LOSS_REASONS)
TOKEN_SUM = (
    "COUNT(*) AS requests, COUNT(total_tokens) AS known, SUM(total_tokens) AS tokens"
)
EVENT_COLUMNS = (
    "litellm_call_id, started_at, duration_ms, model_alias, "
    "client_header, client_metadata,"
    " stream, provider_outcome, client_outcome, usage_source, total_tokens"
)


def timestamp(value: str | None) -> str | None:
    """Canonical UTC text matches the P2 writer's half-open time windows."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(UTC).isoformat()


def conditions(params: Mapping[str, str]) -> tuple[str, list]:
    """Bind approved P2 filters and the two independent outcome filters."""
    start, end = timestamp(params.get("start")), timestamp(params.get("end"))
    if start and end and start >= end:
        raise ValueError("invalid window")
    source = params.get("usage_source")
    if source is not None and source not in SOURCES:
        raise ValueError("invalid source")
    where, values = filters(
        start, end, params.get("model"), params.get("client"), source
    )
    for name, choices in (
        ("provider_outcome", ("success", "error", "unknown")),
        ("client_outcome", ("unknown", "client_disconnected")),
    ):
        if name in params:
            if params[name] not in choices:
                raise ValueError("invalid outcome")
            where += (" AND " if where else " WHERE ") + name + "=?"
            values.append(params[name])
    return where, values


def rows(db: sqlite3.Connection, sql: str, params: list | tuple = ()) -> list[dict]:
    """Materialize only the explicitly selected HTTP fields."""
    return [dict(row) for row in db.execute(sql, params)]


def health(db: sqlite3.Connection) -> dict:
    """Read lifetime counters and gap windows; live queue depth is not persisted."""
    stored = dict(db.execute("SELECT name,value FROM counters"))
    counts = {name: stored.get(name) for name in COUNTERS}
    totals = db.execute("SELECT COUNT(*),SUM(count) FROM gaps").fetchone()
    gaps = rows(
        db,
        "SELECT reason,count,first_at,last_at FROM gaps ORDER BY last_at DESC LIMIT 50",
    )
    losses = [counts[name] for name in LOSS_REASONS]
    return {
        "counters": counts,
        "queue_depth": None,
        "lost": sum(losses) if all(value is not None for value in losses) else None,
        "gap_windows": totals[0],
        "gap_events": totals[1] if totals[0] else 0,
        "gaps": gaps,
        "empty": not any(stored.values()) and not totals[0],
    }


def trend(db: sqlite3.Connection, where: str, values: list) -> list[dict]:
    """UTC hourly observations with separate totals and coverage for each source."""
    return rows(
        db,
        "SELECT strftime('%Y-%m-%dT%H:00:00Z',started_at) AS hour,"
        "usage_source,"
        + TOKEN_SUM
        + " FROM events"
        + where
        + " GROUP BY hour,usage_source ORDER BY hour,usage_source",
        values,
    )


def overview(db: sqlite3.Connection, where: str, values: list) -> dict:
    """Compute event counts and coverage separately from each estimate price group."""
    counts = rows(
        db,
        "SELECT COUNT(*) AS requests, "
        "COUNT(CASE WHEN provider_outcome='success' THEN 1 END) AS success,"
        "COUNT(CASE WHEN provider_outcome='error' THEN 1 END) AS errors FROM events"
        + where,
        values,
    )[0]
    usage = rows(
        db,
        "SELECT usage_source,"
        + TOKEN_SUM
        + " FROM events"
        + where
        + " GROUP BY usage_source",
        values,
    )
    by_source = {row["usage_source"]: row for row in usage}
    costs = rows(
        db,
        "SELECT cost_currency,cost_source,price_version,COUNT(*) AS requests,"
        "COUNT(cost_amount) AS known,SUM(cost_amount) AS amount FROM events"
        + where
        + " GROUP BY cost_currency,cost_source,price_version "
        "ORDER BY cost_source,price_version",
        values,
    )
    for row in costs:
        version = row["price_version"]
        if version is not None and not re.fullmatch(
            r"(?:litellm-[\d.]+:sha256:[\da-f]{64}|demo-v[\d.]+|\d{4}-\d{2}-\d{2})",
            version,
        ):
            row["price_version"] = None
        if row["cost_currency"] is not None and not re.fullmatch(
            r"[A-Z]{3}", row["cost_currency"]
        ):
            row["cost_currency"] = None
    status = health(db)
    return {
        **counts,
        "empty": counts["requests"] == 0,
        "disconnected_estimated": disconnected_estimates(db, where, values),
        "usage": [
            by_source.get(
                s, {"usage_source": s, "requests": 0, "known": 0, "tokens": None}
            )
            for s in SOURCES
        ],
        "costs": costs,
        "trend": trend(db, where, values),
        "lost": status["lost"],
        "auth_rejected": status["counters"]["auth_rejected"],
        "gap_windows": status["gap_windows"],
    }


def request_page(
    db: sqlite3.Connection, where: str, values: list, params: Mapping[str, str]
) -> dict:
    """Return a bounded newest-first list from the same snapshot as its count."""
    page, size = int(params.get("page", "1")), int(params.get("size", "15"))
    if not 1 <= page <= 10**8 or not 1 <= size <= 100:
        raise ValueError("invalid page")
    total = db.execute("SELECT COUNT(*) FROM events" + where, values).fetchone()[0]
    page = min(page, max(1, (total + size - 1) // size))
    items = rows(
        db,
        "SELECT "
        + EVENT_COLUMNS
        + " FROM events"
        + where
        + " ORDER BY started_at DESC,litellm_call_id DESC LIMIT ? OFFSET ?",
        [*values, size, (page - 1) * size],
    )
    for item in items:
        try:
            item["litellm_call_id"] = str(UUID(item["litellm_call_id"]))
        except (ValueError, TypeError):
            item["litellm_call_id"] = None
        for name in ("client_header", "client_metadata", "model_alias"):
            item[name] = label(item[name], ())
    return {
        "empty": not items,
        "items": items,
        "page": page,
        "size": size,
        "total": total,
    }


def dashboard(path: Path, view: str, params: Mapping[str, str]) -> dict:
    """Serve one explicit projection with no caller-controlled SQL or paths."""
    valid = {"start", "end"}
    if view == "requests":
        valid.update(
            (
                "model",
                "client",
                "provider_outcome",
                "client_outcome",
                "usage_source",
                "page",
                "size",
            )
        )
    if view == "health":
        valid.clear()
    if (
        view not in VIEWS
        or set(params) - valid
        or any(len(v) > 128 for v in params.values())
    ):
        raise ValueError("invalid query")
    where, values = conditions(params)
    with read_connection(path) as db:
        db.execute("BEGIN")
        if view == "overview":
            result = overview(db, where, values)
        elif view == "health":
            result = health(db)
        elif view == "requests":
            result = request_page(db, where, values, params)
        elif view == "trend":
            items = trend(db, where, values)
            result = {"empty": not items, "items": items}
        else:
            # Group the two self-reported labels as an explicit pair.
            group = (
                "model_alias" if view == "models" else "client_header,client_metadata"
            )
            items = rows(
                db,
                "SELECT "
                + group
                + ",usage_source,"
                + TOKEN_SUM
                + " FROM events"
                + where
                + " GROUP BY "
                + group
                + ",usage_source ORDER BY "
                + group
                + ",usage_source",
                values,
            )
            for item in items:
                for name in group.split(","):
                    item[name] = label(item[name], ())
            result = {"empty": not items, "items": items}
    return {"view": view, **result}
