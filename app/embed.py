import threading

_model = None
_lock = threading.Lock()
EMBED_BATCH = 8


def _model_instance():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", threads=1)
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    vectors: list[list[float]] = []
    model = _model_instance()
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        with _lock:
            vectors.extend(
                vector.tolist() for vector in model.embed(batch, batch_size=EMBED_BATCH)
            )
    return vectors


def embed_query(text: str) -> list[float]:
    with _lock:
        embedding = _model_instance()
        if hasattr(embedding, "query_embed"):
            return next(iter(embedding.query_embed([text]))).tolist()
        prefixed = "Represent this sentence for searching relevant passages: " + text
        return next(iter(embedding.embed([prefixed]))).tolist()
