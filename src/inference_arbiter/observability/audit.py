"""Per-decision audit trail store."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from inference_arbiter.routing.context import RequestContext


class AuditStore:
    def __init__(self, max_records: int = 10_000) -> None:
        self._max = max_records
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def put(self, ctx: RequestContext) -> None:
        data = ctx.to_dict()
        data["timestamp"] = time.time()
        if ctx.request_id in self._records:
            del self._records[ctx.request_id]
        self._records[ctx.request_id] = data
        while len(self._records) > self._max:
            self._records.popitem(last=False)

    def update(self, request_id: str, **fields: Any) -> None:
        rec = self._records.get(request_id)
        if not rec:
            return
        rec.update(fields)

    def get(self, request_id: str) -> dict[str, Any] | None:
        return self._records.get(request_id)
