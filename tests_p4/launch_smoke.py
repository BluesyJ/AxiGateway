"""Same validated production launcher/callback; ephemeral probe credentials only."""

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from axi_gateway import secrets  # noqa: E402
from axi_gateway.launcher import main  # noqa: E402

MASTER = os.environ["P4_MASTER_KEY"]


def ephemeral_key(path: Path) -> str:
    """Use only the synthetic child-environment master, without persisting it."""
    return MASTER


def attach(fake_billing_time: datetime | None = None) -> None:
    """Use the actual GatewayCallback without a substitute logger subclass."""
    import yaml

    from tests_p4.policy import check_no_retries_or_fallbacks

    runtime = Path(sys.argv[sys.argv.index("--data-dir") + 1]) / "proxy.runtime.yaml"
    check_no_retries_or_fallbacks(yaml.safe_load(runtime.read_bytes()))
    if "--offline" in sys.argv:
        from litellm.proxy.proxy_server import app

        from tests_p4.cost_expectation import BILLING_MOMENTS
        from tests_p4.fake_billing import FakeBillingTime

        app.add_middleware(
            FakeBillingTime,
            moment=fake_billing_time or BILLING_MOMENTS["standard"],
        )
    elif fake_billing_time is not None:
        raise ValueError("fake_clock_requires_offline")


if __name__ == "__main__":
    if "--offline" not in sys.argv or "--run-real" in sys.argv:
        raise SystemExit(2)
    secrets.master_key = ephemeral_key
    raise SystemExit(main(before_server=attach))
