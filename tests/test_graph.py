from pathlib import Path

from app.graph import _refresh_source, connected_hits, drop_absent, replace_graph
from app.ontology import Alias, EntityType, Ontology, Relationship, load, mentioned_aliases
from app.store import replace_source

ROOT = Path(__file__).resolve().parents[1]


def _ontology() -> Ontology:
    return Ontology(
        entities=(
            EntityType("Person", ""),
            EntityType("Company", ""),
            EntityType("Product", ""),
        ),
        relationships=(
            Relationship("Person", "works_at", "Company"),
            Relationship("Company", "customer_of", "Company"),
        ),
        aliases=(
            Alias("Company", "Kedron Street Kitchen", ("KSK-19",)),
            Alias("Product", "Light soy sauce", ("NL-SOY-1",)),
        ),
        digest="digest-a",
    )


def _embedding() -> list[float]:
    values = [0.0] * 384
    values[0] = 1.0
    return values


def _chunks(conn, source_id: str) -> list[tuple[int, str]]:
    rows = conn.execute(
        """
        SELECT id, content
        FROM facts
        WHERE source_id = %s AND deleted_at IS NULL
        ORDER BY chunk_idx
        """,
        (source_id,),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def test_one_person_is_stored_once_and_a_second_file_reuses_the_row(conn):
    ontology = _ontology()
    embedding = _embedding()
    replace_source(conn, "email", "email.md", [(0, "Arun Shah at KSK-19 asked about soy", embedding)])
    replace_graph(
        conn,
        ontology,
        "email",
        _chunks(conn, "email"),
        {
            "entities": [{"type": "Person", "name": "Arun Shah"}],
            "links": [
                {
                    "from_type": "Person",
                    "from": "Arun Shah",
                    "type": "works_at",
                    "to_type": "Company",
                    "to": "KSK-19",
                }
            ],
        },
        "digest-a",
    )
    replace_source(conn, "invoice", "invoice.md", [(0, "Buyer Arun Shah, account KSK-19", embedding)])
    replace_graph(
        conn,
        ontology,
        "invoice",
        _chunks(conn, "invoice"),
        {
            "entities": [{"type": "Person", "name": "Dr. Arun Shah"}],
            "links": [
                {
                    "from_type": "Person",
                    "from": "Arun Shah",
                    "type": "works_at",
                    "to_type": "Company",
                    "to": "Kedron Street Kitchen",
                }
            ],
        },
        "digest-a",
    )
    people = conn.execute("SELECT name FROM entities WHERE type = 'Person'").fetchall()
    companies = conn.execute("SELECT name FROM entities WHERE type = 'Company'").fetchall()
    assert people == [("Arun Shah",)]
    assert companies == [("Kedron Street Kitchen",)]
    assert conn.execute("SELECT count(*) FROM links").fetchone()[0] == 2


def test_the_soy_email_reaches_the_seven_day_rule(conn):
    ontology = load(ROOT / "ontology.yaml")
    embedding = _embedding()
    email = (ROOT / "sample/demo/email-kedron-soy-return.md").read_text(encoding="utf-8")
    prices = (ROOT / "sample/price-list.md").read_text(encoding="utf-8")
    replace_source(conn, "email", "email.md", [(0, email, embedding)])
    replace_source(conn, "prices", "price-list.md", [(0, prices, embedding)])
    for source_id, text in (("email", email), ("prices", prices)):
        replace_graph(
            conn,
            ontology,
            source_id,
            _chunks(conn, source_id),
            {"entities": mentioned_aliases(ontology, text), "links": []},
        )
    email_chunk = _chunks(conn, "email")[0]
    hits = connected_hits(
        conn,
        "who asked to return the soy",
        ontology,
        [
            {
                "id": email_chunk[0],
                "source_id": "email",
                "source_name": "email.md",
                "chunk_idx": 0,
                "content": email_chunk[1],
            }
        ],
    )
    assert any("7 days" in hit["content"] for hit in hits)


def test_alias_names_stay_when_the_model_call_fails(conn, monkeypatch):
    monkeypatch.setenv("EXTRACTION_API_KEY", "test-key")
    monkeypatch.setattr("app.graph.extract_text", lambda ontology, text: None)
    ontology = load(ROOT / "ontology.yaml")
    email = (ROOT / "sample/demo/email-kedron-soy-return.md").read_text(encoding="utf-8")
    replace_source(conn, "email", "email.md", [(0, email, _embedding())])
    assert _refresh_source(conn, ontology, "email", email) is False
    names = {row[0] for row in conn.execute("SELECT name FROM entities").fetchall()}
    assert "Light soy sauce" in names
    assert "Arun Shah" in names
    assert conn.execute("SELECT value FROM meta WHERE key = 'graph:email'").fetchone() is None


def test_a_shared_product_adds_the_other_file(conn):
    ontology = _ontology()
    embedding = _embedding()
    replace_source(conn, "email", "email.md", [(0, "3 bottles of NL-SOY-1", embedding)])
    replace_source(conn, "prices", "price-list.md", [(0, "Light soy sauce can be returned within 7 days", embedding)])
    email_chunks = _chunks(conn, "email")
    price_chunks = _chunks(conn, "prices")
    replace_graph(
        conn,
        ontology,
        "email",
        email_chunks,
        {"entities": [{"type": "Product", "name": "NL-SOY-1"}], "links": []},
    )
    replace_graph(
        conn,
        ontology,
        "prices",
        price_chunks,
        {"entities": [{"type": "Product", "name": "Light soy sauce"}], "links": []},
    )
    hits = connected_hits(
        conn,
        "who asked to return the soy",
        ontology,
        [
            {
                "id": email_chunks[0][0],
                "source_id": "email",
                "source_name": "email.md",
                "chunk_idx": 0,
                "content": email_chunks[0][1],
            }
        ],
    )
    assert [hit["source_name"] for hit in hits] == ["price-list.md"]
    assert hits[0]["path"] == "Product: Light soy sauce"


def test_a_hop_follows_works_at(conn):
    ontology = _ontology()
    embedding = _embedding()
    replace_source(conn, "people", "people.md", [(0, "Arun Shah", embedding)])
    replace_source(conn, "account", "account.md", [(0, "Kedron Street Kitchen is a customer", embedding)])
    replace_graph(
        conn,
        ontology,
        "people",
        _chunks(conn, "people"),
        {
            "links": [
                {
                    "from_type": "Person",
                    "from": "Arun Shah",
                    "type": "works_at",
                    "to_type": "Company",
                    "to": "Kedron Street Kitchen",
                }
            ]
        },
    )
    replace_graph(
        conn,
        ontology,
        "account",
        _chunks(conn, "account"),
        {"entities": [{"type": "Company", "name": "Kedron Street Kitchen"}], "links": []},
    )
    hits = connected_hits(conn, "What did Arun Shah ask?", ontology, [])
    assert {hit["source_name"] for hit in hits} == {"people.md", "account.md"}
    account = next(hit for hit in hits if hit["source_name"] == "account.md")
    assert "works_at" in account["path"]


def test_replacing_a_file_drops_its_old_link(conn):
    ontology = _ontology()
    embedding = _embedding()
    replace_source(conn, "email", "email.md", [(0, "Arun Shah at Kedron Street Kitchen", embedding)])
    replace_graph(
        conn,
        ontology,
        "email",
        _chunks(conn, "email"),
        {
            "links": [
                {
                    "from_type": "Person",
                    "from": "Arun Shah",
                    "type": "works_at",
                    "to_type": "Company",
                    "to": "Kedron Street Kitchen",
                }
            ]
        },
    )
    replace_source(conn, "email", "email.md", [(0, "The office is closed", embedding)])
    replace_graph(conn, ontology, "email", _chunks(conn, "email"), {"entities": [], "links": []})
    assert conn.execute("SELECT count(*) FROM links").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM entities").fetchone()[0] == 0


def test_a_removed_drive_file_drops_its_graph_and_a_sample_stays(conn):
    ontology = _ontology()
    embedding = _embedding()
    replace_source(conn, "drive-a", "a.md", [(0, "Arun Shah", embedding)])
    replace_source(conn, "sample:price-list.md", "price-list.md", [(0, "Light soy sauce", embedding)])
    replace_graph(
        conn,
        ontology,
        "drive-a",
        _chunks(conn, "drive-a"),
        {"entities": [{"type": "Person", "name": "Arun Shah"}], "links": []},
        "digest-a",
    )
    replace_graph(
        conn,
        ontology,
        "sample:price-list.md",
        _chunks(conn, "sample:price-list.md"),
        {"entities": [{"type": "Product", "name": "Light soy sauce"}], "links": []},
        "digest-a",
    )
    drop_absent(conn, set())
    names = {row[0] for row in conn.execute("SELECT name FROM entities").fetchall()}
    assert names == {"Light soy sauce"}
    assert conn.execute("SELECT value FROM meta WHERE key = 'graph:drive-a'").fetchone() is None
    assert conn.execute(
        "SELECT value FROM meta WHERE key = 'graph:sample:price-list.md'"
    ).fetchone()[0] == "digest-a"
