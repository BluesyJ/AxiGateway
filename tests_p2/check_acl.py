"""Check synthetic test ACLs without persisting account names or SIDs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import win32api  # noqa: E402
import win32security  # noqa: E402

from axi_gateway.secrets import private_event_directory  # noqa: E402
from axi_gateway.storage import SQLiteSink  # noqa: E402
from tests_p2.samples import sample  # noqa: E402

directory = Path(sys.argv[1])
private_event_directory(directory)
sink = SQLiteSink(directory / "events.sqlite3")
sink.write_batch([sample()], {}, [])
token = win32security.OpenProcessToken(
    win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
)
try:
    user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
finally:
    token.Close()
paths = [directory, *directory.iterdir()]
passed = len(paths) == 4
for path in paths:
    dacl = win32security.GetFileSecurity(
        str(path), win32security.DACL_SECURITY_INFORMATION
    ).GetSecurityDescriptorDacl()
    passed &= dacl is not None and dacl.GetAceCount() == 1
    if dacl is not None:
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            passed &= (
                ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE and ace[2] == user
            )
print(
    json.dumps(
        {"passed": passed, "files_checked": len(paths), "current_user_only": passed}
    )
)
sink.close()
raise SystemExit(0 if passed else 1)
