"""Windows current-user DPAPI storage; never print a credential."""

import re
import secrets
from pathlib import Path

from axi_gateway.bootstrap import StartupError


def private_event_directory(path: Path) -> None:
    """Protect SQLite, WAL and SHM by an inheritable current-user-only directory ACL."""
    import win32api
    import win32security

    try:
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.is_junction():
            raise ValueError("event directory must be local")
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        try:
            sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
        finally:
            token.Close()
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;OICI;FA;;;{sid})", 1
        )
        win32security.SetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION - 0x80000000, descriptor
        )
        # Existing SQLite sidecars are tightened too; never follow arbitrary links.
        file_descriptor = (
            win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f"D:P(A;;FA;;;{sid})", 1
            )
        )
        for name in (
            "events.sqlite3",
            "events.sqlite3-wal",
            "events.sqlite3-shm",
            "development-events.jsonl",
        ):
            child = path / name
            if child.exists():
                if child.is_symlink():
                    raise ValueError("event file must be local")
                win32security.SetFileSecurity(
                    str(child),
                    win32security.DACL_SECURITY_INFORMATION - 0x80000000,
                    file_descriptor,
                )
    except Exception:
        raise StartupError("event_directory_unavailable") from None


def master_key(path: Path) -> str:
    """Create/reuse an encrypted local master key with a protected current-user ACL."""
    import win32api
    import win32crypt
    import win32security

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not path.exists():
            value = "sk-axi-" + secrets.token_urlsafe(48)
            blob = win32crypt.CryptProtectData(
                value.encode(), "AxiGateway master key", None, None, None, 1
            )
            try:
                with path.open("xb") as handle:
                    handle.write(blob)
            except FileExistsError:
                pass
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        try:
            sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
        finally:
            token.Close()
        descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;;FA;;;SY)(A;;FA;;;{sid})", 1
        )
        win32security.SetFileSecurity(
            # pywin32 312 parses SECURITY_INFORMATION as a signed C long.
            str(path),
            win32security.DACL_SECURITY_INFORMATION - 0x80000000,
            descriptor,
        )
        _, clear = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)
        value = clear.decode("ascii")
        if not re.fullmatch(r"sk-axi-[A-Za-z0-9_-]{64}", value):
            raise ValueError("invalid local key")
        return value
    except Exception:
        raise StartupError("local_key_unavailable") from None


def copy_key(value: str) -> None:
    """Copy only on explicit local CLI request; no stdout or subprocess argument."""
    import win32clipboard

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(value)
    finally:
        win32clipboard.CloseClipboard()
