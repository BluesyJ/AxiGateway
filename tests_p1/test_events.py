"""Privacy, provenance, arithmetic and loss handling invariants."""

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from axi_gateway.bootstrap import StartupError, check_hooks, pins, prepare_tokenizers
from axi_gateway.collector import Collector
from axi_gateway.events import Projector, stream_disconnected, usage_projection
from axi_gateway.sinks import DevelopmentJsonlSink

RAW = {
    "prompt_tokens": 20,
    "completion_tokens": 12,
    "total_tokens": 32,
    "prompt_tokens_details": {"cached_tokens": 8},
    "completion_tokens_details": {"reasoning_tokens": 6},
}


def projector() -> Projector:
    """Use synthetic credentials and a version-only price marker."""
    return Projector(
        "deepseek", {"deepseek-chat"}, "test-price-v1", ("sk-secret-sentinel",)
    )


def event_kwargs() -> dict:
    """Mimic the selected fields of a logging call, not its raw payload."""
    return {
        "litellm_call_id": str(uuid4()),
        "model": "deepseek-chat",
        "stream": False,
        "response_cost": 0.123,
        "litellm_params": {
            "metadata": {
                "client_label": "client-meta",
                "usage_source": "reported",
                "key_alias": "spoofed",
            },
            "proxy_server_request": {
                "headers": {
                    "x-axi-client": "client-header",
                    "authorization": "sk-secret-sentinel",
                }
            },
        },
    }


@pytest.mark.parametrize("source", ["unknown", "missing", "estimated", "derived"])
def test_unreported_discards_details(source: str) -> None:
    """All unreported usage must lose cache/reasoning detail, including zeros."""
    result = usage_projection(RAW, source)
    assert result.prompt_tokens == 20
    assert result.cache_hit_tokens is None and result.cache_miss_tokens is None
    assert result.reasoning_tokens is None


