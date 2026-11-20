"""Start only the offline demo management API; no identity issuer or login UI."""

import argparse
import contextlib
import json
import logging
import os
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from axi_gateway.bootstrap import verify_environment  # noqa: E402
from axi_gateway.launcher import Quiet, loopback_guard  # noqa: E402


def main() -> int:
    """Validate the unchanged runtime, then bind a single loopback worker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if not sys.flags.isolated or not sys.flags.no_site:
            raise ValueError
        if not 1 <= args.port <= 65535 or args.port in {4000, 8787, 8788}:
            raise ValueError
        venv = ROOT / ".venv-litellm"
        verify_environment(ROOT, venv)
        sys.addaudithook(loopback_guard)
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
        sys.prefix = sys.exec_prefix = str(venv)
        site.addsitedir(str(venv / "Lib/site-packages"))
        from axi_gateway.access.identity import read_demo_config
        from axi_gateway.access.policy import PolicyStore
        from axi_gateway.access.server import create_app

        config = read_demo_config(args.config.resolve(), ROOT)
        store = PolicyStore(config.database, config.models)
        store.apps()
        app = create_app(store, config.admin_verifier, config.admins, args.port)
        if args.check:
            print(json.dumps({"event": "demo_management_preflight_ok"}))
            return 0
        logging.disable(logging.CRITICAL)
        print(
            json.dumps(
                {
                    "event": "demo_management_starting",
                    "host": "127.0.0.1",
                    "port": args.port,
                }
            ),
            flush=True,
        )
        with contextlib.redirect_stdout(Quiet()), contextlib.redirect_stderr(Quiet()):
            import uvicorn

            uvicorn.run(
                app,
                host="127.0.0.1",
                port=args.port,
                workers=1,
                proxy_headers=False,
                access_log=False,
                log_config=None,
            )
        return 0
    except (Exception, SystemExit):
        print(json.dumps({"event": "demo_management_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
