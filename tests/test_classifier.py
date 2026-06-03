from inference_arbiter.classifier import HeuristicComplexityClassifier
from inference_arbiter.models import ComplexityLabel


def test_simple_factual():
    clf = HeuristicComplexityClassifier()
    result = clf.classify([{"role": "user", "content": "What is the capital of France?"}])
    assert result.label == ComplexityLabel.SIMPLE
    assert result.confidence > 0.5


def test_complex_code():
    clf = HeuristicComplexityClassifier()
    prompt = """Compare two async Python implementations step by step:
```python
async def a(): ...
```
Prove correctness."""
    result = clf.classify([{"role": "user", "content": prompt}])
    assert result.label == ComplexityLabel.COMPLEX


def test_medium_prompt():
    clf = HeuristicComplexityClassifier()
    content = (
        "Summarize and analyze the following article in three paragraphs. "
        + ("word " * 350)
    )
    result = clf.classify([{"role": "user", "content": content}])
    assert result.label in (ComplexityLabel.MEDIUM, ComplexityLabel.COMPLEX)
    assert result.signals["words"] > 300


def test_math_notation():
    clf = HeuristicComplexityClassifier()
    result = clf.classify([{"role": "user", "content": "Solve $x^2 + 1 = 0$"}])
    assert result.label != ComplexityLabel.SIMPLE or result.signals["has_math"]
