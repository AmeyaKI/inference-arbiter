"""Re-export backend client as BackendProxy."""

from inference_arbiter.endpoints.client import BackendClient

BackendProxy = BackendClient

__all__ = ["BackendProxy", "BackendClient"]
