"""Backward-compatible entrypoint."""

from inference_arbiter.gateway.app import app, create_app, run

__all__ = ["app", "create_app", "run"]
