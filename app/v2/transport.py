"""Bound v2 response backpressure without changing v1 transport behavior."""

import asyncio

from starlette.requests import ClientDisconnect

from app.v2.diagnostics import FailureEvent, record_failure


class V2Transport:
    def __init__(self, app, send_budget=0.5):
        self.app = app
        self.send_budget = send_budget

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in {"/v2/analyses", "/health/ready/v2"}:
            return await self.app(scope, receive, send)
        remaining = self.send_budget
        loop = asyncio.get_running_loop()

        async def bounded_send(message):
            nonlocal remaining
            started = loop.time()
            try:
                async with asyncio.timeout(remaining):
                    await send(message)
            finally:
                remaining -= loop.time() - started

        try:
            await self.app(scope, receive, bounded_send)
        except TimeoutError:
            # Headers may already have been sent. Never append a second JSON response.
            record_failure(FailureEvent.HTTP_SEND_TIMEOUT)
        except ClientDisconnect:
            record_failure(FailureEvent.HTTP_DISCONNECT)
        finally:
            if lease := scope.get("v2.admission"):
                lease.release()
