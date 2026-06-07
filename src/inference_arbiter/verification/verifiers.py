"""Fast programmatic verifiers (<1ms hot path)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from inference_arbiter.models import FailureAttribution, VerificationStatus

TOOL_CALL = re.compile(r"<tool_call>|\"tool_calls\"|function_call", re.I)


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    status: VerificationStatus
    failure_attribution: FailureAttribution
    latency_us: float


def verify_response(
    *,
    response_text: str,
    estimated_tokens: int,
    expect_json: bool = False,
    expect_tool_call: bool = False,
    max_length_multiplier: float = 4.0,
) -> VerificationResult:
    start = time.perf_counter()

    if not response_text or not response_text.strip():
        return _result(
            start,
            passed=False,
            status=VerificationStatus.FAILED_EMPTY,
            attribution=FailureAttribution.QUALITY_FAILURE,
        )

    max_len = int(estimated_tokens * max_length_multiplier)
    if len(response_text) > max(max_len, 4096):
        return _result(
            start,
            passed=False,
            status=VerificationStatus.FAILED_LENGTH,
            attribution=FailureAttribution.QUALITY_FAILURE,
        )

    if expect_json or response_text.strip().startswith(("{", "[")):
        try:
            json.loads(response_text)
        except json.JSONDecodeError:
            return _result(
                start,
                passed=False,
                status=VerificationStatus.FAILED_INVALID_JSON,
                attribution=FailureAttribution.QUALITY_FAILURE,
            )

    if expect_tool_call and not TOOL_CALL.search(response_text):
        return _result(
            start,
            passed=False,
            status=VerificationStatus.FAILED_TOOL_CALL,
            attribution=FailureAttribution.QUALITY_FAILURE,
        )

    return _result(
        start,
        passed=True,
        status=VerificationStatus.PASSED,
        attribution=FailureAttribution.NONE,
    )


def _result(
    start: float,
    *,
    passed: bool,
    status: VerificationStatus,
    attribution: FailureAttribution,
) -> VerificationResult:
    latency_us = (time.perf_counter() - start) * 1_000_000
    return VerificationResult(
        passed=passed,
        status=status,
        failure_attribution=attribution,
        latency_us=latency_us,
    )


def extract_response_text(content: dict) -> str:
    choices = content.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or choices[0].get("delta") or {}
    return str(message.get("content") or "")
