import os

import psycopg
import pytest

from app.search import format_hits, search
from app.store import (
    delete_absent,
    eligible_for_default_search,
    init_schema,
    keyword_hits,
    mark_shown,
    next_decay,
    record_feedback,
    replace_source,
    vector_hits,
)


def test_two_demotions_hide_a_chunk():
    score = next_decay(1.0, "wrong")
    assert eligible_for_default_search(score, None)
    score = next_decay(score, "outdated")
    assert score == 0.0
    assert not eligible_for_default_search(score, None)


def test_useful_can_return_a_hidden_chunk():
    assert next_decay(1.0, "useful") == 1.0
    assert next_decay(0.0, "wrong") == 0.0
    assert eligible_for_default_search(next_decay(0.0, "useful"), None)


def test_soft_delete_hides_a_fresh_chunk():
    assert not eligible_for_default_search(1.0, object())


def test_search_bumps_only_returned_chunks(monkeypatch):
    first = _hit(3, 0, "rice")
    second = _hit(4, 1, "bolt")
    shown = {}
    _patch_search(monkeypatch, [first, second], shown)
    hits = search(None, "rice", top_k=1)
    assert [hit["id"] for hit in hits] == [3]
    assert shown["ids"] == [3]
    assert format_hits(hits).startswith("Chunk: 3\nSource: a.md\nrice")


def test_empty_search_marks_nothing(monkeypatch):
    shown = {}
    _patch_search(monkeypatch, [], shown)
    assert search(None, "rice") == []
    assert shown["ids"] == []
    assert format_hits([]) == "The documents do not say."


def test_bearer_guard_still_fails_closed():
    text = open("app/main.py", encoding="utf-8").read()
    assert "if not key:\n        return False" in text
    assert 'startswith("/mcp")' in text


def test_existing_bank_keeps_rows_through_migration(conn):
    conn.execute("DROP TABLE IF EXISTS chunk_feedback")
    conn.execute("DROP TABLE IF EXISTS facts")
    conn.execute(
        """
        CREATE TABLE facts (
            id          BIGSERIAL PRIMARY KEY,
            source_id   TEXT NOT NULL,
            source_name TEXT NOT NULL,
            chunk_idx   INT NOT NULL,
            content     TEXT NOT NULL,
            embedding   vector(384) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_id, chunk_idx)
        )
        """
    )
    conn.commit()
    embedding = _embedding()
    literal = "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
    conn.execute(
        """
        INSERT INTO facts (source_id, source_name, chunk_idx, content, embedding)
        VALUES ('legacy', 'legacy.md', 0, 'legacy jasmine rice', %s::vector)
        """,
        (literal,),
    )
    conn.commit()
    init_schema(conn)
    row = conn.execute(
        """
        SELECT content, useful_count, shown_count, decay_score, deleted_at
        FROM facts
        WHERE source_id = 'legacy'
        """
    ).fetchone()
    assert row[0] == "legacy jasmine rice"
    assert (row[1], row[2]) == (0, 0)
    assert float(row[3]) == pytest.approx(1.0)
    assert row[4] is None
    replace_source(conn, "legacy", "legacy.md", [(0, "fresh legacy jasmine", embedding)])
    assert [hit["content"] for hit in keyword_hits(conn, "jasmine", 5)] == ["fresh legacy jasmine"]


def test_migration_is_idempotent(conn):
    init_schema(conn)
    init_schema(conn)
    columns = {
        row[0]
        for row in conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'facts'
            """
        ).fetchall()
    }
    assert {
        "useful_count",
        "shown_count",
        "last_useful_at",
        "decay_score",
        "deleted_at",
    } <= columns
    index = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'facts_live_source_chunk'"
    ).fetchone()
    assert index is not None
    assert "deleted_at IS NULL" in index[0]
    uniques = conn.execute(
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'facts' AND con.contype = 'u'
        """
    ).fetchall()
    assert uniques == []


def test_replace_and_absent_files_are_soft_deleted(conn):
    embedding = _embedding()
    replace_source(
        conn,
        "doc",
        "doc.md",
        [(0, "old jasmine one", embedding), (1, "old jasmine two", embedding)],
    )
    replace_source(conn, "doc", "doc.md", [(0, "new jasmine only", embedding)])
    rows = conn.execute(
        """
        SELECT content, deleted_at IS NULL
        FROM facts
        WHERE source_id = 'doc'
        ORDER BY id
        """
    ).fetchall()
    assert rows == [
        ("old jasmine one", False),
        ("old jasmine two", False),
        ("new jasmine only", True),
    ]
    replace_source(conn, "sample:price-list.md", "price-list.md", [(0, "sample jasmine", embedding)])
    replace_source(conn, "drive-a", "a.md", [(0, "kept jasmine", embedding)])
    replace_source(conn, "drive-b", "b.md", [(0, "gone jasmine", embedding)])
    delete_absent(conn, {"drive-a", "doc"})
    found = {hit["content"] for hit in keyword_hits(conn, "jasmine", 10)}
    assert "new jasmine only" in found
    assert "kept jasmine" in found
    assert "sample jasmine" in found
    assert "old jasmine one" not in found
    assert "gone jasmine" not in found
    listed = conn.execute(
        """
        SELECT source_name
        FROM facts
        WHERE deleted_at IS NULL
        GROUP BY source_name
        ORDER BY source_name
        """
    ).fetchall()
    assert [row[0] for row in listed] == ["a.md", "doc.md", "price-list.md"]


