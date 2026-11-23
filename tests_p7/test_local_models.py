"""Local model configuration and chat contracts; synthetic secrets only."""

import asyncio
import site
from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

from axi_gateway.access.policy import PolicyError
from axi_gateway.playground.models import ModelStore

ROOT = Path(__file__).resolve().parents[1]
KEY = "synthetic-model-secret-sentinel"
PROFILE = dict(
    name="Test model",
    base_url="https://models.example/v1",
    model="org/model",
    key=KEY,
    expected_revision=0,
)


@pytest.fixture(scope="session", autouse=True)
def locked_windows_modules() -> None:
    from axi_gateway.bootstrap import verify_environment

    verify_environment(ROOT, ROOT / ".venv-litellm")
    site.addsitedir(str(ROOT / ".venv-litellm/Lib/site-packages"))


@pytest.fixture
def store(tmp_path: Path) -> ModelStore:
    from axi_gateway.playground.models import ModelStore

    return ModelStore(tmp_path / "models")


def test_openai_profile_preserves_environment_credentials(tmp_path: Path) -> None:
    from axi_gateway.launcher import proxy_configuration, read_settings

    path = tmp_path / "profile.yaml"
    path.write_text(
        "model_alias: local-model\nprovider_model: openai/org/model\n"
        "api_base: https://models.example/v1\nqueue_capacity: 256\n"
    )
    result = proxy_configuration(read_settings(path))
    assert result["model_list"][0]["litellm_params"]["model"] == "openai/org/model"
    assert (
        result["model_list"][0]["litellm_params"]["api_key"]
        == "os.environ/UPSTREAM_API_KEY"
    )
    assert result["router_settings"]["num_retries"] == 0


def test_encrypted_store_reload_update_delete(store: ModelStore) -> None:
    from axi_gateway.playground.models import ModelStore

    saved = store.create(PROFILE)
    assert "key" not in saved and saved["has_key"]
    blob = (store.directory / "models.dpapi").read_bytes()
    assert KEY.encode() not in blob
    again = ModelStore(store.directory)
    assert again.list() == [saved]
    changed = again.put(
        saved["id"], PROFILE | dict(key="", name="Renamed", expected_revision=1)
    )
    assert changed["revision"] == 2
    assert again.selected(saved["id"])["key"] == KEY
    with pytest.raises(PolicyError) as exc:
        store.put(saved["id"], PROFILE | dict(expected_revision=1))
    assert exc.value.status == 409
    again.delete(saved["id"], 2)
    assert ModelStore(store.directory).list() == []


def test_changed_destination_requires_new_secret_and_bad_input_is_atomic(
    store: ModelStore,
) -> None:
    saved = store.create(PROFILE)
    before = (store.directory / "models.dpapi").read_bytes()
    for patch in (
        dict(base_url="https://other.example/v1", key=""),
        dict(base_url="http://remote.example/v1"),
        dict(base_url="https://user:pass@models.example/v1"),
        dict(base_url="https://models.example/v1?api_key=secret"),
        dict(base_url="https://models.example/v1/chat/completions"),
        dict(model="bad\nmodel"),
    ):
        with pytest.raises(PolicyError):
            store.put(saved["id"], PROFILE | dict(expected_revision=1) | patch)
        assert (store.directory / "models.dpapi").read_bytes() == before
    fresh = store.put(
        saved["id"],
        PROFILE
        | dict(
            expected_revision=1,
            base_url="https://other.example/v1",
            key="different-synthetic-key",
        ),
    )
    assert fresh["revision"] == 2


def test_corrupt_store_fails_closed(store: ModelStore) -> None:
    path = store.directory / "models.dpapi"
    path.write_bytes(b"invalid-synthetic-encrypted-data")
    with pytest.raises(PolicyError):
        store.list()
    with pytest.raises(PolicyError):
        store.create(PROFILE)
    assert path.read_bytes() == b"invalid-synthetic-encrypted-data"


