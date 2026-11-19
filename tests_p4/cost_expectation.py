"""Independent Decimal oracle for the locked DeepSeek test price entries only."""

from datetime import UTC, datetime
from decimal import Decimal

BILLING_MOMENTS = {
    "standard": datetime(2026, 11, 4, 8, tzinfo=UTC),
    "off_peak": datetime(2026, 11, 4, 12, tzinfo=UTC),
}
RATE_KEYS = (
    "input_cost_per_token",
    "cache_read_input_token_cost",
    "output_cost_per_token",
)


def expected_cost(entry: dict, usage: dict, moment: datetime) -> dict:
    """Compute USD without calling LiteLLM's pricing functions or reading its output.

    Deliberately handle only the UTC weekday/window shape in the two locked entries;
    new pricing structures require explicit review instead of a guessed fallback.
    """
    if moment.tzinfo is None:
        raise ValueError("timezone_required")
    now = moment.astimezone(UTC)
    off_peak = entry.get("off_peak_pricing", {})
    if set(off_peak) - {*RATE_KEYS, "windows"}:
        raise ValueError("unsupported_test_price_schedule")
    active = False
    for rule in off_peak.get("windows", []):
        if set(rule) != {"weekdays", "hours_utc"}:
            raise ValueError("unsupported_test_price_window")
        if now.isoweekday() not in rule["weekdays"]:
            continue
        windows = rule["hours_utc"]
        for window in [windows] if isinstance(windows, str) else windows:
            start, end = (
                sum(int(n) * w for n, w in zip(t.split(":"), (60, 1), strict=True))
                for t in window.split("-")
            )
            minute = now.hour * 60 + now.minute
            active |= (
                start <= minute < end
                if start < end
                else minute >= start or minute < end
            )
    rates = {
        k: Decimal(str(off_peak.get(k, entry[k]) if active else entry[k]))
        for k in RATE_KEYS
    }
    prompt, output, total = (
        usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = usage.get("cached_tokens")
    reasoning = usage.get("reasoning_tokens")
    if reasoning is None:
        reasoning = (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens", 0
        )
    if not all(type(n) is int and n >= 0 for n in (prompt, output, total, hit)):
        raise ValueError("test_usage_required")
    if hit > prompt or total != prompt + output or not 0 <= reasoning <= output:
        raise ValueError("invalid_test_usage")
    miss = prompt - hit
    raw_miss = usage.get("prompt_cache_miss_tokens")
    if raw_miss is not None and raw_miss != miss:
        raise ValueError("inconsistent_cache_usage")
    components = (
        miss * rates[RATE_KEYS[0]],
        hit * rates[RATE_KEYS[1]],
        output * rates[RATE_KEYS[2]],
    )
    return {
        "period": "off_peak" if active else "standard",
        "billing_time_utc": now.isoformat(),
        "rates_usd_per_token": {k: float(v) for k, v in rates.items()},
        "uncached_input_usd": float(components[0]),
        "cached_input_usd": float(components[1]),
        "completion_including_reasoning_usd": float(components[2]),
        "expected_usd": float(sum(components)),
    }
