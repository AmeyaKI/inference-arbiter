"""Endpoint pool manager."""

from __future__ import annotations

from inference_arbiter.config import Settings, build_default_endpoints
from inference_arbiter.routing.state import EndpointRegistry


def create_registry(settings: Settings) -> EndpointRegistry:
    endpoints = build_default_endpoints(settings)
    return EndpointRegistry(endpoints, settings)
