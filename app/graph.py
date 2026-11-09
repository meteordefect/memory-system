import logging
import threading

from app.extract import TEXT_LIMIT, configured, extract_text
from app.ontology import Ontology, mentioned, mentioned_aliases
from app.store import HEAVY_DEMOTE_BELOW, connect, get_meta

log = logging.getLogger("company-brain.graph")
MAX_DEPTH = 2
EXTRA_CHUNKS = 3
SEED_LIMIT = 8
_lock = threading.Lock()


def start_refresh() -> None:
    if not configured():
        log.info("Graph extraction is off. EXTRACTION_API_KEY is unset.")
        return
    threading.Thread(target=_refresh_existing, name="graph-refresh", daemon=True).start()


def refresh_source(conn, ontology: Ontology, source_id: str, text: str | None = None) -> bool:
    return _refresh_source(conn, ontology, source_id, text)


def refresh_stale(conn, ontology: Ontology) -> int:
    if not ontology.entities or not configured():
        return 0
    rows = conn.execute(
        """
        SELECT DISTINCT source_id
        FROM facts
        WHERE deleted_at IS NULL
        ORDER BY source_id
        """
    ).fetchall()
    conn.commit()
    conn.execute("SET statement_timeout = '20s'")
    source_ids = sorted(
        (row[0] for row in rows),
        key=lambda source_id: (not str(source_id).startswith("sample:"), str(source_id)),
    )
    done = 0
    for source_id in source_ids:
        if get_meta(conn, _graph_key(source_id)) == ontology.digest:
            continue
        log.info("Graph file %s", source_id)
        try:
            if _refresh_source(conn, ontology, source_id, None):
                done += 1
        except Exception:
            log.exception("Graph file failed %s", source_id)
            conn.rollback()
    return done


def replace_graph(conn, ontology: Ontology, source_id: str, chunks: list[tuple[int, str]], extracted: dict, digest: str | None = None) -> None:
    from app.ontology import accept

    accepted = accept(ontology, extracted)
    with conn.transaction():
        conn.execute("DELETE FROM links WHERE source_id = %s", (source_id,))
        conn.execute("DELETE FROM entity_sources WHERE source_id = %s", (source_id,))
        ids = {}
        for entity in accepted["entities"]:
            entity_id = _upsert_entity(conn, entity["type"], entity["name"])
            ids[(entity["type"], entity["name"])] = entity_id
            conn.execute(
                """
                INSERT INTO entity_sources (entity_id, source_id, fact_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (entity_id, source_id) DO UPDATE SET fact_id = EXCLUDED.fact_id
                """,
                (entity_id, source_id, _fact_for(chunks, entity["name"])),
            )
        for link in accepted["links"]:
            conn.execute(
                """
                INSERT INTO links (from_id, type, to_id, source_id, fact_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (from_id, type, to_id, source_id) DO UPDATE SET fact_id = EXCLUDED.fact_id
                """,
                (
                    ids[(link["from_type"], link["from"])],
                    link["type"],
                    ids[(link["to_type"], link["to"])],
                    source_id,
                    _fact_for(chunks, link["from"]) or _fact_for(chunks, link["to"]),
                ),
            )
        _delete_orphans(conn)
        if digest is not None:
            _write_meta(conn, _graph_key(source_id), digest)


def drop_absent(conn, keep_ids: set[str]) -> None:
    with conn.transaction():
        conn.execute(
            """
            DELETE FROM links
            WHERE source_id NOT LIKE 'sample:%%'
              AND NOT (source_id = ANY(%s::text[]))
            """,
            (list(keep_ids),),
        )
        conn.execute(
            """
            DELETE FROM entity_sources
            WHERE source_id NOT LIKE 'sample:%%'
              AND NOT (source_id = ANY(%s::text[]))
            """,
            (list(keep_ids),),
        )
        conn.execute(
            """
            DELETE FROM meta
            WHERE key LIKE 'graph:%%'
              AND substring(key FROM 7) NOT LIKE 'sample:%%'
              AND NOT (substring(key FROM 7) = ANY(%s::text[]))
            """,
            (list(keep_ids),),
        )
        _delete_orphans(conn)


