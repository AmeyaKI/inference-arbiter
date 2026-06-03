"""Environment-driven configuration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from inference_arbiter.models import ModelTier, RoutingMode


class EndpointConfig(BaseSettings):
    name: str
    tier: ModelTier
    base_url: str
    backend_model: str
    base_latency_ms: float = 200.0
    max_concurrency: int = 4
    tier_weight: float = 1.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARBITER_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    routing_mode: RoutingMode = RoutingMode.ACTIVE
    default_tier: ModelTier = ModelTier.LARGE
    shadow_default_tier: ModelTier = ModelTier.LARGE

    ollama_base_url: str = "http://127.0.0.1:11434"
    small_model: str = "llama3.2:1b"
    medium_model: str = "llama3.2:3b"
    large_model: str = "llama3.1:8b"

    latency_ema_alpha: float = 0.3
    queue_pressure_threshold: int = 3
    batch_queue_max_wait_s: float = 5.0
    batch_retry_after_s: int = 30

    circuit_failure_threshold: int = 3
    circuit_recovery_timeout_s: float = 30.0
    http_timeout_s: float = 300.0

    audit_max_records: int = 10_000
    log_level: str = "INFO"

    allow_degraded_ok: bool = Field(default=True, description="Honor auto-degraded-ok model flag")


def build_default_endpoints(settings: Settings) -> list[EndpointConfig]:
    base = settings.ollama_base_url.rstrip("/")
    return [
        EndpointConfig(
            name="small",
            tier=ModelTier.SMALL,
            base_url=f"{base}/v1",
            backend_model=settings.small_model,
            base_latency_ms=150.0,
            max_concurrency=8,
            tier_weight=1.0,
        ),
        EndpointConfig(
            name="medium",
            tier=ModelTier.MEDIUM,
            base_url=f"{base}/v1",
            backend_model=settings.medium_model,
            base_latency_ms=400.0,
            max_concurrency=4,
            tier_weight=3.0,
        ),
        EndpointConfig(
            name="large",
            tier=ModelTier.LARGE,
            base_url=f"{base}/v1",
            backend_model=settings.large_model,
            base_latency_ms=900.0,
            max_concurrency=2,
            tier_weight=8.0,
        ),
    ]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
