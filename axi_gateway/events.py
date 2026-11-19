"""Metadata-only event contract v0; callback quantities are not a provider invoice."""

import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

UsageSource = Literal["reported", "derived", "estimated", "missing", "unknown"]


def get(value: object, name: str, default: object = None) -> object:
    """Read one explicit field without copying provider/request objects."""
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


def count(value: object) -> int | None:
    """Accept only bounded nonnegative integer counters, never booleans."""
    return value if type(value) is int and 0 <= value <= 2**53 else None


def amount(value: object) -> float | None:
    """Return only finite nonnegative cost values."""
    if type(value) not in (int, float):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 1e12 else None


@dataclass(frozen=True)
class Usage:
    """Usage values plus provenance; reasoning is a subset of completion."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_source: UsageSource = "missing"
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    cache_miss_source: UsageSource | None = None
    reasoning_tokens: int | None = None


def usage_projection(value: object, source: UsageSource = "unknown") -> Usage:
    """Apply D04 only to independently proven reported usage; never trust metadata."""
    if value is None:
        return Usage(usage_source="estimated" if source == "estimated" else "missing")
    prompt = count(get(value, "prompt_tokens"))
    completion = count(get(value, "completion_tokens"))
    total = count(get(value, "total_tokens"))
    cached = count(get(get(value, "prompt_tokens_details"), "cached_tokens"))
    reasoning = count(get(get(value, "completion_tokens_details"), "reasoning_tokens"))
    if source != "reported":
        return Usage(prompt, completion, total, source)
    raw_hit = count(get(value, "prompt_cache_hit_tokens"))
    raw_miss = count(get(value, "prompt_cache_miss_tokens"))
    hit = raw_hit if raw_hit is not None else cached
    if prompt is None or (hit is not None and hit > prompt):
        hit = None
    miss_source: UsageSource | None = None
    miss = None
    if raw_miss is not None:
        if (
            prompt is not None
            and raw_miss <= prompt
            and (hit is None or hit + raw_miss == prompt)
        ):
            miss, miss_source = raw_miss, "reported"
    elif hit is not None and prompt is not None:
        miss, miss_source = prompt - hit, "derived"
    if reasoning is not None and (completion is None or reasoning > completion):
        reasoning = None
    return Usage(
        prompt,
        completion,
        total,
        source,
        hit,
        miss,
        miss_source,
        reasoning,
    )


@dataclass(frozen=True)
class Labels:
    """Two untrusted display labels; neither is used for authorization."""

    header: str | None
    metadata: str | None


@dataclass(frozen=True)
class Cost:
    """Versioned estimate, explicitly not an invoice."""

    amount: float | None
    currency: str | None
    source: Literal["litellm_builtin", "user_prices"] | None
    price_version: str | None
    estimate_not_invoice: bool = True


@dataclass(frozen=True)
class Event:
    """The metadata payload accepted by SQLite and optional development sinks."""

    litellm_call_id: str
    started_at: str | None
    ended_at: str | None
    duration_ms: float | None
    client_label: Labels
    key_alias: str | None
    model_alias: str
    stream: bool
    provider_outcome: Literal["success", "error", "unknown"]
    client_outcome: Literal["unknown", "client_disconnected"]
    usage: Usage
    cost: Cost
    error_category: str | None
    schema_version: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serialize only fields declared by the contract, never original kwargs."""
        return asdict(self)


def label(value: object, credentials: tuple[str, ...]) -> str | None:
    """Bound untrusted labels and reject credential-shaped or configured secrets."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value):
        return None
    if value.lower().startswith(("sk-", "bearer", "eyj")) or any(
        s and s in value for s in credentials
    ):
        return None
    return value


def utc_time(value: object) -> datetime | None:
    """Convert callback instants to UTC; naive SDK clocks use host local time."""
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC)


def stream_disconnected(kwargs: dict) -> bool:
    """Read only LiteLLM's normalized log and sanitized callback metadata."""
    if kwargs.get("stream") is not True:
        return False
    standard = kwargs.get("standard_logging_object") or {}
    error = get(standard, "error_information") or {}
    metadata = get(kwargs.get("litellm_params"), "metadata") or {}
    return (
        get(error, "error_code") in (499, "499")
        or get(error, "error_class") == "ClientDisconnected"
        or get(metadata, "client_disconnected") is True
    )


