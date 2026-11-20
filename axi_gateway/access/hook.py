"""LiteLLM OSS authentication extension; never forward a model request itself."""

import asyncio
import os
from pathlib import Path

from fastapi import HTTPException, Request
from litellm.proxy._types import UserAPIKeyAuth

from axi_gateway.access.identity import IdentityError, read_demo_config
from axi_gateway.access.policy import PolicyError, PolicyStore
from axi_gateway.access.protocol import model_request

ROOT = Path(__file__).resolve().parents[2]
CONFIG = read_demo_config(Path(os.environ["AXI_ACCESS_DEMO_CONFIG"]), ROOT)
STORE = PolicyStore(CONFIG.database, CONFIG.models)


async def authenticate(request: Request, api_key: str) -> UserAPIKeyAuth:
    """Validate a signed application, then consult current policy on every request."""
    try:
        values = request.headers.getlist("authorization")
        if len(values) != 1 or not values[0].startswith("Bearer "):
            raise IdentityError("identity_rejected")
        subject = CONFIG.model_verifier.subject(values[0][7:])
        model = None
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            try:
                data = await request.json()
                model = model_request(data, CONFIG.models)
            except ValueError:
                raise PolicyError(400, "invalid_model_request") from None
        elif request.method != "GET" or request.url.path != "/v1/models":
            raise PolicyError(403, "route_denied")
        policy = await asyncio.to_thread(STORE.admit, subject, model)
        if not policy["models"]:
            raise PolicyError(403, "model_denied")
        # Returning no raw token prevents a credential entering callback metadata.
        return UserAPIKeyAuth(
            key_alias=subject, models=policy["models"], user_role="internal_user"
        )
    except IdentityError:
        raise HTTPException(401, "identity_rejected") from None
    except PolicyError as error:
        raise HTTPException(error.status, error.code) from None
