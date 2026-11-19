"""Verify the reviewable source snapshot; ignore local environments and test data."""

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "docs/SNAPSHOT_MANIFEST.json"
IGNORED = {
    ".git",
    ".cache",
    "data",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
}


def source_files() -> list[str]:
    """Enumerate source only, never traverse private runtime directories."""
    files = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [
            name
            for name in dirs
            if name not in IGNORED and not name.startswith(".venv")
        ]
        for name in names:
            relative = (Path(base) / name).relative_to(ROOT).as_posix()
            if name.startswith(".env") and name != ".env.example":
                continue
            if relative != MANIFEST:
                files.append(relative)
    return sorted(files)


def main() -> int:
    """Check exact membership and SHA-256 without exposing file contents."""
    manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf8"))
    expected = {row["path"]: row["sha256"] for row in manifest["files"]}
    actual = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in source_files()
    }
    passed = actual == expected
    print(json.dumps({"passed": passed, "source_files": len(actual)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
