"""Re-export endpoint state for backward compatibility."""

from inference_arbiter.routing.state import EndpointRegistry, EndpointState

__all__ = ["EndpointRegistry", "EndpointState"]
