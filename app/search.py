from app.embed import embed_query
from app.store import keyword_hits, mark_shown, vector_hits
from app.text import rrf


def search(conn, query: str, top_k: int = 5) -> list[dict]:
    top_k = min(max(top_k, 1), 20)
    pool = max(top_k, 20)
    vector = vector_hits(conn, embed_query(query), pool)
    keyword = keyword_hits(conn, query, pool)
    by_key = {}
    for hit in vector + keyword:
        by_key[(hit["source_id"], hit["chunk_idx"])] = hit
    order = rrf(
        [
            [(hit["source_id"], hit["chunk_idx"]) for hit in vector],
            [(hit["source_id"], hit["chunk_idx"]) for hit in keyword],
        ]
    )
    hits = [by_key[key] for key in order[:top_k]]
    mark_shown(conn, [hit["id"] for hit in hits])
    return hits


def format_hits(hits: list[dict]) -> str:
    if not hits:
        return "The documents do not say."
    blocks = []
    for hit in hits:
        blocks.append(f"Chunk: {hit['id']}\nSource: {hit['source_name']}\n{hit['content']}")
    return "\n\n".join(blocks)
