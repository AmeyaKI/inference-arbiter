"""Unit tests for feature extractor."""

import time

from inference_arbiter.config import Settings, build_default_endpoints
from inference_arbiter.models import ComplexityLabel
from inference_arbiter.routing.features import FeatureExtractor
from inference_arbiter.routing.state import EndpointRegistry


def test_simple_prompt_features():
    settings = Settings(feature_dim=16)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    extractor = FeatureExtractor(registry, feature_dim=16)
    result = extractor.extract([{"role": "user", "content": "What is 2+2?"}])
    assert result.complexity == ComplexityLabel.SIMPLE
    assert len(result.vector) == 16


def test_complex_prompt_features():
    settings = Settings(feature_dim=16)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    extractor = FeatureExtractor(registry, feature_dim=16)
    prompt = (
        "Compare and analyze two async Python implementations step by step:\n"
        "```python\nasync def a(): ...\n```\nProve correctness."
    )
    result = extractor.extract([{"role": "user", "content": prompt}])
    assert result.complexity in (ComplexityLabel.MEDIUM, ComplexityLabel.COMPLEX)
    assert result.signals["has_code"] is True


def test_feature_extraction_under_2ms():
    settings = Settings(feature_dim=16)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    extractor = FeatureExtractor(registry, feature_dim=16)
    messages = [{"role": "user", "content": "Summarize this article in three paragraphs."}]
    start = time.perf_counter()
    for _ in range(100):
        extractor.extract(messages)
    elapsed_ms = (time.perf_counter() - start) * 1000 / 100
    assert elapsed_ms < 2.0
