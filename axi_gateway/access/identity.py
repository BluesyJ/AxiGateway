"""Validate externally issued identities with the existing pinned PyJWT library."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


class IdentityError(Exception):
    """A credential failed validation; never include its value in diagnostics."""


def identifier(value: object) -> bool:
    """Allow only bounded non-secret application/administrator identifiers."""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is not None
        and not value.lower().startswith(("sk-", "bearer", "eyj"))
    )


class Verifier:
    """Pin algorithm, issuer, audience, public key and short credential lifetime."""

    def __init__(self, public_key: bytes, issuer: str, audience: str) -> None:
        key = load_pem_public_key(public_key)
        if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
            raise ValueError("invalid_public_key")
        self.key, self.issuer, self.audience = key, issuer, audience

    def subject(self, token: str) -> str:
        """Return a verified subject, or a controlled failure without token text."""
        if not isinstance(token, str) or not 1 <= len(token) <= 8192:
            raise IdentityError("identity_rejected")
        try:
            claims = jwt.decode(
                token,
                self.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub"]},
            )
            if (
                not identifier(claims["sub"])
                or claims["aud"] != self.audience
                or any(type(claims[k]) is not int for k in ("iat", "nbf", "exp"))
                or not 0 < claims["exp"] - claims["iat"] <= 900
                or not claims["iat"] <= claims["nbf"] < claims["exp"]
            ):
                raise IdentityError("identity_rejected")
            return claims["sub"]
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise IdentityError("identity_rejected") from None


@dataclass(frozen=True)
class DemoConfig:
    """Non-secret public trust settings; issuance is outside the gateway."""

    models: tuple[str, ...]
    database: Path
    admins: frozenset[str]
    model_verifier: Verifier
    admin_verifier: Verifier


def read_demo_config(path: Path, root: Path) -> DemoConfig:
    """Load only an explicit demo trust file under the workspace ignored roots."""
    try:
        data = json.loads(path.read_text(encoding="utf8"))
        if (
            set(data)
            != {
                "demo",
                "models",
                "database",
                "admins",
                "issuer",
                "model_audience",
                "admin_audience",
                "public_key",
            }
            or data["demo"] is not True
        ):
            raise ValueError
        models, admins = data["models"], data["admins"]
        if any(
            not isinstance(values, list)
            or not 1 <= len(values) <= 32
            or not all(identifier(v) for v in values)
            or len(set(values)) != len(values)
            for values in (models, admins)
        ):
            raise ValueError
        if (
            not all(
                isinstance(data[k], str) and 1 <= len(data[k]) <= 256
                for k in ("issuer", "model_audience", "admin_audience")
            )
            or data["model_audience"] == data["admin_audience"]
        ):
            raise ValueError
        database = (path.parent / data["database"]).resolve()
        if "demo" not in database.parts or not any(
            database.is_relative_to(root / name) for name in ("data", ".cache")
        ):
            raise ValueError
        public = data["public_key"].encode("ascii")
        return DemoConfig(
            tuple(models),
            database,
            frozenset(admins),
            Verifier(public, data["issuer"], data["model_audience"]),
            Verifier(public, data["issuer"], data["admin_audience"]),
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid_demo_access_config") from None