def test_feedback_updates_counters_and_hides_heavy_demotion(conn):
    embedding = _embedding()
    replace_source(conn, "doc", "doc.md", [(0, "jasmine rice costs forty two", embedding)])
    fact_id = conn.execute("SELECT id FROM facts WHERE deleted_at IS NULL").fetchone()[0]
    mark_shown(conn, [fact_id])
    assert conn.execute("SELECT shown_count FROM facts WHERE id = %s", (fact_id,)).fetchone()[0] == 1

    assert "Recorded wrong" in record_feedback(conn, fact_id, "wrong", "price changed")
    decay, useful = conn.execute(
        "SELECT decay_score, useful_count FROM facts WHERE id = %s",
        (fact_id,),
    ).fetchone()
    assert float(decay) == pytest.approx(next_decay(1.0, "wrong"))
    assert useful == 0
    assert keyword_hits(conn, "jasmine", 5)

    hidden = record_feedback(conn, str(fact_id), "outdated")
    assert "hidden from default search" in hidden
    assert keyword_hits(conn, "jasmine", 5) == []
    assert vector_hits(conn, embedding, 5) == []

    assert "Recorded useful" in record_feedback(conn, fact_id, "useful", "still right")
    decay, useful, last_useful = conn.execute(
        "SELECT decay_score, useful_count, last_useful_at FROM facts WHERE id = %s",
        (fact_id,),
    ).fetchone()
    assert float(decay) == pytest.approx(0.25)
    assert useful == 1
    assert last_useful is not None
    assert [hit["id"] for hit in keyword_hits(conn, "jasmine", 5)] == [fact_id]

    notes = conn.execute(
        "SELECT verdict, note FROM chunk_feedback ORDER BY id"
    ).fetchall()
    assert notes == [("wrong", "price changed"), ("outdated", None), ("useful", "still right")]
    before = conn.execute("SELECT count(*) FROM chunk_feedback").fetchone()[0]
    assert record_feedback(conn, fact_id, "maybe") == "Verdict must be useful, wrong, or outdated."
    assert record_feedback(conn, 0, "useful") == "Chunk id must be a positive integer."
    assert "not in the index" in record_feedback(conn, 99999, "useful")
    assert conn.execute("SELECT count(*) FROM chunk_feedback").fetchone()[0] == before

    replace_source(conn, "doc", "doc.md", [(0, "replacement jasmine", embedding)])
    assert "not in the index" in record_feedback(conn, fact_id, "useful")
    assert conn.execute("SELECT count(*) FROM chunk_feedback").fetchone()[0] == before


def test_stale_and_demoted_chunks_sink(conn):
    embedding = _embedding()
    replace_source(conn, "fresh", "fresh.md", [(0, "jasmine rice pack", embedding)])
    replace_source(conn, "stale", "stale.md", [(0, "jasmine rice pack", embedding)])
    replace_source(conn, "weak", "weak.md", [(0, "jasmine rice pack", embedding)])
    weak_id = conn.execute("SELECT id FROM facts WHERE source_id = 'weak'").fetchone()[0]
    record_feedback(conn, weak_id, "wrong")
    conn.execute(
        """
        UPDATE facts
        SET created_at = now() - interval '120 days',
            shown_count = 0,
            last_useful_at = NULL
        WHERE source_id = 'stale'
        """
    )
    conn.commit()
    assert [hit["source_id"] for hit in keyword_hits(conn, "jasmine", 5)] == ["fresh", "weak", "stale"]
    assert [hit["source_id"] for hit in vector_hits(conn, embedding, 5)] == ["fresh", "weak", "stale"]


@pytest.fixture()
def conn():
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    with psycopg.connect(url) as connection:
        init_schema(connection)
        connection.execute("TRUNCATE facts, chunk_feedback RESTART IDENTITY CASCADE")
        connection.commit()
        yield connection


def _embedding() -> list[float]:
    values = [0.0] * 384
    values[0] = 1.0
    return values


def _hit(fact_id: int, chunk_idx: int, content: str) -> dict:
    return {
        "id": fact_id,
        "source_id": "s",
        "source_name": "a.md",
        "chunk_idx": chunk_idx,
        "content": content,
        "score": 1.0,
    }


def _patch_search(monkeypatch, vector, shown) -> None:
    monkeypatch.setattr("app.search.embed_query", lambda query: [0.0])
    monkeypatch.setattr("app.search.vector_hits", lambda conn, embedding, limit: vector)
    monkeypatch.setattr("app.search.keyword_hits", lambda conn, query, limit: [])
    monkeypatch.setattr("app.search.mark_shown", lambda conn, ids: shown.setdefault("ids", list(ids)))
