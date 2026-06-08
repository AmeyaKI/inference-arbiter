"""Environment and YAML-driven configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from inference_arbiter.models import ModelTier, RoutingMode

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


class EndpointConfig(BaseSettings):
    name: str
    tier: ModelTier
    base_url: str = ""
    backend_model: str = ""
    base_latency_ms: float = 200.0
    max_concurrency: int = 4
    tier_weight: float = 1.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARBITER_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    routing_mode: RoutingMode = RoutingMode.ACTIVE
    routing_engine: str = "full"
    default_tier: ModelTier = ModelTier.LARGE
    shadow_default_tier: ModelTier = ModelTier.LARGE

    ollama_base_url: str = "http://127.0.0.1:11434"
    small_model: str = "llama3.2:1b"
    medium_model: str = "llama3.2:3b"
    large_model: str = "llama3.1:8b"

    latency_ema_alpha: float = 0.2
    ttft_ema_alpha: float = 0.2
    error_rate_ema_alpha: float = 0.2
    p95_window_size: int = 100
    p95_spike_threshold_ms: float = 2000.0
    queue_pressure_threshold: int = 3
    verification_overhead_ms: float = 1.0

    batch_queue_max_wait_s: float = 0.0
    batch_retry_after_s: int = 30
    batch_immediate_shed: bool = True

    circuit_failure_threshold: int = 3
    circuit_error_rate_threshold: float = 0.3
    circuit_recovery_timeout_s: float = 30.0
    http_timeout_s: float = 300.0

    audit_max_records: int = 10_000
    log_level: str = "INFO"
    low_confidence_threshold: float = 0.7
    allow_degraded_ok: bool = Field(default=True)

    linucb_alpha: float = 0.5
    cold_start_min_observations_per_tier: int = 500
    heuristic_confidence_threshold: float = 0.7
    feature_dim: int = 16

    ring_buffer_size: int = 10_000
    updater_interval_s: float = 5.0
    max_output_length_multiplier: float = 4.0

    otel_enabled: bool = True
    otel_service_name: str = "inference-arbiter"

    console_enabled: bool = True
    prometheus_url: str = "http://127.0.0.1:9090"
    event_buffer_size: int = 500


def _load_yaml_defaults() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    flat: dict[str, Any] = {}
    if "host" in data:
        flat["host"] = data["host"]
    if "port" in data:
        flat["port"] = data["port"]
    routing = data.get("routing", {})
    if "mode" in routing:
        flat["routing_mode"] = routing["mode"]
    if "engine" in routing:
        flat["routing_engine"] = routing["engine"]
    if "shadow_default_tier" in routing:
        flat["shadow_default_tier"] = routing["shadow_default_tier"]
    if "ollama_base_url" in data:
        flat["ollama_base_url"] = data["ollama_base_url"]
    models = data.get("models", {})
    flat["small_model"] = models.get("small", flat.get("small_model", "llama3.2:1b"))
    flat["medium_model"] = models.get("medium", flat.get("medium_model", "llama3.2:3b"))
    flat["large_model"] = models.get("large", flat.get("large_model", "llama3.1:8b"))
    state = data.get("state", {})
    for key in (
        "latency_ema_alpha",
        "ttft_ema_alpha",
        "error_rate_ema_alpha",
        "p95_window_size",
        "p95_spike_threshold_ms",
        "queue_pressure_threshold",
        "verification_overhead_ms",
    ):
        if key in state:
            flat[key] = state[key]
    admission = data.get("admission", {})
    if "batch_retry_after_s" in admission:
        flat["batch_retry_after_s"] = admission["batch_retry_after_s"]
    if "batch_immediate_shed" in admission:
        flat["batch_immediate_shed"] = admission["batch_immediate_shed"]
    cb = data.get("circuit_breaker", {})
    for key in ("failure_threshold", "error_rate_threshold", "recovery_timeout_s"):
        yaml_key = key if key != "failure_threshold" else "failure_threshold"
        mapped = {
            "failure_threshold": "circuit_failure_threshold",
            "error_rate_threshold": "circuit_error_rate_threshold",
            "recovery_timeout_s": "circuit_recovery_timeout_s",
        }
        if yaml_key in cb:
            flat[mapped[yaml_key]] = cb[yaml_key]
    bandit = data.get("bandit", {})
    for key in (
        "linucb_alpha",
        "cold_start_min_observations_per_tier",
        "heuristic_confidence_threshold",
        "feature_dim",
    ):
        if key in bandit:
            flat[key] = bandit[key]
    telemetry = data.get("telemetry", {})
    if "ring_buffer_size" in telemetry:
        flat["ring_buffer_size"] = telemetry["ring_buffer_size"]
    if "updater_interval_s" in telemetry:
        flat["updater_interval_s"] = telemetry["updater_interval_s"]
    verification = data.get("verification", {})
    if "max_output_length_multiplier" in verification:
        flat["max_output_length_multiplier"] = verification["max_output_length_multiplier"]
    observability = data.get("observability", {})
    if "audit_max_records" in observability:
        flat["audit_max_records"] = observability["audit_max_records"]
    if "otel_enabled" in observability:
        flat["otel_enabled"] = observability["otel_enabled"]
    if "otel_service_name" in observability:
        flat["otel_service_name"] = observability["otel_service_name"]
    console = data.get("console", {})
    if "enabled" in console:
        flat["console_enabled"] = console["enabled"]
    if "prometheus_url" in console:
        flat["prometheus_url"] = console["prometheus_url"]
    if "event_buffer_size" in console:
        flat["event_buffer_size"] = console["event_buffer_size"]
    if "http_timeout_s" in data:
        flat["http_timeout_s"] = data["http_timeout_s"]
    flat["_yaml_endpoints"] = data.get("endpoints", [])
    return flat


def build_default_endpoints(settings: Settings) -> list[EndpointConfig]:
    yaml_defaults = _load_yaml_defaults()
    yaml_endpoints = yaml_defaults.get("_yaml_endpoints", [])
    base = settings.ollama_base_url.rstrip("/")
    model_map = {
        "small": settings.small_model,
        "medium": settings.medium_model,
        "large": settings.large_model,
    }
    if yaml_endpoints:
        result: list[EndpointConfig] = []
        for ep in yaml_endpoints:
            tier = ModelTier(ep["tier"])
            backend = model_map.get(ep["tier"], ep.get("backend_model", ""))
            result.append(
                EndpointConfig(
                    name=ep["name"],
                    tier=tier,
                    base_url=f"{base}/v1",
                    backend_model=backend,
                    base_latency_ms=ep.get("base_latency_ms", 200.0),
                    max_concurrency=ep.get("max_concurrency", 4),
                    tier_weight=ep.get("tier_weight", 1.0),
                )
            )
        return result
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
        yaml_defaults = _load_yaml_defaults()
        yaml_defaults.pop("_yaml_endpoints", None)
        # Environment variables (ARBITER_*) always win over YAML defaults.
        init_kwargs: dict[str, Any] = {}
        for key, value in yaml_defaults.items():
            env_key = f"ARBITER_{key.upper()}"
            if env_key not in os.environ:
                init_kwargs[key] = value
        _settings = Settings(**init_kwargs)
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
