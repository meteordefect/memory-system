from app.embed import embed_passages
from app.store import replace_source
from app.text import chunks


def ingest_text(conn, source_id: str, source_name: str, text: str) -> int:
    pieces = chunks(text)
    vectors = embed_passages(pieces)
    rows = [
        (index, piece, vector)
        for index, (piece, vector) in enumerate(zip(pieces, vectors))
    ]
    replace_source(conn, source_id, source_name, rows)
    return len(rows)
