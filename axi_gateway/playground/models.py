"""Current-user encrypted model settings, separate from telemetry and demo data."""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from axi_gateway.access.policy import PolicyError
from axi_gateway.secrets import private_event_directory


def profile(data: dict) -> dict:
    """Accept explicit OpenAI-compatible endpoints, not provider request overrides."""
    if set(data) != {"name", "base_url", "model", "key", "expected_revision"}:
        raise PolicyError(400, "invalid_model_config")
    if any(not isinstance(data[k], str) for k in ("name", "base_url", "model", "key")):
        raise PolicyError(400, "invalid_model_config")
    result = {k: data[k].strip() for k in ("name", "base_url", "model", "key")}
    result["base_url"] = result["base_url"].rstrip("/")
    if (
        not 1 <= len(result["name"]) <= 64
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", result["model"])
        or len(result["key"]) > 4096
        or any(ord(c) < 32 for c in result["key"] + result["name"])
        or type(data["expected_revision"]) is not int
        or data["expected_revision"] < 0
    ):
        raise PolicyError(400, "invalid_model_config")
    try:
        url = urlsplit(result["base_url"])
        valid = (
            len(result["base_url"]) <= 2048
            and bool(url.hostname)
            and not (url.username or url.password or url.query or url.fragment)
            and not any(c.isspace() for c in result["base_url"])
            and (
                url.scheme == "https"
                or (
                    url.scheme == "http"
                    and url.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
            and (url.port is None or 1 <= url.port <= 65535)
            and not url.path.endswith(("/chat/completions", "/responses"))
        )
    except ValueError:
        valid = False
    if not valid:
        raise PolicyError(400, "invalid_model_endpoint")
    return result


class ModelStore:
    """Persist an atomic DPAPI document; never project its credentials to clients."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "models.dpapi"
        self.lock = threading.RLock()
        private_event_directory(directory)

    def _read(self) -> dict:
        import win32crypt

        try:
            if not self.path.exists():
                return {"version": 2, "models": {}, "audit": []}
            _, clear = win32crypt.CryptUnprotectData(
                self.path.read_bytes(), None, None, None, 1
            )
            data = json.loads(clear)
            if not isinstance(data, dict):
                raise ValueError
            if "version" not in data:
                return {"version": 2, "models": data, "audit": []}
            if data["version"] != 2 or not isinstance(data.get("audit"), list):
                raise ValueError
            return data
        except Exception:
            raise PolicyError(503, "model_store_unavailable") from None

    def _write(self, models: dict) -> None:
        import win32crypt

        temporary = self.directory / "models.tmp.dpapi"
        try:
            blob = win32crypt.CryptProtectData(
                json.dumps(models).encode("utf8"),
                "ItbigtecGateway models",
                None,
                None,
                None,
                1,
            )
            temporary.write_bytes(blob)
            temporary.replace(self.path)
        except Exception:
            raise PolicyError(503, "model_store_unavailable") from None

    @staticmethod
    def public(model: dict) -> dict:
        """Return only non-secret fields, with a credential-presence indicator."""
        result = {
            k: model[k] for k in ("id", "name", "base_url", "model", "revision")
        } | {"has_key": bool(model["key"])}
        if model["key"]:
            for key in ("name", "base_url", "model"):
                result[key] = result[key].replace(model["key"], "[已隐藏凭据]")
        return result

    def list(self) -> list[dict]:
        """List saved models without decryptable credentials in the response."""
        with self.lock:
            return [self.public(v) for v in self._read()["models"].values()]

    def audit(self) -> list[dict]:
        """Read committed model changes, including removed models, without keys."""
        with self.lock:
            return list(reversed(self._read()["audit"]))

    @staticmethod
    def _audit(document: dict, model: dict, action: str) -> None:
        public = ModelStore.public(model)
        document["audit"].append(
            dict(
                sequence=len(document["audit"]) + 1,
                at_utc=datetime.now(UTC).isoformat(),
                actor="local-operator",
                action=action,
                target=model["id"],
                name=public["name"],
                model=public["model"],
                revision=model["revision"],
            )
        )

    def selected(self, model_id: str) -> dict:
        """Resolve credentials internally for exactly one selected model."""
        with self.lock:
            record = self._read()["models"].get(model_id)
            if record is None:
                raise PolicyError(404, "model_not_found")
            return record

    def create(self, data: dict) -> dict:
        """Allocate a non-secret gateway alias and store one model."""
        return self.put("local-" + uuid4().hex[:16], data)

    def put(self, model_id: str, data: dict) -> dict:
        """Apply versioned edits; changing destinations requires re-entering a key."""
        value = profile(data)
        with self.lock:
            document = self._read()
            models = document["models"]
            old = models.get(model_id)
            if data["expected_revision"] != (old["revision"] if old else 0):
                raise PolicyError(409, "revision_conflict")
            if not re.fullmatch(r"local-[a-f0-9]{16}", model_id):
                raise PolicyError(400, "invalid_model_id")
            if not old and len(models) >= 32:
                raise PolicyError(400, "model_limit_reached")
            if old and not value["key"]:
                if old["base_url"] != value["base_url"] and old["key"]:
                    raise PolicyError(400, "new_endpoint_requires_key")
                value["key"] = old["key"]
            if not value["key"] and urlsplit(value["base_url"]).hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise PolicyError(400, "model_key_required")
            value.update(id=model_id, revision=data["expected_revision"] + 1)
            models[model_id] = value
            self._audit(document, value, "update" if old else "create")
            self._write(document)
            return self.public(value)

    def delete(self, model_id: str, revision: int) -> None:
        """Remove a model only if the page has the current revision."""
        with self.lock:
            document = self._read()
            models = document["models"]
            if model_id not in models:
                raise PolicyError(404, "model_not_found")
            if type(revision) is not int or models[model_id]["revision"] != revision:
                raise PolicyError(409, "revision_conflict")
            self._audit(document, models[model_id], "delete")
            del models[model_id]
            self._write(document)
