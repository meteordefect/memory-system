import os
from contextlib import contextmanager

import psycopg

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS facts (
    id          BIGSERIAL PRIMARY KEY,
    source_id   TEXT NOT NULL,
    source_name TEXT NOT NULL,
    chunk_idx   INT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(384) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, chunk_idx)
);

CREATE INDEX IF NOT EXISTS facts_content_fts
    ON facts USING gin (to_tsvector('english', content));

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://company_brain@127.0.0.1:5432/company_brain",
    )


@contextmanager
def connect():
    with psycopg.connect(database_url()) as conn:
        yield conn


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        for statement in SCHEMA.split(";"):
            sql = statement.strip()
            if sql:
                cur.execute(sql)
    conn.commit()


def fact_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM facts").fetchone()[0]


def known_sources(conn) -> set[str]:
    rows = conn.execute("SELECT DISTINCT source_id FROM facts").fetchall()
    return {row[0] for row in rows}


def source_mtimes(conn) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE 'mtime:%%'").fetchall()
    return {row[0][len("mtime:") :]: row[1] for row in rows}


def replace_source(conn, source_id: str, source_name: str, pieces: list[tuple[int, str, list[float]]]) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM facts WHERE source_id = %s", (source_id,))
        for chunk_idx, content, embedding in pieces:
            conn.execute(
                """
                INSERT INTO facts (source_id, source_name, chunk_idx, content, embedding)
                VALUES (%s, %s, %s, %s, %s::vector)
                """,
                (source_id, source_name, chunk_idx, content, _literal(embedding)),
            )


def delete_absent(conn, keep_ids: set[str]) -> None:
    with conn.transaction():
        conn.execute(
            """
            DELETE FROM facts
            WHERE source_id NOT LIKE 'sample:%%'
              AND NOT (source_id = ANY(%s::text[]))
            """,
            (list(keep_ids),),
        )
        conn.execute(
            """
            DELETE FROM meta
            WHERE key LIKE 'mtime:%%'
              AND NOT (substring(key FROM 7) = ANY(%s::text[]))
            """,
            (list(keep_ids),),
        )


def vector_hits(conn, embedding: list[float], limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT source_id, source_name, chunk_idx, content,
               1 - (embedding <=> %s::vector) AS score
        FROM facts
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (_literal(embedding), _literal(embedding), limit),
    ).fetchall()
    return [_row(row) for row in rows]


def keyword_hits(conn, query: str, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT source_id, source_name, chunk_idx, content,
               ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s)) AS score
        FROM facts
        WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
        ORDER BY score DESC
        LIMIT %s
        """,
        (query, query, limit),
    ).fetchall()
    return [_row(row) for row in rows]


def list_sources(conn) -> tuple[list[dict], str | None]:
    rows = conn.execute(
        """
        SELECT source_name, count(*)
        FROM facts
        GROUP BY source_name
        ORDER BY source_name
        """
    ).fetchall()
    last = conn.execute("SELECT value FROM meta WHERE key = 'last_success'").fetchone()
    return (
        [{"source_name": row[0], "chunks": row[1]} for row in rows],
        last[0] if last else None,
    )


def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = %s", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
    )
    conn.commit()


def _literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in embedding) + "]"


def _row(row) -> dict:
    return {
        "source_id": row[0],
        "source_name": row[1],
        "chunk_idx": row[2],
        "content": row[3],
        "score": float(row[4]),
    }
