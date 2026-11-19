"""Test-only billing clock; never installed by a real or production launcher."""

from collections.abc import Callable
from datetime import datetime


class FakeBillingTime:
    """Pin the existing vendor context per request, without changing vendor files."""

    def __init__(self, app: Callable, moment: datetime) -> None:
        self.app, self.moment = app, moment

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        from litellm._internal_context import pinned_billing_time

        with pinned_billing_time(self.moment):
            await self.app(scope, receive, send)
