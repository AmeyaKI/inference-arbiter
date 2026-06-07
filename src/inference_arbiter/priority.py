"""Re-export admission controller as PriorityGate."""

from inference_arbiter.routing.admission import AdmissionController, AdmissionDecision

PriorityGate = AdmissionController

__all__ = ["PriorityGate", "AdmissionController", "AdmissionDecision"]
