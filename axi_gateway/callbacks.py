"""The production callback class; exact class reused by all Proxy contracts."""

import atexit
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from axi_gateway.collector import Collector
from axi_gateway.events import Projector
from axi_gateway.sinks import DevelopmentJsonlSink
from axi_gateway.storage import SQLiteSink


class GatewayCallback(CustomLogger):
    """Logging hooks only; no request, raw transport or stream interception."""

    def __init__(self, collector: Collector) -> None:
        super().__init__()
        self.collector = collector

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: object,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """Queue one success observation; client/source remain conservative."""
        self.collector.submit(kwargs, response_obj, start_time, end_time, True)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: object,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """Queue the SDK failure once, without a second Proxy failure event."""
        self.collector.submit(kwargs, response_obj, start_time, end_time, False)


def create_callback() -> GatewayCallback:
    """Build a replaceable collector from launcher-controlled metadata and paths."""
    settings = json.loads(os.environ["AXI_COLLECTOR_SETTINGS"])
    projector = Projector(
        settings["alias"],
        set(settings["priced_models"]),
        settings["price_version"],
        (
            os.environ.get("UPSTREAM_API_KEY", ""),
            os.environ.get("LITELLM_MASTER_KEY", ""),
        ),
        litellm_version=settings["litellm_version"],
        trusted_identity_mode=settings.get("trusted_identity_mode", False),
        aliases=tuple(settings.get("aliases", ())) if settings["alias"] is None else (),
    )
    collector = Collector(
        projector,
        SQLiteSink(Path(settings["database_path"])),
        settings["queue_capacity"],
        drain_timeout=settings["drain_timeout"],
        debug_sink=DevelopmentJsonlSink(Path(settings["debug_path"]))
        if settings["debug_path"]
        else None,
    )
    atexit.register(collector.close)
    return GatewayCallback(collector)


callback = create_callback()
