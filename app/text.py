def chunks(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    words = text.split()
    if not words:
        return []
    window = max(1, int(size / 1.3))
    step = max(1, window - int(overlap / 1.3))
    if len(words) <= window:
        return [" ".join(words)]
    out = []
    start = 0
    while start < len(words):
        out.append(" ".join(words[start : start + window]))
        if start + window >= len(words):
            break
        start += step
    return out


def rrf(rankings: list[list[tuple]], k: int = 60) -> list[tuple]:
    scores: dict[tuple, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda key: scores[key], reverse=True)
