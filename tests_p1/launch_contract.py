"""Start the real P1 launcher with the same callback class for every fake case."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from axi_gateway.launcher import main  # noqa: E402


def probe() -> None:
    """Attach bounded metadata-only test instrumentation after static preflight."""
    from tests_p1.contract_probe import attach

    directory = Path(sys.argv[sys.argv.index("--data-dir") + 1])
    attach(directory)


if __name__ == "__main__":
    raise SystemExit(main(before_server=probe))
