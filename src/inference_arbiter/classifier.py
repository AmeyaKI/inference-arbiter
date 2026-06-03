"""Complexity classification: heuristics v1 with optional ML hook."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from inference_arbiter.models import ComplexityLabel

COMPLEX_KEYWORDS = re.compile(
    r"\b(compare|analyze|analyse|synthesize|synthesise|prove|debug|refactor|"
    r"optimize|optimise|derive|evaluate|critique|theorem|epistemolog)\b",
    re.I,
)
STEP_KEYWORDS = re.compile(r"\b(step\s+by\s+step|chain\s+of\s+thought|reasoning)\b", re.I)
CODE_BLOCK = re.compile(r"```[\s\S]*?```|`[^`]+`")
MATH_NOTATION = re.compile(r"\$[^$]+\$|\\\(|\\\)|\\\[|\\\]")
TABLE_MARKERS = re.compile(r"\|.+\||<table", re.I)
MULTI_QUESTION = re.compile(r"\?")
JSON_XML = re.compile(r"[\{\[]\s*\"[\w-]+\"\s*:")


@dataclass(frozen=True)
class ClassificationResult:
    label: ComplexityLabel
    confidence: float
    signals: dict[str, float | int | bool]


class BaseComplexityClassifier(ABC):
    @abstractmethod
    def classify(self, messages: list[dict]) -> ClassificationResult:
        ...


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


def _approx_word_count(text: str) -> int:
    return len(text.split())


class HeuristicComplexityClassifier(BaseComplexityClassifier):
    """Fast heuristic cascade for v1."""

    def classify(self, messages: list[dict]) -> ClassificationResult:
        text = _extract_text(messages)
        words = _approx_word_count(text)
        num_messages = len(messages)
        questions = len(MULTI_QUESTION.findall(text))
        has_code = bool(CODE_BLOCK.search(text))
        has_math = bool(MATH_NOTATION.search(text))
        has_table = bool(TABLE_MARKERS.search(text))
        has_structured = bool(JSON_XML.search(text))
        complex_kw = len(COMPLEX_KEYWORDS.findall(text))
        step_kw = bool(STEP_KEYWORDS.search(text))

        score = 0.0
        if words > 800:
            score += 3.0
        elif words > 300:
            score += 2.0
        elif words > 120:
            score += 1.0

        if num_messages > 6:
            score += 1.5
        if questions >= 3:
            score += 1.5
        elif questions == 2:
            score += 0.75

        if has_code:
            score += 2.0
        if has_math:
            score += 1.5
        if has_table:
            score += 1.0
        if has_structured:
            score += 0.5
        score += min(complex_kw * 0.75, 2.25)
        if step_kw:
            score += 1.0

        if score >= 4.5:
            label = ComplexityLabel.COMPLEX
            confidence = min(0.55 + score * 0.06, 0.98)
        elif score >= 2.0:
            label = ComplexityLabel.MEDIUM
            confidence = min(0.5 + score * 0.05, 0.9)
        else:
            label = ComplexityLabel.SIMPLE
            confidence = min(0.65 + (2.0 - score) * 0.08, 0.95)

        signals = {
            "words": words,
            "messages": num_messages,
            "questions": questions,
            "has_code": has_code,
            "has_math": has_math,
            "has_table": has_table,
            "complex_keywords": complex_kw,
            "score": round(score, 2),
        }
        return ClassificationResult(label=label, confidence=confidence, signals=signals)


class ModelComplexityClassifier(BaseComplexityClassifier):
    """Optional hook for a future lightweight ML classifier."""

    def __init__(self, inner: BaseComplexityClassifier | None = None) -> None:
        self._fallback = inner or HeuristicComplexityClassifier()

    def classify(self, messages: list[dict]) -> ClassificationResult:
        # v1: delegate to heuristics until a model artifact is wired in.
        return self._fallback.classify(messages)
