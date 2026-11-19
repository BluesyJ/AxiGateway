"""Run the retained offline contracts without importing the legacy application."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from axi_gateway.bootstrap import verify_environment  # noqa: E402
from axi_gateway.launcher import loopback_guard  # noqa: E402


def main() -> int:
    """Validate the separate proxy environment, then run only synthetic tests."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ.pop("UPSTREAM_API_KEY", None)
    os.environ.pop("LOG_CONTENT", None)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    verify_environment(ROOT, ROOT / ".venv-litellm")
    # PyYAML is already hash-locked in the proxy environment. The development
    # interpreter's existing packages take priority; no .pth files are executed.
    sys.path.append(str(ROOT / ".venv-litellm/Lib/site-packages"))
    sys.addaudithook(loopback_guard)
    (ROOT / ".cache").mkdir(exist_ok=True)
    if args.contracts:
        from tests_p1.run_contracts import main as contracts

        result = asyncio.run(contracts(ROOT / ".cache/contracts.json", p2_privacy=True))
        return 0 if result["passed"] else 1

    import pytest

    return pytest.main(
        ["-q", "--basetemp=.cache/unit", "--junitxml=.cache/core-tests.xml"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
