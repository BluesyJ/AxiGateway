"""Restrict demo requests to standard chat fields, without client routing controls."""

from axi_gateway.access.policy import PolicyError

CHAT_FIELDS = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "top_p",
        "n",
        "max_tokens",
        "max_completion_tokens",
        "stop",
        "stream",
        "stream_options",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "response_format",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "user",
        "metadata",
        "litellm_metadata",
    }
)


def model_request(data: object, models: tuple[str, ...]) -> str:
    """Reject extensions before admission so routing cannot bypass model grants."""
    if not isinstance(data, dict) or set(data) - CHAT_FIELDS:
        raise PolicyError(403, "request_extension_denied")
    model = data.get("model")
    if not isinstance(model, str) or model not in models:
        raise PolicyError(403, "model_denied")
    return model