class Projector:
    """Project only LiteLLM logging fields; no transport or iterator interception."""

    def __init__(
        self,
        alias: str | None,
        priced_models: set[str],
        price_version: str,
        credentials: tuple[str, ...] = (),
        aliases: tuple[str, ...] = (),
        litellm_version: str | None = None,
    ) -> None:
        self.alias, self.priced_models, self.price_version = (
            alias,
            priced_models,
            price_version,
        )
        self.credentials = credentials
        self.aliases = aliases
        self.litellm_version = litellm_version

    def rejected_before_upstream(self, kwargs: dict, success: bool) -> bool:
        """Use the server-stamped proxy gate marker, never status/text heuristics."""
        return not success and kwargs.get("litellm_no_upstream_llm_call") is True

    def project(
        self, kwargs: dict, response: object, start: object, end: object, success: bool
    ) -> Event:
        """Project approved D02 markers; other unproven outcomes stay unknown."""
        call_id = str(UUID(str(kwargs.get("litellm_call_id"))))
        params = kwargs.get("litellm_params") or {}
        metadata = get(params, "metadata") or kwargs.get("metadata") or {}
        proxy_request = (
            get(params, "proxy_server_request")
            or kwargs.get("proxy_server_request")
            or {}
        )
        headers = get(proxy_request, "headers", {}) or {}
        request_body = get(proxy_request, "body", {}) or {}
        alias = self.alias
        if self.aliases:
            requested = get(request_body, "model") or get(metadata, "model_group")
            if requested not in self.aliases:
                raise ValueError("unknown_request_model_alias")
            alias = requested
        # Header names in LiteLLM proxy_server_request are normalized to lowercase.
        labels = Labels(
            label(get(headers, "x-axi-client"), self.credentials),
            label(
                get(
                    metadata,
                    "client_label",
                    get(get(request_body, "metadata"), "client_label"),
                ),
                self.credentials,
            ),
        )
        standard = kwargs.get("standard_logging_object") or {}
        value = kwargs.get("response_cost", get(standard, "response_cost"))
        model = kwargs.get("model")
        disconnected = stream_disconnected(kwargs)
        value_usage = get(response, "usage")
        source: UsageSource = "estimated" if disconnected else "unknown"
        if (
            self.litellm_version == "1.106.5"
            and success
            and kwargs.get("stream") in (False, None)
            and (count(get(value_usage, "prompt_tokens")) or 0) > 0
            and (count(get(value_usage, "total_tokens")) or 0) > 0
        ):
            source = "reported"
        usage = usage_projection(value_usage, source)
        cost = (
            amount(value)
            if success
            and model in self.priced_models
            and usage.usage_source != "missing"
            else None
        )
        # A framework-default zero without proven usage is not evidence of free usage.
        if cost == 0 and usage.usage_source != "reported":
            cost = None
        # No key_alias is trustworthy in the approved master-key-only mode.
        duration = None
        start_utc, end_utc = utc_time(start), utc_time(end)
        if start_utc is not None and end_utc is not None:
            duration = max(0.0, (end_utc - start_utc).total_seconds() * 1000)
        exception = kwargs.get("exception")
        status = get(exception, "status_code")
        error_category = (
            None
            if success
            else (
                "upstream_http"
                if type(status) is int and status >= 400
                else "provider_or_transport"
            )
        )
        return Event(
            call_id,
            start_utc.isoformat() if start_utc is not None else None,
            end_utc.isoformat() if end_utc is not None else None,
            duration,
            labels,
            None,
            alias,
            kwargs.get("stream") is True,
            "success" if success else "error",
            "client_disconnected" if disconnected else "unknown",
            usage,
            Cost(
                cost,
                "USD" if cost is not None else None,
                "litellm_builtin" if cost is not None else None,
                self.price_version if cost is not None else None,
            ),
            error_category,
        )
