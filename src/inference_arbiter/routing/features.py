"""Fast feature extractor for contextual bandit (<2ms target)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

from inference_arbiter.models import ComplexityLabel, ModelTier
from inference_arbiter.routing.state import EndpointRegistry

COMPLEX_KEYWORDS = re.compile(
    r"\b(compare|analyze|analyse|synthesize|synthesise|prove|debug|refactor|"
    r"optimize|optimise|derive|evaluate|critique|theorem|epistemolog)\b",
    re.I,
)
CODE_BLOCK = re.compile(r"```[\s\S]*?```|`[^`]+`")
MATH_NOTATION = re.compile(r"\$[^$]+\$|\\\(|\\\)|\\\[|\\\]")
JSON_XML = re.compile(r"[\{\[]\s*\"[\w-]+\"\s*:")
MULTI_QUESTION = re.compile(r"\?")


@dataclass(frozen=True)
class FeatureResult:
    vector: list[float]
    complexity: ComplexityLabel
    heuristic_confidence: float
    signals: dict[str, float | int | bool]
    estimated_tokens: int


def _extract_text(messages: list[dict]) -> str:
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
    return "\n".join(parts)


class FeatureExtractor:
    def __init__(self, registry: EndpointRegistry, feature_dim: int = 16) -> None:
        self.registry = registry
        self.feature_dim = feature_dim

    def extract(self, messages: list[dict]) -> FeatureResult:
        text = _extract_text(messages)
        words = len(text.split())
        chars = max(len(text), 1)
        token_est = max(1, chars // 4)
        char_token_ratio = chars / token_est
        questions = len(MULTI_QUESTION.findall(text))
        has_code = bool(CODE_BLOCK.search(text))
        has_math = bool(MATH_NOTATION.search(text))
        has_structured = bool(JSON_XML.search(text))
        complex_kw = len(COMPLEX_KEYWORDS.findall(text))
        bracket_density = (text.count("{") + text.count("[") + text.count("(")) / chars
        hour = datetime.now().hour / 24.0

        score = 0.0
        if words > 800:
            score += 3.0
        elif words > 300:
            score += 2.0
        elif words > 120:
            score += 1.0
        if questions >= 3:
            score += 1.5
        elif questions == 2:
            score += 0.75
        if has_code:
            score += 2.0
        if has_math:
            score += 1.5
        if has_structured:
            score += 0.5
        score += min(complex_kw * 0.75, 2.25)

        if score >= 4.5:
            complexity = ComplexityLabel.COMPLEX
            confidence = min(0.55 + score * 0.06, 0.98)
        elif score >= 2.0:
            complexity = ComplexityLabel.MEDIUM
            confidence = min(0.5 + score * 0.05, 0.9)
        else:
            complexity = ComplexityLabel.SIMPLE
            confidence = min(0.65 + (2.0 - score) * 0.08, 0.95)

        states = self.registry.all_states()
        avg_latency = sum(s.latency_ema_ms or s.config.base_latency_ms for s in states) / len(states)
        avg_queue = sum(s.queue_depth_estimate for s in states) / len(states)
        max_queue = max(s.queue_depth_estimate for s in states)

        vector = [
            min(token_est / 2000.0, 1.0),
            min(char_token_ratio / 8.0, 1.0),
            float(has_code),
            float(has_math),
            float(has_structured),
            min(questions / 5.0, 1.0),
            min(complex_kw / 5.0, 1.0),
            min(bracket_density * 10.0, 1.0),
            hour,
            min(avg_latency / 5000.0, 1.0),
            min(avg_queue / 10.0, 1.0),
            min(max_queue / 10.0, 1.0),
            {ComplexityLabel.SIMPLE: 0.0, ComplexityLabel.MEDIUM: 0.5, ComplexityLabel.COMPLEX: 1.0}[
                complexity
            ],
            confidence,
            min(score / 8.0, 1.0),
            float(any(s.is_saturated(self.registry.settings) for s in states)),
        ]
        vector = vector[: self.feature_dim]
        while len(vector) < self.feature_dim:
            vector.append(0.0)

        signals = {
            "words": words,
            "questions": questions,
            "has_code": has_code,
            "has_math": has_math,
            "score": round(score, 2),
        }
        return FeatureResult(
            vector=vector,
            complexity=complexity,
            heuristic_confidence=confidence,
            signals=signals,
            estimated_tokens=token_est,
        )
