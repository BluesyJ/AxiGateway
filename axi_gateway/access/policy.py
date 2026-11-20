"""Credential-free policy state and atomic governance audit, separate from events."""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from axi_gateway.access.identity import identifier


class PolicyError(Exception):
    """Controlled API status and category; no database exception text escapes."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


class PolicyStore:
    """Use independent connections across management and proxy processes."""

    def __init__(self, path: Path, models: tuple[str, ...]) -> None:
        self.path, self.models = path.resolve(), models

    def initialize(self) -> None:
        """Create a new demo store explicitly; never overwrite an existing database."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("xb"):
            pass
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE apps(id TEXT PRIMARY KEY, models TEXT NOT NULL,
                    rpm INTEGER NOT NULL, enabled INTEGER NOT NULL,
                    revision INTEGER NOT NULL);
                CREATE TABLE audit(sequence INTEGER PRIMARY KEY, at_utc TEXT NOT NULL,
                    actor TEXT NOT NULL, target TEXT NOT NULL, action TEXT NOT NULL,
                    revision INTEGER NOT NULL, models TEXT NOT NULL,
                    rpm INTEGER NOT NULL,
                    enabled INTEGER NOT NULL);
                CREATE TABLE rpm(app TEXT PRIMARY KEY, minute INTEGER NOT NULL,
                    used INTEGER NOT NULL);
            """)

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Fail closed if a store is missing, locked or damaged; never recreate it."""
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=1)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except sqlite3.Error:
            raise PolicyError(503, "policy_store_unavailable") from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def row(row: sqlite3.Row) -> dict:
        """Return the bounded public policy representation."""
        return dict(
            id=row["id"],
            models=json.loads(row["models"]),
            rpm=row["rpm"],
            enabled=bool(row["enabled"]),
            revision=row["revision"],
        )

    def put(
        self,
        actor: str,
        app: str,
        models: list[str],
        rpm: int,
        enabled: bool,
        expected_revision: int,
    ) -> dict:
        """Commit a version-checked policy replacement and its audit together."""
        if (
            not identifier(actor)
            or not identifier(app)
            or not isinstance(models, list)
            or not all(isinstance(m, str) and m in self.models for m in models)
            or len(set(models)) != len(models)
            or len(models) > 32
            or type(rpm) is not int
            or not 1 <= rpm <= 10000
            or type(enabled) is not bool
            or type(expected_revision) is not int
            or not 0 <= expected_revision <= 2**31
        ):
            raise PolicyError(400, "invalid_policy")
        with self.connect(write=True) as db:
            old = db.execute("SELECT revision FROM apps WHERE id=?", (app,)).fetchone()
            revision = old[0] if old else 0
            if revision != expected_revision:
                raise PolicyError(409, "revision_conflict")
            if (
                not old
                and db.execute("SELECT count(*) FROM apps").fetchone()[0] >= 1000
            ):
                raise PolicyError(409, "application_limit")
            values = (app, json.dumps(sorted(models)), rpm, int(enabled), revision + 1)
            db.execute(
                "INSERT INTO apps VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "models=excluded.models,rpm=excluded.rpm,enabled=excluded.enabled,"
                "revision=excluded.revision",
                values,
            )
            db.execute(
                "INSERT INTO audit(at_utc,actor,target,action,revision,"
                "models,rpm,enabled) "
                "VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),?,?,?,?,?,?,?)",
                (
                    actor,
                    app,
                    "policy_replaced",
                    revision + 1,
                    values[1],
                    rpm,
                    int(enabled),
                ),
            )
            return self.row(
                db.execute("SELECT * FROM apps WHERE id=?", (app,)).fetchone()
            )

    def admit(self, app: str, model: str | None, now: float | None = None) -> dict:
        """Atomically check current grants and consume one UTC minute admission."""
        with self.connect(write=model is not None) as db:
            row = db.execute("SELECT * FROM apps WHERE id=?", (app,)).fetchone()
            if not row or not row["enabled"]:
                raise PolicyError(403, "application_denied")
            result = self.row(row)
            if model is not None:
                if model not in result["models"] or model not in self.models:
                    raise PolicyError(403, "model_denied")
                minute = int((time.time() if now is None else now) // 60)
                counter = db.execute(
                    "SELECT minute,used FROM rpm WHERE app=?", (app,)
                ).fetchone()
                used = counter[1] if counter and counter[0] == minute else 0
                if used >= result["rpm"]:
                    raise PolicyError(429, "rpm_exceeded")
                db.execute(
                    "INSERT INTO rpm VALUES(?,?,?) ON CONFLICT(app) DO UPDATE SET "
                    "minute=excluded.minute,used=excluded.used",
                    (app, minute, used + 1),
                )
            return result

    def apps(self) -> list[dict]:
        """List at most the enforced 1000 applications, without credentials."""
        with self.connect() as db:
            return [
                self.row(row) for row in db.execute("SELECT * FROM apps ORDER BY id")
            ]

    def audit(self, limit: int = 100) -> list[dict]:
        """Return bounded, immutable policy-change history, not invocation content."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PolicyError(400, "invalid_limit")
        with self.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit ORDER BY sequence DESC LIMIT ?", (limit,)
                )
            ]
        for row in rows:
            row["models"] = json.loads(row["models"])
            row["enabled"] = bool(row["enabled"])
        return rows
