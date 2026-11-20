"""Run a bounded offline identity/policy demonstration with no saved credentials."""

import argparse
import asyncio
import logging
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from axi_gateway.bootstrap import verify_environment  # noqa: E402
from axi_gateway.launcher import loopback_guard  # noqa: E402


def main() -> None:
    """Check the original runtime before importing dependencies or starting servers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rejections-only", action="store_true")
    args = parser.parse_args()
    if not sys.flags.isolated or not sys.flags.no_site:
        raise SystemExit("use_python_isolated_no_site")
    venv = ROOT / ".venv-litellm"
    verify_environment(ROOT, venv)
    sys.addaudithook(loopback_guard)
    sys.prefix = sys.exec_prefix = str(venv)
    site.addsitedir(str(venv / "Lib/site-packages"))
    logging.disable(logging.CRITICAL)
    from tests_p7.run_access import run

    asyncio.run(run(rejections_only=args.rejections_only))


if __name__ == "__main__":
    main()
