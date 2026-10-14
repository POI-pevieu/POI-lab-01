"""Pure ASGI middleware adding ``X-Process-Time`` (seconds, float).

Implemented as raw ASGI instead of ``BaseHTTPMiddleware`` to avoid the extra
task/stream overhead that the latter adds to every request.
"""
from __future__ import annotations

import time


class ProcessTimeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                elapsed = time.perf_counter() - start
                headers = list(message.get("headers", []))
                headers.append((b"x-process-time", f"{elapsed:.6f}".encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
