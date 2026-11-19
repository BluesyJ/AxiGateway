"""Metadata-only synthetic samples shared with the isolated crash child."""

from datetime import UTC, datetime
from uuid import uuid4

from axi_gateway.events import Event, Projector


def projector() -> Projector:
    """No price table or credentials are needed for storage-only samples."""
    return Projector("synthetic", set(), "unpriced")


def kwargs() -> dict:
    """Return a fresh synthetic call identifier, never a historical ID."""
    return {"litellm_call_id": str(uuid4()), "model": "synthetic"}


def sample() -> Event:
    """Create a cost-unknown event with no provider usage."""
    now = datetime.now(UTC)
    return projector().project(kwargs(), None, now, now, True)
