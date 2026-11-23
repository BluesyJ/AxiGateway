"""Read-only cross-model views over P2 and local request context."""

import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from uuid import UUID

from axi_gateway.access.policy import PolicyError
from axi_gateway.playground.models import ModelStore
from axi_gateway.playground.monitor import CALL_SCHEMA, Monitor
from axi_gateway.storage import CACHE_V2, EVENT_TABLE
from p3_console.read_model import SOURCES, timestamp

ID = re.compile(r"local-[a-f0-9]{16}")
TOKENS = ("prompt_tokens", "completion_tokens", "total_tokens")
EVENT_FIELDS = (
    "litellm_call_id",
    "started_at",
    "duration_ms",
    "provider_outcome",
    "client_outcome",
    "usage_source",
    *TOKENS,
    "cache_hit_tokens",
    "cache_miss_tokens",
    "cache_miss_source",
    "reasoning_tokens",
    "error_category",
)


def merge_nullable(rows: list[dict], name: str) -> int | float | None:
    """Sum measured values without replacing absent measurements with zero."""
    values = [r[name] for r in rows if r[name] is not None]
    return sum(values) if values else None


class MonitorQueries:
    """Join each model's immutable history to request context by exact call UUID."""

    def __init__(self, store: ModelStore) -> None:
        self.store = store
        self.monitor = Monitor(store.directory)

    def catalog(self) -> dict[str, dict]:
        result = {m["id"]: m for m in self.store.list()}
        for row in self.store.audit():
            result.setdefault(
                row["target"],
                dict(id=row["target"], name=row["name"], model=row["model"]),
            )
        for path in (self.store.directory / "proxies").glob("local-*"):
            if ID.fullmatch(path.name):
                result.setdefault(
                    path.name, dict(id=path.name, name=path.name, model=None)
                )
        return result

    @contextmanager
    def snapshot(self, model: dict) -> Iterator[sqlite3.Connection]:
        """Attach only known local files in read-only mode, with an empty fallback."""
        db = sqlite3.connect(":memory:", uri=True)
        db.row_factory = sqlite3.Row
        try:
            if self.monitor.path.exists():
                db.execute(
                    "ATTACH DATABASE ? AS history",
                    (self.monitor.path.resolve().as_uri() + "?mode=ro",),
                )
                db.execute("CREATE TEMP VIEW calls AS SELECT * FROM history.calls")
            else:
                db.execute(CALL_SCHEMA)
            path = (
                self.store.directory / "proxies" / model["id"] / "events/events.sqlite3"
            )
            if path.exists():
                db.execute(
                    "ATTACH DATABASE ? AS p2", (path.resolve().as_uri() + "?mode=ro",)
                )
                if db.execute("PRAGMA p2.user_version").fetchone()[0] not in (1, 2):
                    raise ValueError("unsupported_event_schema")
                db.execute("CREATE TEMP VIEW events AS SELECT * FROM p2.events")
            else:
                db.execute(
                    EVENT_TABLE.format(table="events", cache_constraint=CACHE_V2)
                )
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    @staticmethod
    def projection() -> str:
        fields = ",".join("e." + name for name in EVENT_FIELDS)
        absent = dict.fromkeys(EVENT_FIELDS, "NULL")
        absent.update(
            litellm_call_id="t.call_id",
            started_at="t.started_at",
            provider_outcome="'unknown'",
            client_outcome="'unknown'",
            usage_source="'missing'",
        )
        return (
            "WITH records AS (SELECT " + fields + ","
            "e.model_alias AS model_id,COALESCE(t.name,?) AS name,"
            "t.upstream_model,t.revision,t.actor,"
            "COALESCE(t.outcome,e.provider_outcome) AS result,"
            "t.duration_ms AS end_to_end_ms,t.error,"
            "COALESCE(t.content_status,'not_recorded') AS content_status "
            "FROM events e LEFT JOIN calls t ON t.call_id=e.litellm_call_id "
            "AND t.model_id=e.model_alias UNION ALL SELECT "
            + ",".join(absent.values())
            + ","
            "t.model_id,t.name,t.upstream_model,t.revision,t.actor,t.outcome,"
            "t.duration_ms,t.error,t.content_status FROM calls t WHERE t.model_id=? "
            "AND NOT EXISTS (SELECT 1 FROM events e "
            "WHERE e.litellm_call_id=t.call_id)) "
        )

    @staticmethod
    def filters(params: Mapping[str, str]) -> tuple[str, list, int, int]:
        if set(params) - {
            "start",
            "end",
            "model",
            "result",
            "usage_source",
            "caller",
            "page",
            "size",
        }:
            raise ValueError("invalid_filter")
        page, size = int(params.get("page", "1")), int(params.get("size", "20"))
        if not 1 <= page <= 100000 or not 1 <= size <= 100:
            raise ValueError("invalid_page")
        start, end = timestamp(params.get("start")), timestamp(params.get("end"))
        if start and end and start >= end:
            raise ValueError("invalid_window")
        clauses, values = [], []
        for col, value in (("started_at>=?", start), ("started_at<?", end)):
            if value:
                clauses.append(col)
                values.append(value)
        for key, choices in (
            ("result", ("success", "error", "unknown")),
            ("usage_source", SOURCES),
            ("caller", ("local-operator", "unknown")),
        ):
            if key in params:
                if params[key] not in choices:
                    raise ValueError("invalid_filter")
                if key == "caller":
                    clauses.append(
                        "actor IS NULL"
                        if params[key] == "unknown"
                        else "actor='local-operator'"
                    )
                else:
                    clauses.append(key + "=?")
                    values.append(params[key])
        if "model" in params and not ID.fullmatch(params["model"]):
            raise ValueError("invalid_model")
        return (
            (" WHERE " + " AND ".join(clauses) if clauses else ""),
            values,
            page,
            size,
        )

    def query(self, params: Mapping[str, str]) -> dict:
        """Return filtered counts and per-source totals, plus a bounded global page."""
        where, values, page, size = self.filters(params)
        catalog = self.catalog()
        items, models, usage = [], [], []
        for model in catalog.values():
            if params.get("model", model["id"]) != model["id"]:
                continue
            with self.snapshot(model) as db:
                args = [model["name"], model["id"], *values]
                base = self.projection()
                counts = dict(
                    db.execute(
                        base + "SELECT COUNT(*) AS requests,"
                        "COUNT(CASE WHEN result='success' THEN 1 END) AS success,"
                        "COUNT(CASE WHEN result='error' THEN 1 END) AS errors,"
                        "COUNT(COALESCE(end_to_end_ms,duration_ms)) AS latency_known,"
                        "SUM(COALESCE(end_to_end_ms,duration_ms)) AS latency_sum "
                        "FROM records" + where,
                        args,
                    ).fetchone()
                )
                if not counts["requests"]:
                    continue
                counts.update(
                    model_id=model["id"],
                    name=model["name"],
                    avg_duration_ms=counts["latency_sum"] / counts["latency_known"]
                    if counts["latency_known"]
                    else None,
                )
                models.append(counts)
                groups = [
                    dict(r)
                    for r in db.execute(
                        base
                        + "SELECT usage_source,COUNT(*) AS requests,"
                        + ",".join(
                            f"SUM({c}) AS {c},COUNT({c}) AS {c}_known" for c in TOKENS
                        )
                        + " FROM records"
                        + where
                        + " GROUP BY usage_source",
                        args,
                    )
                ]
                usage.extend(groups)
                counts["usage"] = groups
                items.extend(
                    dict(r)
                    for r in db.execute(
                        base
                        + "SELECT * FROM records"
                        + where
                        + " ORDER BY started_at DESC,litellm_call_id DESC LIMIT ?",
                        [*args, page * size],
                    )
                )
        items.sort(
            key=lambda r: (r["started_at"] or "", r["litellm_call_id"]), reverse=True
        )
        grouped = []
        for source in SOURCES:
            selected = [r for r in usage if r["usage_source"] == source]
            grouped.append(
                dict(
                    usage_source=source,
                    requests=sum(r["requests"] for r in selected),
                    **{c: merge_nullable(selected, c) for c in TOKENS},
                    **{
                        c + "_known": sum(r[c + "_known"] for r in selected)
                        for c in TOKENS
                    },
                )
            )
        return dict(
            total=sum(r["requests"] for r in models),
            success=sum(r["success"] for r in models),
            errors=sum(r["errors"] for r in models),
            page=page,
            size=size,
            items=items[(page - 1) * size : page * size],
            models=models,
            usage=grouped,
            catalog=[dict(id=m["id"], name=m["name"]) for m in catalog.values()],
        )

    def detail(self, model_id: str, call_id: str, include_content: bool = True) -> dict:
        """Read one record and its optional body after validating both identifiers."""
        try:
            if not ID.fullmatch(model_id) or str(UUID(call_id)) != call_id:
                raise ValueError
        except ValueError:
            raise PolicyError(404, "record_not_found") from None
        model = self.catalog().get(model_id)
        if model is None:
            raise PolicyError(404, "record_not_found")
        with self.snapshot(model) as db:
            row = db.execute(
                self.projection() + "SELECT * FROM records WHERE litellm_call_id=?",
                [model["name"], model_id, call_id],
            ).fetchone()
        if row is None:
            raise PolicyError(404, "record_not_found")
        content = (
            self.monitor.content(call_id)
            if include_content
            else dict(status=row["content_status"], messages=None, reply=None)
        )
        return dict(record=dict(row), content=content)
