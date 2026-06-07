"""OpenTelemetry tracing for routing decisions."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from inference_arbiter.config import Settings

_tracer = None


def init_tracer(settings: Settings):
    global _tracer
    if not settings.otel_enabled:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("inference-arbiter")
        return _tracer
    except ImportError:
        return None


def get_tracer():
    return _tracer


@contextmanager
def routing_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        if attributes and span is not None:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span
