"""Replaceable sink contracts; JSONL remains an optional NON-PRODUCT developer aid."""

import json
from pathlib import Path
from typing import Protocol

from axi_gateway.events import Event


class Sink(Protocol):
    """A blocking sink may run only on the collector's consumer thread."""

    def write(self, event: Event) -> None:
        """Write one allowlisted event or raise for the consumer to count."""
        ...


class BatchSink(Protocol):
    """The writer commits allowlisted batches with counters and gap windows."""

    def write_batch(
        self, events: list[Event], counters: dict, gaps: list[dict]
    ) -> None:
        """Commit the complete batch atomically or raise for loss accounting."""
        ...

    def close(self) -> None:
        """Release writer-owned resources."""
        ...


class DevelopmentJsonlSink:
    """NON-PRODUCT: append-only developer output, no durability/retention guarantees."""

    kind = "development_jsonl_non_product"

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: Event) -> None:
        """Write only the declared Event schema; never accept arbitrary dictionaries."""
        if not isinstance(event, Event):
            raise TypeError("Event required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf8") as handle:
            handle.write(
                json.dumps(event.to_dict(), ensure_ascii=False, allow_nan=False) + "\n"
            )
