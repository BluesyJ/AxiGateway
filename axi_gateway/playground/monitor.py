"""Local request metadata and explicitly opted-in encrypted conversation content."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

CALL_SCHEMA = """CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, name TEXT NOT NULL,
    upstream_model TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL,
    started_at TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms REAL,
    error TEXT, content_status TEXT NOT NULL, content BLOB
)"""


class Monitor:
    """Keep request context separate from P2 usage and from model credentials."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "monitor.sqlite3"

    def begin(self, call_id: str, model: dict) -> None:
        """Record a local operation, without claiming an authenticated person."""
        with sqlite3.connect(self.path, timeout=1) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(CALL_SCHEMA)
            db.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,'local-operator',?,"
                "'unknown',NULL,NULL,'disabled',NULL)",
                (
                    call_id,
                    model["id"],
                    model["name"],
                    model["model"],
                    model["revision"],
                    datetime.now(UTC).isoformat(),
                ),
            )

    def finish(
        self,
        call_id: str,
        outcome: str,
        duration_ms: int,
        messages: list[dict],
        reply: str | None,
        save_content: bool,
        secrets: tuple[str, ...],
        error: str | None = None,
    ) -> str:
        """Store no plaintext content; content failures do not invite a retry."""
        status, blob = "disabled", None
        if save_content:
            try:
                import win32crypt

                def clean(value: str) -> str:
                    for secret in secrets:
                        if secret:
                            value = value.replace(secret, "[已隐藏凭据]")
                    return value

                content = dict(
                    messages=[
                        dict(role=m["role"], content=clean(m["content"]))
                        for m in messages
                    ],
                    reply=clean(reply) if reply is not None else None,
                )
                blob = win32crypt.CryptProtectData(
                    json.dumps(content).encode("utf8"),
                    "Playground content",
                    None,
                    None,
                    None,
                    1,
                )
                status = "saved"
            except Exception:
                status = "save_failed"
        with sqlite3.connect(self.path, timeout=1) as db:
            db.execute(
                "UPDATE calls SET outcome=?,duration_ms=?,error=?,"
                "content_status=?,content=? WHERE call_id=?",
                (outcome, duration_ms, error, status, blob, call_id),
            )
        return status

    def content(self, call_id: str) -> dict:
        """Decrypt only one explicitly requested body, never the list response."""
        empty = dict(status="not_recorded", messages=None, reply=None)
        if not self.path.exists():
            return empty
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT content_status,content FROM calls WHERE call_id=?", (call_id,)
            ).fetchone()
        finally:
            db.close()
        if row is None:
            return empty
        if row[0] != "saved":
            return empty | {"status": row[0]}
        try:
            import win32crypt

            _, clear = win32crypt.CryptUnprotectData(row[1], None, None, None, 1)
            return json.loads(clear) | {"status": "saved"}
        except Exception:
            return empty | {"status": "unavailable"}
