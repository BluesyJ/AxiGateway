"""Use: .venv-litellm/Scripts/python.exe -I -S scripts/start_p1.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axi_gateway.launcher import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
