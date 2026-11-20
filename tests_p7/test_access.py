"""Synthetic model-governance contracts; no external identity or model traffic."""

import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from axi_gateway.access.identity import IdentityError, Verifier, read_demo_config
from axi_gateway.access.policy import PolicyError, PolicyStore
from axi_gateway.access.server import create_app
from axi_gateway.events import Projector
from axi_gateway.launcher import ROOT, main


@pytest.fixture
def signing() -> tuple:
    """Create only ephemeral synthetic signing material."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


def token(signing: tuple, subject: str = "demo-app-a", **claims: object) -> str:
    """Sign a short-lived fixture credential without saving it."""
    now = int(time.time())
    payload = dict(
        sub=subject,
        iss="demo-issuer",
        aud="demo-models",
        iat=now,
        nbf=now,
        exp=now + 300,
    )
    payload.update(claims)
    return jwt.encode(payload, signing[0], algorithm="RS256")


@pytest.fixture
def store(tmp_path: Path) -> PolicyStore:
    """Create an isolated governance store, not the event database."""
    result = PolicyStore(tmp_path / "demo-policy.sqlite3", ("model-a", "model-b"))
    result.initialize()
    result.put("demo-admin", "demo-app-a", ["model-a"], 1000, True, 0)
    return result


def test_identity_validation(signing: tuple) -> None:
    verifier = Verifier(signing[1], "demo-issuer", "demo-models")
    assert verifier.subject(token(signing)) == "demo-app-a"
    for change in (
        {"aud": "admin"},
        {"iss": "other"},
        {"exp": 1},
        {"nbf": int(time.time()) + 60},
        {"exp": int(time.time()) + 7200},
        {"sub": "sk-secret"},
    ):
        with pytest.raises(IdentityError):
            verifier.subject(token(signing, **change))
    for value in (
        "",
        "not-a-token",
        jwt.encode(
            {"sub": "demo-app-a"},
            "synthetic-hmac-secret-not-rsa-long-enough",
            algorithm="HS256",
        ),
    ):
        with pytest.raises(IdentityError):
            verifier.subject(value)


def test_missing_required_claims(signing: tuple) -> None:
    with pytest.raises(IdentityError):
        Verifier(signing[1], "demo-issuer", "demo-models").subject(
            jwt.encode({"sub": "demo-app-a"}, signing[0], algorithm="RS256")
        )


def test_grants_disabled_and_unknown(store: PolicyStore) -> None:
    assert store.admit("demo-app-a", "model-a")["id"] == "demo-app-a"
    for app, model in (("demo-app-a", "model-b"), ("unknown", "model-a")):
        with pytest.raises(PolicyError) as exc:
            store.admit(app, model)
        assert exc.value.status == 403
    store.put("demo-admin", "demo-app-a", ["model-a"], 1000, False, 1)
    with pytest.raises(PolicyError):
        store.admit("demo-app-a", "model-a")


def test_rpm_atomic_and_restart(store: PolicyStore) -> None:
    store.put("demo-admin", "demo-app-a", ["model-a"], 3, True, 1)

    def attempt(_: int) -> bool:
        try:
            store.admit("demo-app-a", "model-a", now=120)
            return True
        except PolicyError as error:
            assert error.status == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(12))) == 3
    reopened = PolicyStore(store.path, ("model-a", "model-b"))
    with pytest.raises(PolicyError):
        reopened.admit("demo-app-a", "model-a", now=120)
    assert reopened.admit("demo-app-a", "model-a", now=180)
    assert reopened.admit("demo-app-a", None, now=180)


def test_audit_atomicity_and_revision(store: PolicyStore) -> None:
    with pytest.raises(PolicyError) as exc:
        store.put("demo-admin", "demo-app-a", ["model-b"], 2, True, 0)
    assert exc.value.status == 409
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON audit "
            "BEGIN SELECT RAISE(ABORT,'synthetic_failure'); END"
        )
    with pytest.raises(PolicyError) as exc:
        store.put("demo-admin", "demo-app-a", ["model-b"], 2, True, 1)
    assert exc.value.status == 503
    assert store.apps()[0]["models"] == ["model-a"]
    assert len(store.audit()) == 1


def test_policy_input_and_missing_store(store: PolicyStore, tmp_path: Path) -> None:
    for models, rpm, enabled in (
        (["unknown"], 2, True),
        (["model-a"], 0, True),
        (["model-a"], True, True),
        (["model-a"], 2, "true"),
        (["model-a"] * 2, 2, True),
    ):
        with pytest.raises(PolicyError):
            store.put("demo-admin", "demo-app-b", models, rpm, enabled, 0)
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(PolicyError) as exc:
        PolicyStore(missing, ("model-a",)).admit("demo-app-a", "model-a")
    assert exc.value.status == 503
    assert not missing.exists()


def test_management_auth_origin_privacy(signing: tuple, store: PolicyStore) -> None:
    async def run() -> None:
        app = create_app(
            store,
            Verifier(signing[1], "demo-issuer", "demo-admin"),
            {"demo-admin"},
            8789,
        )
        headers = {
            "Authorization": "Bearer " + token(signing, "demo-admin", aud="demo-admin")
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8789"
        ) as client:
            assert (await client.get("/api/apps")).status_code == 401
            assert (
                await client.get(
                    "/api/apps", headers={"Authorization": "Bearer " + token(signing)}
                )
            ).status_code == 401
            assert (
                await client.get(
                    "/api/apps",
                    headers={
                        "Authorization": "Bearer "
                        + token(signing, "other", aud="demo-admin")
                    },
                )
            ).status_code == 403
            for extra in (
                {"Host": "evil.example"},
                {"Origin": "https://evil.example"},
                {"Origin": "null"},
            ):
                assert (
                    await client.get("/api/apps", headers=headers | extra)
                ).status_code == 403
            assert (
                await client.options("/api/apps", headers=headers)
            ).status_code == 405
            assert (
                await client.delete("/api/apps", headers=headers)
            ).status_code == 405
            response = await client.get("/api/apps", headers=headers)
            assert response.status_code == 200
            assert "content-security-policy" in response.headers
            assert "access-control-allow-origin" not in response.headers
            payload = dict(models=["model-b"], rpm=5, enabled=True, expected_revision=0)
            response = await client.put(
                "/api/apps/demo-app-b", headers=headers, json=payload
            )
            assert response.status_code == 200
            assert (
                await client.put(
                    "/api/apps/demo-app-c",
                    headers=headers,
                    json=payload | {"prompt": "SENTINEL_BODY"},
                )
            ).status_code == 400
            for route in ("/api/apps", "/api/audit"):
                response = await client.get(route, headers=headers)
                assert response.status_code == 200
                assert all(
                    s not in response.text
                    for s in (
                        headers["Authorization"],
                        "SENTINEL_BODY",
                        "BEGIN",
                        "api_key",
                        "prompt",
                    )
                )
            assert (
                await client.get("/api/audit?limit=10000", headers=headers)
            ).status_code == 400

    asyncio.run(run())


def test_demo_configuration_boundary(signing: tuple, tmp_path: Path) -> None:
    root = tmp_path / ".cache/demo"
    root.mkdir(parents=True)
    path = root / "trust.json"
    data = dict(
        demo=True,
        models=["model-a"],
        database="demo-policy.sqlite3",
        admins=["demo-admin"],
        issuer="demo-issuer",
        model_audience="model",
        admin_audience="admin",
        public_key=signing[1].decode(),
    )
    path.write_text(json.dumps(data), encoding="utf8")
    assert read_demo_config(path, tmp_path).models == ("model-a",)
    for change in (
        {"database": "../../outside.sqlite3"},
        {"demo": False},
        {"admin_audience": "model"},
        {"models": ["model-a", "model-a"]},
    ):
        path.write_text(json.dumps(data | change), encoding="utf8")
        with pytest.raises(ValueError, match="invalid_demo_access_config"):
            read_demo_config(path, tmp_path)


def test_trusted_projection_has_no_request_fallback() -> None:
    values = dict(
        litellm_call_id="10000000-0000-4000-8000-000000000000",
        metadata={"user_api_key_alias": "forged"},
        proxy_server_request={"body": {"key_alias": "forged"}},
    )
    for enabled in (False, True):
        projector = Projector(
            "model-a",
            set(),
            "test",
            litellm_version="1.106.5",
            trusted_identity_mode=enabled,
        )
        assert projector.project(values, {}, None, None, True).key_alias is None
        trusted = values | {
            "litellm_params": {"metadata": {"user_api_key_alias": "demo-app-a"}}
        }
        assert projector.project(trusted, {}, None, None, True).key_alias == (
            "demo-app-a" if enabled else None
        )


def test_management_duplicate_headers_and_bounded_body(
    signing: tuple, store: PolicyStore
) -> None:
    async def run() -> None:
        app = create_app(
            store,
            Verifier(signing[1], "demo-issuer", "demo-admin"),
            {"demo-admin"},
            8789,
        )
        credential = "Bearer " + token(signing, "demo-admin", aud="demo-admin")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8789"
        ) as client:
            response = await client.get(
                "/api/apps",
                headers=[
                    ("Host", "127.0.0.1:8789"),
                    ("Host", "127.0.0.1:8789"),
                    ("Authorization", credential),
                ],
            )
            assert response.status_code == 403
            response = await client.get(
                "/api/apps",
                headers=[("Authorization", credential), ("Authorization", credential)],
            )
            assert response.status_code == 401
            response = await client.put(
                "/api/apps/demo-app-a",
                headers={
                    "Authorization": credential,
                    "Content-Type": "application/json",
                },
                content=b" " * 16385,
            )
            assert response.status_code == 413

    asyncio.run(run())


def test_request_routing_overrides_denied() -> None:
    from axi_gateway.access.protocol import model_request

    base = dict(model="model-a", messages=[])
    assert model_request(base, ("model-a",)) == "model-a"
    for key in (
        "fallbacks",
        "context_window_fallbacks",
        "content_policy_fallbacks",
        "num_retries",
        "api_base",
        "api_key",
        "extra_body",
        "litellm_params",
    ):
        with pytest.raises(PolicyError) as exc:
            model_request(base | {key: ["model-b"]}, ("model-a",))
        assert exc.value.status == 403


def test_demo_key_copy_conflict_precedes_runtime(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "--access-demo",
                "unused.json",
                "--copy-key",
                "--offline",
                "--data-dir",
                str(ROOT / ".cache/demo"),
            ]
        )
        == 1
    )
    assert (
        json.loads(capsys.readouterr().out)["code"]
        == "access_demo_requires_offline_demo_directory"
    )


def test_management_reserved_port_precedes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import start_access

    calls = []
    monkeypatch.setattr(
        start_access.sys,
        "argv",
        ["start_access", "--config", "unused", "--port", "8787"],
    )
    monkeypatch.setattr(
        start_access.sys, "flags", SimpleNamespace(isolated=True, no_site=True)
    )
    monkeypatch.setattr(
        start_access, "verify_environment", lambda *_: calls.append(True)
    )
    assert start_access.main() == 1
    assert not calls
