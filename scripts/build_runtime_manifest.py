"""Create the reviewed runtime inventory from the existing unchanged wheel lock."""

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from axi_gateway.bootstrap import LOCK_RELATIVE, LOCK_SHA256, pins  # noqa: E402


def main() -> None:
    """Read only locked OSS distribution payloads; never install or execute them."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    args = parser.parse_args()
    versions = pins(ROOT)
    files = {}
    wheels = []
    for line in (ROOT / LOCK_RELATIVE).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        pin, hasharg = line.split()
        name, version = pin.split("==")
        matches = list(
            args.wheelhouse.glob(f"{name.replace('-', '_')}-{version}-*.whl")
        )
        if len(matches) != 1:
            raise RuntimeError("missing or ambiguous wheel")
        path = matches[0]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != hasharg.split(":")[1] or name == "litellm-enterprise":
            raise RuntimeError("untrusted wheel")
        wheels.append({"filename": path.name, "sha256": digest})
        with ZipFile(path) as wheel:
            for member in wheel.namelist():
                parts = PurePosixPath(member).parts
                if member.endswith("/") or "enterprise" in parts:
                    continue
                if member.endswith(".dist-info/RECORD"):
                    continue
                if parts[0].endswith(".data"):
                    if parts[1] not in {"purelib", "platlib"}:
                        continue
                    member_target = "/".join(parts[2:])
                else:
                    member_target = member
                value = hashlib.sha256(wheel.read(member)).hexdigest()
                if member_target in files and files[member_target] != value:
                    raise RuntimeError("conflicting wheel payload")
                files[member_target] = value
    target = ROOT / "supply-chain/runtime-manifest.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "lock_sha256": LOCK_SHA256,
                "versions": versions,
                "wheels": wheels,
                "files": files,
            },
            indent=2,
        ),
        encoding="utf8",
    )
    print(json.dumps({"packages": len(versions), "files": len(files)}))


if __name__ == "__main__":
    main()
