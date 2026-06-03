"""OpenAI-compatible request/response types with routing extensions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from inference_arbiter.models import Priority


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "auto"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    user: str | None = None

    x_slo_deadline_ms: int | None = Field(default=None, alias="x_slo_deadline_ms")
    x_priority: Priority = Field(default=Priority.STANDARD, alias="x_priority")
    x_request_id: str | None = Field(default=None, alias="x_request_id")

    def messages_as_dicts(self) -> list[dict]:
        return [m.model_dump(exclude_none=True) for m in self.messages]

    @property
    def allow_degraded_ok(self) -> bool:
        return self.model.strip().lower() == "auto-degraded-ok"

    def backend_payload(self, backend_model: str) -> dict[str, Any]:
        data = self.model_dump(
            by_alias=True,
            exclude_none=True,
            exclude={"x_slo_deadline_ms", "x_priority", "x_request_id"},
        )
        data["model"] = backend_model
        return data


class ModelCard(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "inference-arbiter"


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]
