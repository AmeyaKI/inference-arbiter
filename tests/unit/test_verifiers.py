"""Unit tests for fast verifiers."""

import json

from inference_arbiter.models import FailureAttribution, VerificationStatus
from inference_arbiter.verification.verifiers import verify_response


def test_verify_accepts_valid_response():
    result = verify_response(response_text="Hello world", estimated_tokens=10)
    assert result.passed
    assert result.status == VerificationStatus.PASSED


def test_verify_rejects_empty():
    result = verify_response(response_text="", estimated_tokens=10)
    assert not result.passed
    assert result.failure_attribution == FailureAttribution.QUALITY_FAILURE


def test_verify_rejects_invalid_json():
    result = verify_response(
        response_text='{"broken": ',
        estimated_tokens=10,
        expect_json=True,
    )
    assert not result.passed
    assert result.status == VerificationStatus.FAILED_INVALID_JSON


def test_verify_accepts_valid_json():
    payload = json.dumps({"answer": 42})
    result = verify_response(response_text=payload, estimated_tokens=10, expect_json=True)
    assert result.passed


def test_verifier_latency_under_1ms():
    result = verify_response(response_text="quick check", estimated_tokens=5)
    assert result.latency_us < 1000
