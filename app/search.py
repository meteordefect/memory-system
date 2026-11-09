import logging

from app.embed import embed_query
from app.extract import configured
from app.graph import connected_hits
from app.ontology import load_or_empty
from app.store import keyword_hits, mark_shown, vector_hits
from app.text import rrf

log = logging.getLogger("company-brain.search")


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
    if configured():
        try:
            hits = hits + connected_hits(conn, query, load_or_empty(), hits)
        except Exception:
            log.exception("Graph lookup failed")
    mark_shown(conn, [hit["id"] for hit in hits])
    return hits


def format_hits(hits: list[dict]) -> str:
    if not hits:
        return "The documents do not say."
    blocks = []
    for hit in hits:
        lines = [f"Chunk: {hit['id']}", f"Source: {hit['source_name']}"]
        if hit.get("path"):
            lines.append(f"Connected: {hit['path']}")
        lines.append(hit["content"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
