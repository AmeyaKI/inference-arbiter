"""Tests for full response text retention in audit context."""

from __future__ import annotations

from inference_arbiter.routing.context import RequestContext
from inference_arbiter.models import TrafficPriority


def test_set_response_text_keeps_full_output():
    ctx = RequestContext.create(
        request_id="full-output",
        priority=TrafficPriority.INTERACTIVE,
        slo_deadline_ms=None,
        messages=[{"role": "user", "content": "hello"}],
        requested_model="auto",
    )
    long_text = "x" * 12000
    ctx.set_response_text(long_text)
    assert ctx.response_text == long_text
    assert ctx.response_preview == long_text
    audit = ctx.to_dict()
    assert audit["response_text"] == long_text
    assert audit["response_preview"] == long_text