def connected_hits(conn, query: str, ontology: Ontology, hits: list[dict], limit: int = EXTRA_CHUNKS) -> list[dict]:
    text = "\n".join([query, *[hit.get("content") or "" for hit in hits]])
    seed_ids = _matching_ids(conn, text, ontology)
    if not seed_ids:
        return []
    rows = conn.execute(
        """
        WITH RECURSIVE walk AS (
            SELECT id,
                   0 AS depth,
                   ARRAY[id] AS seen,
                   (type || ': ' || name) AS path
            FROM entities
            WHERE id = ANY(%s::bigint[])
            UNION ALL
            SELECT e.id,
                   w.depth + 1,
                   w.seen || e.id,
                   w.path || ' ' || l.type || ' ' || e.name
            FROM walk w
            JOIN links l ON l.from_id = w.id OR l.to_id = w.id
            JOIN entities e ON e.id = CASE WHEN l.from_id = w.id THEN l.to_id ELSE l.from_id END
            WHERE w.depth < %s
              AND NOT e.id = ANY (w.seen)
        )
        SELECT f.id, f.source_id, f.source_name, f.chunk_idx, f.content, walk.path, walk.depth
        FROM walk
        JOIN entity_sources es ON es.entity_id = walk.id
        JOIN facts f ON f.id = es.fact_id
        WHERE f.deleted_at IS NULL
          AND f.decay_score >= %s
        """,
        (seed_ids, MAX_DEPTH, HEAVY_DEMOTE_BELOW),
    ).fetchall()
    best = {}
    for row in rows:
        current = best.get(row[0])
        if current is None or row[6] < current[6]:
            best[row[0]] = row
    already = {(hit["source_id"], hit["chunk_idx"]): hit for hit in hits}
    chosen = []
    for row in sorted(best.values(), key=lambda item: (item[6], item[0])):
        current = already.get((row[1], row[3]))
        if current is not None:
            if not current.get("path"):
                current["path"] = row[5]
            continue
        chosen.append(
            {
                "id": row[0],
                "source_id": row[1],
                "source_name": row[2],
                "chunk_idx": row[3],
                "content": row[4],
                "score": 0.0,
                "path": row[5],
            }
        )
        if len(chosen) == limit:
            break
    return chosen


def _refresh_existing() -> None:
    from app.ontology import load_or_empty

    try:
        ontology = load_or_empty()
        with connect() as conn:
            count = refresh_stale(conn, ontology)
        log.info("Graph refresh updated %s file(s)", count)
    except Exception:
        log.exception("Graph refresh failed")


def _refresh_source(conn, ontology: Ontology, source_id: str, text: str | None) -> bool:
    if not ontology.entities or not configured():
        return False
    rows = conn.execute(
        """
        SELECT id, content
        FROM facts
        WHERE source_id = %s AND deleted_at IS NULL
        ORDER BY chunk_idx
        """,
        (source_id,),
    ).fetchall()
    chunks = [(row[0], row[1]) for row in rows]
    source_text = text if text is not None else "\n".join(row[1] for row in rows)
    source_text = source_text[:TEXT_LIMIT]
    conn.commit()
    if not chunks:
        with _lock:
            replace_graph(conn, ontology, source_id, [], {"entities": [], "links": []}, ontology.digest)
        return True
    names = mentioned_aliases(ontology, source_text)
    log.info("Graph extract %s", source_id)
    extracted = extract_text(ontology, source_text)
    log.info("Graph save %s", source_id)
    with _lock:
        if get_meta(conn, _graph_key(source_id)) == ontology.digest:
            return True
        if extracted is None:
            if names:
                replace_graph(conn, ontology, source_id, chunks, {"entities": names, "links": []})
            return False
        extracted["entities"] = list(extracted.get("entities") or []) + names
        replace_graph(conn, ontology, source_id, chunks, extracted, ontology.digest)
    return True


def _matching_ids(conn, text: str, ontology: Ontology) -> list[int]:
    if not text.strip():
        return []
    rows = conn.execute("SELECT id, type, name FROM entities ORDER BY id").fetchall()
    by_key = {(row[1], _key(row[2])): row[0] for row in rows}
    found = []
    for (type_name, key), entity_id in by_key.items():
        if mentioned(text, key):
            found.append(entity_id)
    for alias in ontology.aliases:
        names = [alias.name, *alias.also]
        if any(mentioned(text, name) for name in names):
            entity_id = by_key.get((alias.type, _key(alias.name)))
            if entity_id is not None:
                found.append(entity_id)
    chosen = []
    for entity_id in found:
        if entity_id not in chosen:
            chosen.append(entity_id)
        if len(chosen) == SEED_LIMIT:
            break
    return chosen


def _upsert_entity(conn, type_name: str, name: str) -> int:
    from app.ontology import name_key

    row = conn.execute(
        """
        INSERT INTO entities (type, name, name_key)
        VALUES (%s, %s, %s)
        ON CONFLICT (type, name_key) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (type_name, name, name_key(name)),
    ).fetchone()
    return row[0]


def _fact_for(chunks: list[tuple[int, str]], name: str) -> int | None:
    if not chunks:
        return None
    for fact_id, content in chunks:
        if mentioned(content, name):
            return fact_id
    return chunks[0][0]


def _delete_orphans(conn) -> None:
    conn.execute(
        """
        DELETE FROM entities e
        WHERE NOT EXISTS (SELECT 1 FROM entity_sources s WHERE s.entity_id = e.id)
          AND NOT EXISTS (SELECT 1 FROM links l WHERE l.from_id = e.id OR l.to_id = e.id)
        """
    )


def _write_meta(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
    )


def _graph_key(source_id: str) -> str:
    return f"graph:{source_id}"


def _key(name: str) -> str:
    from app.ontology import name_key

    return name_key(name)
