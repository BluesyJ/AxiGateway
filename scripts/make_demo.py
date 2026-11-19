"""Generate the isolated demo with the verified Windows runtime and no network."""

import runpy
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from axi_gateway.bootstrap import verify_environment  # noqa: E402
from axi_gateway.launcher import loopback_guard  # noqa: E402


def main() -> None:
    """Enable only checked runtime hooks before importing Windows ACL support."""
    if not sys.flags.isolated or not sys.flags.no_site:
        raise SystemExit("use_python_isolated_no_site")
    venv = ROOT / ".venv-litellm"
    verify_environment(ROOT, venv)
    sys.addaudithook(loopback_guard)
    sys.prefix = sys.exec_prefix = str(venv)
    site.addsitedir(str(venv / "Lib/site-packages"))
    runpy.run_module("p3_console.demo", run_name="__main__")


if __name__ == "__main__":
    main()
