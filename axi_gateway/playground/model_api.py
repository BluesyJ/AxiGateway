"""Same-origin model management and bounded text conversations."""

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from axi_gateway.access.policy import PolicyError
from axi_gateway.playground.model_runtime import ModelRuntime
from axi_gateway.playground.models import ModelStore


def chat_input(data: dict) -> dict:
    """Allow text conversation fields only, with no caller-controlled routing."""
    if (
        set(data) != {"model", "revision", "messages", "max_tokens"}
        or type(data["revision"]) is not int
        or data["revision"] < 1
        or not isinstance(data["model"], str)
        or not 1 <= len(data["model"]) <= 64
        or type(data["max_tokens"]) is not int
        or not 1 <= data["max_tokens"] <= 4096
        or not isinstance(data["messages"], list)
        or not 1 <= len(data["messages"]) <= 32
    ):
        raise PolicyError(400, "invalid_chat_request")
    for message in data["messages"]:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in {"system", "user", "assistant"}
            or not isinstance(message["content"], str)
            or not message["content"]
        ):
            raise PolicyError(400, "invalid_chat_request")
    if data["messages"][-1]["role"] != "user":
        raise PolicyError(400, "invalid_chat_request")
    return data


def model_routes(store: ModelStore, runtime: ModelRuntime) -> list[Route]:
    """Expose model metadata and write-only keys on the local Playground."""
    from axi_gateway.playground.server import body

    async def models(request: Request) -> JSONResponse:
        if request.method == "GET":
            return JSONResponse({"models": await asyncio.to_thread(store.list)})
        value = await body(request)
        return JSONResponse(await asyncio.to_thread(store.create, value), 201)

    async def change(request: Request) -> JSONResponse:
        data = await body(request)
        model_id = request.path_params["model_id"]
        async with runtime.lock:
            if request.method == "DELETE":
                if set(data) != {"expected_revision"}:
                    raise PolicyError(400, "invalid_model_config")
                await asyncio.to_thread(
                    store.delete, model_id, data["expected_revision"]
                )
                await runtime.close()
                return JSONResponse({"deleted": True})
            result = await asyncio.to_thread(store.put, model_id, data)
            await runtime.close()
            return JSONResponse(result)

    async def chat(request: Request) -> JSONResponse:
        return JSONResponse(await runtime.chat(chat_input(await body(request, 262144))))

    return [
        Route("/api/local/models", models, methods=["GET", "POST"]),
        Route("/api/local/models/{model_id}", change, methods=["PUT", "DELETE"]),
        Route("/api/local/chat", chat, methods=["POST"]),
    ]
