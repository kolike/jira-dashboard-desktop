"""Analytics helpers extracted from main module (phase 1 decomposition)."""

from typing import Iterable


def tokenize_summary(text: str) -> set[str]:
    stopwords = {
        "и", "или", "для", "что", "как", "это", "при", "надо", "нужно",
        "the", "and", "for", "with", "from", "user", "jira",
    }
    tokens = set()
    for raw in text.lower().replace("/", " ").replace("-", " ").split():
        token = "".join(ch for ch in raw if ch.isalnum())
        if len(token) >= 4 and token not in stopwords:
            tokens.add(token)
    return tokens


def overlap_score(a: Iterable[str], b: Iterable[str]) -> float:
    aset, bset = set(a), set(b)
    if not aset or not bset:
        return 0.0
    return len(aset & bset) / max(1, min(len(aset), len(bset)))
