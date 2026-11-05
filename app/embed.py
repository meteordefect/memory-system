import threading

_model = None
_lock = threading.Lock()


def _model_instance():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    with _lock:
        return [vector.tolist() for vector in _model_instance().embed(texts)]


def embed_query(text: str) -> list[float]:
    with _lock:
        embedding = _model_instance()
        if hasattr(embedding, "query_embed"):
            return next(iter(embedding.query_embed([text]))).tolist()
        prefixed = "Represent this sentence for searching relevant passages: " + text
        return next(iter(embedding.embed([prefixed]))).tolist()
