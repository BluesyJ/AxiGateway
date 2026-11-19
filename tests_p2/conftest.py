"""Write conclusion-only test evidence even when the planned suite fails."""

import json
from pathlib import Path

RESULTS: dict[str, dict] = {}


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    """Preserve aggregate P2 results, never failure messages or event bodies."""
    path = Path(__file__).resolve().parents[1] / ".cache/p2-unit-result.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps({"passed": exitstatus == 0, "cases": RESULTS}, indent=2),
        encoding="utf8",
    )