def test_messages_and_output_limit_validation() -> None:
    from axi_gateway.playground.model_api import chat_input

    valid = dict(
        model="local-123",
        revision=1,
        messages=[dict(role="user", content="hello")],
        max_tokens=512,
    )
    assert chat_input(valid) == valid
    for patch in (
        dict(max_tokens=True),
        dict(max_tokens=4097),
        dict(messages=[]),
        dict(messages=[dict(role="tool", content="x")]),
        dict(messages=[dict(role="user", content="x", api_key=KEY)]),
    ):
        with pytest.raises(PolicyError):
            chat_input(valid | patch)


def test_model_routes_privacy_and_same_origin(store: ModelStore) -> None:
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse

    from axi_gateway.playground.model_api import model_routes
    from axi_gateway.playground.server import DemoGate

    class Runtime:
        lock = asyncio.Lock()

        async def close(self) -> None:
            pass

        async def chat(self, data: dict) -> dict:
            raise PolicyError(502, "upstream_request_failed")

    async def error(request: Request, exc: PolicyError) -> JSONResponse:
        return JSONResponse({"error": exc.code}, exc.status)

    async def exercise() -> None:
        app = DemoGate(
            Starlette(
                routes=model_routes(store, Runtime()),
                exception_handlers={PolicyError: error},
            ),
            8790,
        )
        headers = {"X-Playground": "1", "Origin": "http://127.0.0.1:8790"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8790"
        ) as client:
            for bad in (
                {},
                headers | {"Origin": "https://evil.example"},
                headers | {"Host": "evil.example"},
            ):
                assert (
                    await client.post("/api/local/models", headers=bad, json=PROFILE)
                ).status_code == 403
            response = await client.post(
                "/api/local/models", headers=headers, json=PROFILE
            )
            assert response.status_code == 201 and KEY not in response.text
            listed = await client.get("/api/local/models", headers=headers)
            assert KEY not in listed.text and len(listed.json()["models"]) == 1
            result = await client.post(
                "/api/local/chat",
                headers=headers,
                json=dict(
                    model=response.json()["id"],
                    revision=1,
                    messages=[dict(role="user", content="body-sentinel")],
                    max_tokens=64,
                ),
            )
            assert result.status_code == 502 and "body-sentinel" not in result.text
            assert "reply" not in result.json()
            deleted = await client.request(
                "DELETE",
                "/api/local/models/" + response.json()["id"],
                headers=headers,
                json={"expected_revision": 1},
            )
            assert deleted.status_code == 200
            assert (await client.get("/api/local/models", headers=headers)).json()[
                "models"
            ] == []

    asyncio.run(exercise())


def test_stale_conversation_rejected_before_starting_changed_endpoint(
    store: ModelStore,
) -> None:
    from axi_gateway.playground.model_runtime import ModelRuntime

    saved = store.create(PROFILE)
    store.put(
        saved["id"],
        PROFILE
        | dict(
            expected_revision=1,
            base_url="https://other.example/v1",
            key="new-synthetic-key",
        ),
    )
    runtime = ModelRuntime(ROOT, store, offline=True)

    async def no_start(model: dict) -> None:
        raise AssertionError("stale conversation must not start any proxy")

    runtime._start = no_start

    async def exercise() -> None:
        with pytest.raises(PolicyError) as exc:
            await runtime.chat(
                dict(
                    model=saved["id"],
                    revision=1,
                    messages=[dict(role="user", content="old-context")],
                    max_tokens=64,
                )
            )
        assert exc.value.status == 409
        assert exc.value.code == "model_configuration_changed"

    asyncio.run(exercise())


def test_long_assistant_reply_can_continue_within_total_body_limit() -> None:
    from axi_gateway.playground.model_api import chat_input

    data = dict(
        model="local-123",
        revision=1,
        messages=[
            dict(role="assistant", content="a" * 13000),
            dict(role="user", content="continue"),
        ],
        max_tokens=4096,
    )
    assert chat_input(data) == data
