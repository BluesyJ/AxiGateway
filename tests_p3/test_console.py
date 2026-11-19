"""One synthetic matrix for the query contract, reconciliation, privacy and controls."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from axi_gateway.events import Cost, Labels, Usage
from axi_gateway.queries import read_connection
from axi_gateway.storage import SQLiteSink
from p3_console.server import CSP, VIEWS, create_app
from tests_p2.samples import sample

SECRET = "sk-p3-synthetic-private-key"
BODY = "P3_PRIVATE_RESPONSE_AND_REASONING"


@pytest.fixture(scope="module")
def database(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Path, TestClient]]:
    """Create only isolated synthetic schema-1 data, with explicit zero and NULL."""
    directory = tmp_path_factory.mktemp("p3-demo")
    path = directory / "demo.sqlite3"
    sink = SQLiteSink(path)
    sources = ("reported", "derived", "estimated", "missing", "unknown", "reported")
    event_rows = []
    for i, source in enumerate(sources):
        event_rows.append(
            replace(
                sample(),
                started_at=f"2026-10-02T0{i}:00:00+00:00",
                model_alias="model-a" if i % 2 == 0 else "model-b",
                client_label=Labels(
                    "cli" if i < 3 else None, "sdk" if i < 5 else SECRET
                ),
                usage=Usage(
                    total_tokens=(0, 12, 15, None, None, None)[i], usage_source=source
                ),
                cost=Cost(
                    0.125, "USD", "user_prices", "demo-v1" if i % 2 == 0 else "demo-v2"
                )
                if i < 3
                else Cost(None, None, None, None),
                provider_outcome="error" if i == 3 else "success",
                stream=i == 2,
                client_outcome="client_disconnected" if i == 2 else "unknown",
                key_alias=SECRET,
            )
        )
    sink.write_batch(
        event_rows,
        {
            "accepted": 6,
            "written": 6,
            "auth_rejected": 2,
            "queue_full": 2,
            "storage_failed": 1,
            "projection_failed": 0,
            "sink_failed": 0,
            "rejected_after_close": 0,
        },
        [
            dict(
                run_id=SECRET,
                reason="queue_full",
                count=2,
                first_at=event_rows[0].started_at,
                last_at=event_rows[1].started_at,
            )
        ],
    )
    sink.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE events ADD COLUMN body TEXT")
        connection.execute("UPDATE events SET body=?", (BODY,))
    with TestClient(
        create_app(demo=path, live=directory / "absent.sqlite3"),
        base_url="http://127.0.0.1:8788",
    ) as client:
        yield path, client


def test_pages_filters_and_nulls(database: tuple[Path, TestClient]) -> None:
    """Page boundaries, time/labels/model/outcomes/source and unknown survive HTTP."""
    _, client = database
    url = "/api/demo/requests"
    first = client.get(url, params={"size": 2}).json()
    second = client.get(url, params={"size": 2, "page": 2}).json()
    assert first["total"] == 6 and len(first["items"]) == 2
    assert {r["litellm_call_id"] for r in first["items"]}.isdisjoint(
        r["litellm_call_id"] for r in second["items"]
    )
    result = client.get(
        url,
        params={
            "start": "2026-10-02T10:00:00+08:00",
            "end": "2026-10-02T03:00:00Z",
            "model": "model-a",
            "client": "sdk",
            "client_outcome": "client_disconnected",
            "provider_outcome": "success",
            "usage_source": "estimated",
        },
    ).json()
    assert result["total"] == 1 and result["items"][0]["total_tokens"] == 15
    assert (
        client.get(url, params={"provider_outcome": "error"}).json()["items"][0][
            "total_tokens"
        ]
        is None
    )
    overview = client.get("/api/demo/overview").json()
    assert len(overview["usage"]) == 5
    usage = {r["usage_source"]: r for r in overview["usage"]}
    assert (
        usage["reported"]["tokens"] == 0
        and usage["reported"]["known"] == 1
        and usage["reported"]["requests"] == 2
    )
    assert usage["missing"]["tokens"] is usage["unknown"]["tokens"] is None
    for q in (
        "page=0",
        "usage_source=bad",
        "page=2&page=3",
        "model=x&unexpected=1",
        "start=bad",
    ):
        assert client.get(url + "?" + q).status_code == 400


def test_matches_independent_sql(database: tuple[Path, TestClient]) -> None:
    """Compare the HTTP totals and every group against independently executed SQL."""
    path, client = database
    with sqlite3.connect(path) as db:
        summary = client.get("/api/demo/overview").json()
        assert (
            summary["requests"],
            summary["success"],
            summary["errors"],
        ) == db.execute(
            "SELECT COUNT(*), SUM(provider_outcome='success'),"
            "SUM(provider_outcome='error') FROM events"
        ).fetchone()
        assert {
            (
                r["cost_source"],
                r["price_version"],
                r["cost_currency"],
                r["amount"],
                r["known"],
            )
            for r in summary["costs"]
        } == set(
            db.execute(
                "SELECT cost_source,price_version,cost_currency,SUM(cost_amount),"
                "COUNT(cost_amount) FROM events GROUP BY 1,2,3"
            )
        )
        for view, fields, sql in (
            (
                "trend",
                ("hour", "usage_source"),
                "strftime('%Y-%m-%dT%H:00:00Z',started_at),usage_source",
            ),
            ("models", ("model_alias", "usage_source"), "model_alias,usage_source"),
            (
                "clients",
                ("client_header", "client_metadata", "usage_source"),
                "client_header,CASE WHEN client_metadata LIKE 'sk-%' THEN NULL "
                "ELSE client_metadata END,usage_source",
            ),
        ):
            actual = client.get(f"/api/demo/{view}").json()["items"]
            expected = set(
                db.execute(
                    "SELECT " + sql + ",count(*),count(total_tokens),sum(total_tokens) "
                    "FROM events GROUP BY " + sql
                )
            )
            assert {
                tuple(r[f] for f in (*fields, "requests", "known", "tokens"))
                for r in actual
            } == expected
        status = client.get("/api/demo/health").json()
        assert (
            status["gap_events"] == 2
            and status["gap_windows"] == 1
            and status["lost"] == 2
        )
        assert (
            status["queue_depth"] is None and status["counters"]["auth_rejected"] == 2
        )


def test_private_fields_and_security(database: tuple[Path, TestClient]) -> None:
    """Protect responses, errors, static routes and SQLite against unintended writes."""
    path, client = database
    for view in VIEWS:
        response = client.get("/api/demo/" + view)
        assert response.status_code == 200
        assert all(
            s not in response.text
            for s in (
                BODY,
                SECRET,
                '"body"',
                '"key_alias"',
                '"run_id"',
                "Authorization",
            )
        )
        assert response.headers["content-security-policy"] == CSP
        assert "access-control-allow-origin" not in response.headers
    for route in ("/", "/api/demo/requests", "/assets/example.js"):
        for method in (
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "TRACE",
            "CONNECT",
        ):
            assert client.request(method, route).status_code == 405
        for headers in (
            {"Host": "bad.invalid"},
            {"Origin": "null"},
            {"Origin": "http://127.0.0.1:8787"},
            [("Host", "127.0.0.1:8788"), ("Host", "bad.invalid")],
            [("Origin", "http://127.0.0.1:8788"), ("Origin", "https://bad.invalid")],
        ):
            assert client.get(route, headers=headers).status_code == 403
    assert (
        client.get(
            "/api/demo/health", headers={"Origin": "http://127.0.0.1:8788"}
        ).status_code
        == 200
    )
    assert client.get("/api/live/health").status_code == 503
    assert not path.with_name("absent.sqlite3").exists()
    with (
        read_connection(path) as db,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        db.execute("DELETE FROM events")
