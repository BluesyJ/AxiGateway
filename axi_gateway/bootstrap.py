"""Static, standard-library-only checks before executing dependency startup hooks."""

import hashlib
import importlib.metadata
import json
import platform
import re
import sys
from pathlib import Path

LOCK_RELATIVE = "spikes/litellm_p0/requirements-win-py313.lock.txt"
LOCK_SHA256 = "468edc1de68db234a938c8b12f4a3a091d5e2529e3ef32784741b030dc8ba113"
CACHE_MEMBER = (
    "litellm/litellm_core_utils/tokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
)
ASSETS = {
    "9b5ad71b2ce5302211f9c61530b329a4922fc6a4": (
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    ),
    "fb374d419588a4632f3f557e76b4b70aebbca790": (
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
    ),
}


class StartupError(Exception):
    """Controlled startup error: only an allowlisted code may reach the console."""


def sha256(path: Path) -> str:
    """Hash a local file without loading executable code."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pins(root: Path) -> dict[str, str]:
    """Require the unchanged 117-package hash lock."""
    path = root / LOCK_RELATIVE
    if sha256(path) != LOCK_SHA256:
        raise StartupError("lock_changed")
    result = {}
    for line in path.read_text(encoding="utf8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([\w.-]+)==([\w.+-]+) --hash=sha256:([a-f0-9]{64})", line
        )
        if not match:
            raise StartupError("invalid_lock")
        name, version, _ = match.groups()
        result[name.lower().replace("_", "-")] = version
    if len(result) != 117 or result.get("litellm") != "1.106.5":
        raise StartupError("invalid_lock")
    return result


def check_hooks(site: Path, root: Path, files: dict[str, str]) -> None:
    """Allow only the previously reviewed pywin32 startup files and their bytes."""
    hooks = []
    for path in site.rglob("*"):
        if path.is_file() and (
            path.suffix.lower() in {".pth", "._pth"}
            or path.name.lower() in {"sitecustomize.py", "usercustomize.py"}
        ):
            hooks.append(path.relative_to(site).as_posix())
    if sorted(hooks) != ["pywin32.pth"]:
        raise StartupError("unexpected_startup_hook")
    for name in ("pywin32.pth", "win32/lib/pywin32_bootstrap.py"):
        if name not in files or sha256(site / name) != files[name]:
            raise StartupError("startup_hook_changed")
    for directory in (
        root,
        root / "scripts",
        site.parent,
        site.parent.parent / "Scripts",
    ):
        for path in directory.glob("*"):
            if path.name.lower() in {
                "sitecustomize.py",
                "usercustomize.py",
            } or path.suffix.lower() in {".pth", "._pth"}:
                raise StartupError("unexpected_external_hook")


def verify_environment(root: Path, venv: Path) -> dict[str, object]:
    """Validate platform, distributions, reviewed file hashes and hook allowlist."""
    if (
        sys.version_info[:2] != (3, 13)
        or platform.system() != "Windows"
        or platform.machine().lower() not in {"amd64", "x86_64"}
    ):
        raise StartupError("unsupported_platform")
    expected = pins(root)
    site = venv / "Lib/site-packages"
    installed = {
        dist.metadata["Name"].lower().replace("_", "-"): dist.version
        for dist in importlib.metadata.distributions(path=[str(site)])
    }
    if "litellm-enterprise" in installed or (site / "litellm_enterprise").exists():
        raise StartupError("enterprise_installed")
    if installed != expected:
        raise StartupError("environment_not_locked")
    manifest = json.loads(
        (root / "supply-chain/runtime-manifest.json").read_text(encoding="utf8")
    )
    if manifest["lock_sha256"] != LOCK_SHA256 or manifest["versions"] != expected:
        raise StartupError("manifest_mismatch")
    files = manifest["files"]
    check_hooks(site, root, files)
    missing_cache = False
    for name, digest in files.items():
        path = site / name
        if name == CACHE_MEMBER and not path.exists():
            missing_cache = (
                True  # Explained inert cache defect; no executable exception.
            )
            continue
        if not path.is_file() or sha256(path) != digest:
            raise StartupError("package_integrity_failed")
    # Detect extra importable payloads omitted from RECORD/manifests as well.
    for path in site.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".pyd", ".dll", ".pth"}:
            if path.relative_to(site).as_posix() not in files:
                raise StartupError("unexpected_package_payload")
    return {
        "packages": len(expected),
        "verified_files": len(files),
        "explained_tokenizer_cache_absent": missing_cache,
        "litellm_version": expected["litellm"],
    }


def prepare_tokenizers(root: Path, directory: Path) -> None:
    """Copy only verified bundled static assets; never download or repair online."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, digest in ASSETS.items():
        source = root / "assets/tokenizers" / name
        if not source.is_file() or sha256(source) != digest:
            raise StartupError("tokenizer_asset_invalid")
        target = directory / name
        if target.exists() and sha256(target) != digest:
            raise StartupError("tokenizer_cache_invalid")
        if not target.exists():
            target.write_bytes(source.read_bytes())
