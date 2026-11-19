"""Synthetic-only observations attached to the actual GatewayCallback instance."""

import atexit
import json
import logging
import os
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from axi_gateway.events import Event, Projector, get


def signals(value: object) -> dict[str, object]:
    """Return only marker booleans and numeric status; never arbitrary error text."""
    found_499 = False
    disconnected = False
    inspected = 0

    def walk(node: object, depth: int = 0) -> None:
        nonlocal found_499, disconnected, inspected
        if depth > 4 or inspected > 200:
            return
        inspected += 1
        if type(node) is int:
            found_499 |= node == 499
        elif isinstance(node, str):
            disconnected |= "clientdisconnected" in node.lower().replace("_", "")
            found_499 |= node == "499"
        elif isinstance(node, Exception):
            disconnected |= type(node).__name__ == "ClientDisconnected"
            walk(getattr(node, "status_code", None), depth + 1)
        elif isinstance(node, dict):
            for key, item in list(node.items())[:100]:
                if not isinstance(key, str) or any(
                    w in key.lower()
                    for w in (
                        "key",
                        "header",
                        "metadata",
                        "content",
                        "message",
                        "body",
                        "prompt",
                        "input",
                        "output",
                    )
                ):
                    continue
                if any(
                    w in key.lower()
                    for w in (
                        "status",
                        "exception",
                        "error",
                        "disconnect",
                        "hidden",
                        "standard_logging",
                    )
                ):
                    walk(item, depth + 1)

    walk(value)
    return {
        "has_499": found_499,
        "has_client_disconnected": disconnected,
        "inspected_nodes": inspected,
    }


def attach(directory: Path) -> None:
    """Keep the production callback class; change only synthetic test dependencies."""
    from axi_gateway.callbacks import callback

    observations: queue.Queue[dict[str, Any] | None] = queue.Queue(256)
    path = directory / "disconnect-observations.jsonl"

    def writer() -> None:
        with path.open("w", encoding="utf8") as handle:
            while True:
                item = observations.get()
                if item is None:
                    return
                handle.write(json.dumps(item) + "\n")
                handle.flush()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    original_factory = logging.getLogRecordFactory()

    def record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = original_factory(*args, **kwargs)
        if record.exc_info and record.levelno >= logging.ERROR:
            exc = record.exc_info[1]
            observations.put_nowait(
                {
                    "case": "p1-internal-exception",
                    "exception_type": type(exc).__name__,
                    "attribute_name": getattr(exc, "name", None)
                    if isinstance(exc, AttributeError)
                    else None,
                    "frames": [
                        {
                            "file": Path(frame.filename).name,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                        for frame in traceback.extract_tb(record.exc_info[2])[-8:]
                    ],
                }
            )
        return record

    logging.setLogRecordFactory(record_factory)
    logging.disable(logging.NOTSET)
    os.environ["LITELLM_LOG"] = "ERROR"

    class ProbeProjector(Projector):
        def rejected_before_upstream(self, kwargs: dict, success: bool) -> bool:
            rejected = super().rejected_before_upstream(kwargs, success)
            if rejected:
                observations.put_nowait(
                    {
                        "case": "p11-pre-upstream-rejection",
                        "server_no_upstream_marker": True,
                        "success_callback": success,
                        "callback_class": type(callback).__name__,
                    }
                )
            return rejected

        def project(
            self,
            kwargs: dict,
            response: object,
            start: object,
            end: object,
            success: bool,
        ) -> Event:
            params = kwargs.get("litellm_params") or {}
            meta = get(params, "metadata") or kwargs.get("metadata") or {}
            case = get(meta, "case_id")
            if not isinstance(case, str) or not case.startswith("p1-"):
                case = "p1-unlabelled"
            exception = kwargs.get("exception")
            standard = kwargs.get("standard_logging_object") or {}
            error_info = get(standard, "error_information") or {}
            observations.put_nowait(
                {
                    "case": case,
                    "success_callback": success,
                    "callback_class": type(callback).__name__,
                    "callback_mro": [c.__name__ for c in type(callback).__mro__],
                    "kwargs": signals(kwargs),
                    "standard": signals(kwargs.get("standard_logging_object")),
                    "standard_error_code_499": get(error_info, "error_code")
                    in (499, "499"),
                    "standard_error_class_client_disconnected": get(
                        error_info, "error_class"
                    )
                    == "ClientDisconnected",
                    "params_metadata_client_disconnected": get(
                        meta, "client_disconnected"
                    )
                    is True,
                    "hidden": signals(get(response, "_hidden_params")),
                    "exception_is_client_disconnected": type(exception).__name__
                    == "ClientDisconnected",
                }
            )
            if case == "p1-observer-fault":
                raise RuntimeError("P1 synthetic observation error")
            return super().project(kwargs, response, start, end, success)

    original = callback.collector.projector
    callback.collector.projector = ProbeProjector(
        original.alias,
        original.priced_models,
        original.price_version,
        original.credentials,
        aliases=original.aliases,
        litellm_version=original.litellm_version,
    )
    sink = callback.collector.sink

    class SlowSink:
        def write_batch(
            self, events: list[Event], counters: dict, gaps: list[dict]
        ) -> None:
            if any(event.client_label.header == "p1-slow-sink" for event in events):
                time.sleep(1.5)
            sink.write_batch(events, counters, gaps)

        def close(self) -> None:
            sink.close()

    callback.collector.sink = SlowSink()

    def close() -> None:
        observations.put_nowait(None)
        thread.join(1)

    atexit.register(close)
