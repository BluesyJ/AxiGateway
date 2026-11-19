"""Synthetic crash child: commit once, leave a second real transaction pending."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axi_gateway.storage import SQLiteSink  # noqa: E402
from tests_p2.samples import sample  # noqa: E402

sink = SQLiteSink(Path(sys.argv[1]))
sink.write_batch([sample()], {"written": 1}, [])
connection = sink.connection
connection.execute("BEGIN IMMEDIATE")
connection.execute("INSERT INTO counters VALUES ('uncommitted',1)")
connection.execute("UPDATE events SET model_alias='uncommitted'")
print("transaction_pending", flush=True)
time.sleep(30)