@pytest.mark.parametrize(
    "prompt,cached,expected",
    [
        (20, 8, 12),
        (0, 0, 0),
        (20, 20, 0),
        (20, 21, None),
        (None, 8, None),
        (20, None, None),
        (20, -1, None),
        (True, 0, None),
    ],
)
def test_derived_miss(prompt: object, cached: object, expected: int | None) -> None:
    """Derive only from proven reported, complete and valid operands."""
    result = usage_projection(
        {
            **RAW,
            "prompt_tokens": prompt,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
        "reported",
    )
    assert result.cache_miss_tokens == expected
    assert result.cache_miss_source == ("derived" if expected is not None else None)
    assert result.total_tokens == 32 and result.reasoning_tokens == 6


def test_provenance_labels_privacy_and_cost(tmp_path: Path) -> None:
    """Metadata cannot self-assert source, identity, model or stored content."""
    kwargs = event_kwargs()
    kwargs["messages"] = [{"content": "PRIVATE-PROMPT"}]
    response = {
        "usage": RAW,
        "choices": [
            {
                "message": {
                    "content": "PRIVATE-REPLY",
                    "reasoning_content": "PRIVATE-REASON",
                }
            }
        ],
    }
    now = datetime.now(UTC)
    event = projector().project(kwargs, response, now, now, True)
    assert event.usage.usage_source == "unknown"
    assert event.client_outcome == "unknown" and event.key_alias is None
    assert (
        event.client_label.header == "client-header"
        and event.client_label.metadata == "client-meta"
    )
    assert event.cost.amount == 0.123 and event.cost.estimate_not_invoice
    assert projector().project(kwargs, None, now, now, False).cost.amount is None
    assert (
        projector()
        .project({**kwargs, "response_cost": 0}, response, now, now, True)
        .cost.amount
        is None
    )
    sink = DevelopmentJsonlSink(tmp_path / "events.jsonl")
    sink.write(event)
    text = sink.path.read_text()
    assert not any(
        s in text
        for s in ("PRIVATE-", "sk-secret-sentinel", "authorization", "spoofed")
    )
    kwargs["model"] = "unpriced-model"
    assert projector().project(kwargs, response, now, now, True).cost.amount is None
    kwargs["litellm_params"]["proxy_server_request"]["headers"]["x-axi-client"] = (
        "sk-secret-sentinel"
    )
    assert (
        projector().project(kwargs, response, now, now, True).client_label.header
        is None
    )
    assert json.loads(text)["schema_version"] == 0


def test_queue_and_sink_failures_do_not_block() -> None:
    """A blocked sink cannot block submission; lost events are counted."""
    entered, release = threading.Event(), threading.Event()

    class SlowFailingSink:
        def write(self, event: object) -> None:
            entered.set()
            release.wait(3)
            raise OSError("PRIVATE-SINK-ERROR")

    collector = Collector(projector(), SlowFailingSink(), capacity=1)
    collector.submit(event_kwargs(), {"usage": RAW}, None, None, True)
    assert entered.wait(1)
    start = time.monotonic()
    for _ in range(10):
        collector.submit(event_kwargs(), {"usage": RAW}, None, None, True)
    assert time.monotonic() - start < 0.5
    assert collector.metrics()["queue_full"] == 9
    collector.submit({}, None, None, None, False)
    assert collector.metrics()["projection_failed"] == 1
    release.set()
    collector.close()
    assert collector.metrics()["sink_failed"] == 2
    assert collector.metrics()["loss_count"] == 12


def test_shutdown_is_bounded() -> None:
    """Exit draining has a deadline even if a sink stops making progress."""
    release = threading.Event()

    class Blocked:
        def write(self, event: object) -> None:
            release.wait(5)

    collector = Collector(projector(), Blocked(), 1)
    collector.submit(event_kwargs(), {"usage": RAW}, None, None, True)
    start = time.monotonic()
    collector.close(0.02)
    assert time.monotonic() - start < 0.5
    collector.submit(event_kwargs(), None, None, None, False)
    assert collector.metrics()["rejected_after_close"] == 1
    release.set()
    collector.close()


def test_rejects_unreviewed_hooks_before_execution(tmp_path: Path) -> None:
    """A hostile .pth is never executed by the standard-library preflight."""
    site = tmp_path / "site"
    site.mkdir()
    marker = tmp_path / "executed"
    (site / "evil.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).touch()"
    )
    with pytest.raises(StartupError, match="unexpected_startup_hook"):
        check_hooks(site, tmp_path, {})
    assert not marker.exists()


def test_invalid_assets_never_repaired(tmp_path: Path) -> None:
    """Absent or changed assets fail without any download fallback."""
    with pytest.raises(StartupError, match="tokenizer_asset_invalid"):
        prepare_tokenizers(tmp_path, tmp_path / "cache")


def test_exact_lock() -> None:
    """The P1 pin set is precisely the existing 117-package lock."""
    locked = pins(Path(__file__).resolve().parents[1])
    assert len(locked) == 117 and locked["litellm"] == "1.106.5"
    assert "litellm-enterprise" not in locked


@pytest.mark.parametrize(
    "marker",
    [
        {"standard_logging_object": {"error_information": {"error_code": "499"}}},
        {
            "standard_logging_object": {
                "error_information": {"error_class": "ClientDisconnected"}
            }
        },
        {"litellm_params": {"metadata": {"client_disconnected": True}}},
    ],
)
def test_d02_stream_only(marker: dict) -> None:
    """Approved server markers classify only streams, including absent usage."""
    kwargs = {**event_kwargs(), **marker, "stream": True}
    event = projector().project(kwargs, {"usage": RAW}, None, None, True)
    assert event.client_outcome == "client_disconnected"
    assert event.usage.usage_source == "estimated"
    assert event.usage.cache_hit_tokens is event.usage.reasoning_tokens is None
    assert (
        projector().project(kwargs, None, None, None, True).usage.usage_source
        == "estimated"
    )
    kwargs["stream"] = False
    event = projector().project(kwargs, {"usage": RAW}, None, None, True)
    assert event.client_outcome == "unknown" and event.usage.usage_source == "unknown"


def test_raw_client_markers_are_not_read() -> None:
    """Raw request metadata, body and error text are never D02 signals."""
    kwargs = {
        **event_kwargs(),
        "stream": True,
        "metadata": {"client_disconnected": True},
        "exception": RuntimeError("ClientDisconnected 499"),
    }
    kwargs["litellm_params"]["proxy_server_request"]["body"] = {
        "metadata": {"client_disconnected": True},
        "standard_logging_object": {"error_information": {"error_code": "499"}},
    }
    assert not stream_disconnected(kwargs)


def test_proxy_gate_is_counted_without_an_event(tmp_path: Path) -> None:
    """A server-stamped gate refusal is not a model error or a lost event."""
    path = tmp_path / "events.jsonl"
    collector = Collector(projector(), DevelopmentJsonlSink(path))
    collector.submit({"litellm_no_upstream_llm_call": True}, None, None, None, False)
    collector.close()
    assert collector.metrics()["auth_rejected"] == 1
    assert collector.metrics()["accepted"] == collector.metrics()["loss_count"] == 0
    assert not path.exists()
    kwargs = event_kwargs()
    kwargs["metadata"] = {"litellm_no_upstream_llm_call": True}
    kwargs["exception"] = RuntimeError("401 auth rejected")
    assert not projector().rejected_before_upstream(kwargs, False)
