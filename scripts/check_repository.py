"""Check Git-index hygiene without opening ignored credentials or runtime data."""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from axi_gateway.bootstrap import ASSETS, pins  # noqa: E402

PRIVATE_DIRS = {
    ".cache",
    "data",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
}
PRIVATE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".dpapi",
    ".log",
    ".zip",
    ".whl",
    ".pem",
    ".pfx",
    ".p12",
    ".key",
    ".pyc",
}
SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"
    r"|sk-[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
LINK = re.compile(r"\]\(([^)]+)\)")


def path_is_private(name: str) -> bool:
    """Reject runtime directories and credential containers even if force-added."""
    path = PurePosixPath(name)
    return (
        any(part in PRIVATE_DIRS or part.startswith(".venv") for part in path.parts)
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.suffix.lower() in PRIVATE_SUFFIXES
        or path.name.endswith(("-wal", "-shm"))
    )


def inspect_text(name: str, content: str, members: set[str]) -> list[str]:
    """Return controlled categories only; never return matched secret text."""
    errors = []
    if SECRET.search(content):
        errors.append("credential_pattern")
    if name.endswith(".md"):
        for target in LINK.findall(content):
            target = target.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("https://", "http://", "mailto:")):
                continue
            destination = (ROOT / name).parent / unquote(target)
            try:
                relative = destination.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                errors.append("external_local_link")
                continue
            if relative not in members and not any(
                member.startswith(relative.rstrip("/") + "/") for member in members
            ):
                errors.append("missing_local_link")
    return sorted(set(errors))


def main() -> int:
    """Scan staged files, links and immutable dependency assets."""
    if not (ROOT / ".git").exists():
        print(json.dumps({"passed": False, "reason": "local_git_repository_required"}))
        return 1
    names = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode("utf8")
        .strip("\0")
        .split("\0")
    )
    members = set(names)
    failures = []
    for name in names:
        if path_is_private(name):
            failures.append({"path": name, "reason": "private_file"})
            continue  # Do not read force-added private data.
        content = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
        if name.startswith("assets/tokenizers/") and Path(name).name in ASSETS:
            if hashlib.sha256(content).hexdigest() != ASSETS[Path(name).name]:
                failures.append({"path": name, "reason": "asset_hash"})
            continue
        try:
            decoded = content.decode("utf8")
        except UnicodeDecodeError:
            failures.append({"path": name, "reason": "unexpected_binary"})
            continue
        for reason in inspect_text(name, decoded, members):
            failures.append({"path": name, "reason": reason})
    pins(ROOT)
    print(
        json.dumps({"passed": not failures, "files": len(names), "failures": failures})
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
