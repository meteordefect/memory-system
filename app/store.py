import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql

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

DECAY_MAX = 1.0
USEFUL_BUMP = 0.25
DEMOTE_STEP = 0.5
HEAVY_DEMOTE_BELOW = 0.25
_HALF_LIFE_SECONDS = 90 * 24 * 60 * 60
RANK_WEIGHT = f"""(
  decay_score
  * exp(-0.693147 * EXTRACT(EPOCH FROM (now() - COALESCE(last_useful_at, created_at))) / {_HALF_LIFE_SECONDS}.0)
  * CASE
      WHEN shown_count = 0 AND created_at < now() - interval '30 days' THEN 0.5
      ELSE 1.0
    END
)"""
VERDICTS = {"useful", "wrong", "outdated"}


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
        for statement in _statements(SCHEMA):
            cur.execute(statement)
        for statement in _migration_statements():
            cur.execute(statement)
        _drop_full_chunk_uniques(cur)
    conn.commit()


def next_decay(current: float, verdict: str) -> float:
    if verdict == "useful":
        return min(DECAY_MAX, float(current) + USEFUL_BUMP)
    if verdict in {"wrong", "outdated"}:
        return max(0.0, float(current) - DEMOTE_STEP)
    raise ValueError(verdict)


def eligible_for_default_search(decay_score: float, deleted_at) -> bool:
    return deleted_at is None and float(decay_score) >= HEAVY_DEMOTE_BELOW


def fact_count(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM facts WHERE deleted_at IS NULL"
    ).fetchone()[0]


def known_sources(conn) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT source_id FROM facts WHERE deleted_at IS NULL"
    ).fetchall()
    return {row[0] for row in rows}


def source_mtimes(conn) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE 'mtime:%%'").fetchall()
    return {row[0][len("mtime:") :]: row[1] for row in rows}


def replace_source(conn, source_id: str, source_name: str, pieces: list[tuple[int, str, list[float]]]) -> None:
    with conn.transaction():
        conn.execute(
            """
            UPDATE facts
            SET deleted_at = now()
            WHERE source_id = %s AND deleted_at IS NULL
            """,
            (source_id,),
        )
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
            UPDATE facts
            SET deleted_at = now()
            WHERE deleted_at IS NULL
              AND source_id NOT LIKE 'sample:%%'
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
    literal = _literal(embedding)
    rows = conn.execute(
        f"""
        SELECT id, source_id, source_name, chunk_idx, content,
               1 - (embedding <=> %s::vector) AS score
        FROM facts
        WHERE deleted_at IS NULL
          AND decay_score >= %s
        ORDER BY (embedding <=> %s::vector) + (1 - GREATEST({RANK_WEIGHT}, 0.05))
        LIMIT %s
        """,
        (literal, HEAVY_DEMOTE_BELOW, literal, limit),
    ).fetchall()
    return [_row(row) for row in rows]


def keyword_hits(conn, query: str, limit: int) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT id, source_id, source_name, chunk_idx, content,
               ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s)) AS score
        FROM facts
        WHERE deleted_at IS NULL
          AND decay_score >= %s
          AND to_tsvector('english', content) @@ plainto_tsquery('english', %s)
        ORDER BY (
          ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s))
          * {RANK_WEIGHT}
        ) DESC
        LIMIT %s
        """,
        (query, HEAVY_DEMOTE_BELOW, query, query, limit),
    ).fetchall()
    return [_row(row) for row in rows]


def mark_shown(conn, fact_ids: list[int]) -> None:
    ids = [
        fact_id
        for fact_id in fact_ids
        if isinstance(fact_id, int) and not isinstance(fact_id, bool) and fact_id > 0
    ]
    if not ids:
        return
    conn.execute(
        """
        UPDATE facts
        SET shown_count = shown_count + 1
        WHERE deleted_at IS NULL
          AND id = ANY(%s)
        """,
        (ids,),
    )


def record_feedback(conn, fact_id, verdict: str, note: str | None = None) -> str:
    parsed = _fact_id(fact_id)
    if parsed is None:
        return "Chunk id must be a positive integer."
    verdict = (verdict or "").strip().lower()
    if verdict not in VERDICTS:
        return "Verdict must be useful, wrong, or outdated."
    cleaned = str(note).strip() if note else None
    if cleaned == "":
        cleaned = None
    with conn.transaction():
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id = %s AND deleted_at IS NULL",
            (parsed,),
        ).fetchone()
        stored = None
        if row is not None:
            conn.execute(
                """
                INSERT INTO chunk_feedback (fact_id, verdict, note)
                VALUES (%s, %s, %s)
                """,
                (parsed, verdict, cleaned),
            )
            if verdict == "useful":
                stored_row = conn.execute(
                    """
                    UPDATE facts
                    SET useful_count = useful_count + 1,
                        last_useful_at = now(),
                        decay_score = LEAST(%s, decay_score + %s)
                    WHERE id = %s AND deleted_at IS NULL
                    RETURNING decay_score
                    """,
                    (DECAY_MAX, USEFUL_BUMP, parsed),
                ).fetchone()
            else:
                stored_row = conn.execute(
                    """
                    UPDATE facts
                    SET decay_score = GREATEST(0, decay_score - %s)
                    WHERE id = %s AND deleted_at IS NULL
                    RETURNING decay_score
                    """,
                    (DEMOTE_STEP, parsed),
                ).fetchone()
            if stored_row is None:
                raise RuntimeError(f"chunk {parsed} changed during feedback")
            stored = stored_row[0]
    if stored is None:
        return "That chunk is not in the index."
    if float(stored) < HEAVY_DEMOTE_BELOW:
        return f"Recorded {verdict}. Chunk {parsed} is hidden from default search."
    return f"Recorded {verdict} for chunk {parsed}."


def list_sources(conn) -> tuple[list[dict], str | None]:
    rows = conn.execute(
        """
        SELECT source_name, count(*)
        FROM facts
        WHERE deleted_at IS NULL
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


def _migration_statements() -> list[str]:
    directory = Path(__file__).resolve().parent.parent / "migrations"
    paths = sorted(directory.glob("*.sql"))
    if not paths:
        raise RuntimeError(f"No migrations in {directory}")
    statements = []
    for path in paths:
        statements.extend(_statements(path.read_text(encoding="utf-8")))
    return statements


def _statements(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _drop_full_chunk_uniques(cur) -> None:
    cur.execute(
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        WHERE nsp.nspname = current_schema()
          AND rel.relname = 'facts'
          AND con.contype = 'u'
          AND (
            SELECT array_agg(att.attname::text ORDER BY cols.ord)
            FROM unnest(con.conkey) WITH ORDINALITY AS cols(attnum, ord)
            JOIN pg_attribute att
              ON att.attrelid = rel.oid AND att.attnum = cols.attnum
          ) = ARRAY['source_id', 'chunk_idx']::text[]
        """
    )
    names = [row[0] for row in cur.fetchall()]
    for name in names:
        cur.execute(sql.SQL("ALTER TABLE facts DROP CONSTRAINT {}").format(sql.Identifier(name)))


def _fact_id(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return None
    if parsed < 1:
        return None
    return parsed


def _literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in embedding) + "]"


def _row(row) -> dict:
    return {
        "id": row[0],
        "source_id": row[1],
        "source_name": row[2],
        "chunk_idx": row[3],
        "content": row[4],
        "score": float(row[5]),
    }
