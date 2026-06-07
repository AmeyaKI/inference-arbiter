"""Request ID and timing middleware."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_start = time.perf_counter()
        if "X-Request-ID" not in request.headers:
            request.state.request_id = str(uuid.uuid4())
        else:
            request.state.request_id = request.headers["X-Request-ID"]
        response = await call_next(request)
        if "X-Request-ID" not in response.headers:
            response.headers["X-Request-ID"] = request.state.request_id
        return response
