"""Independent expectations and time-boundary checks for the fixed local price map."""

import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from axi_gateway.launcher import proxy_configuration, read_settings
from tests_p4.cost_expectation import BILLING_MOMENTS, expected_cost

ROOT = Path(__file__).resolve().parents[1]
PRICES = json.loads(
    (
        ROOT
        / ".venv-litellm/Lib/site-packages/litellm"
        / "model_prices_and_context_window_backup.json"
    ).read_bytes()
)
USAGE = {
    "prompt_tokens": 20,
    "completion_tokens": 12,
    "total_tokens": 32,
    "prompt_cache_hit_tokens": 8,
    "prompt_cache_miss_tokens": 12,
    "reasoning_tokens": 6,
}


@pytest.mark.parametrize(
    "model,standard,off_peak",
    [
        ("deepseek-v4-pro", 0.000063712, 0.000031856),
        ("deepseek-flash", 0.000018048, 0.000009024),
    ],
)
def test_independent_costs(model: str, standard: float, off_peak: float) -> None:
    """Check literal miss*rate + hit*rate + output*rate totals, without vendor code."""
    entry = PRICES["deepseek/" + model]
    for period, expected in (("standard", standard), ("off_peak", off_peak)):
        result = expected_cost(entry, USAGE, BILLING_MOMENTS[period])
        assert result["period"] == period
        assert result["expected_usd"] == expected
        # Reasoning is already in completion: omitting its detail must not cut cost.
        assert (
            expected_cost(
                entry, {**USAGE, "reasoning_tokens": 0}, BILLING_MOMENTS[period]
            )["expected_usd"]
            == expected
        )
    # This is the pre-fix error: the standard-only expectation fails off-peak.
    assert (
        expected_cost(entry, USAGE, BILLING_MOMENTS["off_peak"])["expected_usd"]
        != standard
    )


@pytest.mark.parametrize(
    "moment,period",
    [
        ("2026-11-04T00:59:59+00:00", "off_peak"),
        ("2026-11-04T01:00:00+00:00", "standard"),
        ("2026-11-04T04:00:00+00:00", "off_peak"),
        ("2026-11-04T06:00:00+00:00", "standard"),
        ("2026-11-04T09:59:59+00:00", "standard"),
        ("2026-11-04T10:00:00+00:00", "off_peak"),
        ("2026-11-04T18:00:00+08:00", "off_peak"),
        ("2026-11-07T08:00:00+00:00", "off_peak"),
    ],
)
def test_price_window_boundaries(moment: str, period: str) -> None:
    """UTC, half-open endpoints, and full weekend days are distinct test cases."""
    for model in ("deepseek-v4-pro", "deepseek-flash"):
        assert (
            expected_cost(
                PRICES["deepseek/" + model], USAGE, datetime.fromisoformat(moment)
            )["period"]
            == period
        )


def test_real_path_has_no_fake_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real launch path rejects an injected fake clock and needs no clock module."""
    import yaml

    monkeypatch.setenv("P4_MASTER_KEY", "synthetic-test-only")
    launch = importlib.import_module("tests_p4.launch_smoke")
    config = proxy_configuration(
        read_settings(ROOT / "config/litellm.astrbot.example.yaml")
    )
    (tmp_path / "proxy.runtime.yaml").write_text(
        yaml.safe_dump(config), encoding="utf8"
    )
    monkeypatch.setattr(sys, "argv", ["launch_smoke.py", "--data-dir", str(tmp_path)])
    launch.attach()
    with pytest.raises(ValueError, match="fake_clock_requires_offline"):
        launch.attach(BILLING_MOMENTS["off_peak"])
