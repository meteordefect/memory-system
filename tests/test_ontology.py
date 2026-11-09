from pathlib import Path

import pytest

from app.graph import _fact_for
from app.ontology import (
    Alias,
    EntityType,
    Ontology,
    OntologyError,
    Relationship,
    accept,
    canonical,
    load,
    mentioned,
    mentioned_aliases,
    name_key,
)

ROOT = Path(__file__).resolve().parents[1]


def _ontology() -> Ontology:
    return Ontology(
        entities=(
            EntityType("Person", "A named individual."),
            EntityType("Company", "A business."),
            EntityType("Product", "A product or SKU."),
        ),
        relationships=(
            Relationship("Person", "works_at", "Company"),
            Relationship("Company", "customer_of", "Company"),
        ),
        aliases=(Alias("Company", "Kedron Street Kitchen", ("Kedron", "KSK-19")),),
        digest="test",
    )


def test_shipped_ontology_loads_the_starter_types():
    ontology = load(ROOT / "ontology.yaml")
    assert [item.name for item in ontology.entities] == ["Person", "Company", "Product"]
    assert ("Person", "works_at", "Company") in {
        (item.from_type, item.type, item.to_type) for item in ontology.relationships
    }
    assert ontology.digest
    soy = next(item for item in ontology.aliases if item.name == "Light soy sauce")
    assert "NL-SOY-1" in soy.also
    email = (ROOT / "sample/demo/email-kedron-soy-return.md").read_text(encoding="utf-8")
    found = {(item["type"], item["name"]) for item in mentioned_aliases(ontology, email)}
    assert found == {
        ("Person", "Arun Shah"),
        ("Company", "Kedron Street Kitchen"),
        ("Company", "Northline Wholesale"),
        ("Product", "Light soy sauce"),
    }
    prices = (ROOT / "sample/price-list.md").read_text(encoding="utf-8")
    price_names = {item["name"] for item in mentioned_aliases(ontology, prices)}
    assert {"Light soy sauce", "Jasmine rice"} <= price_names


def test_alias_and_title_resolve_to_one_name():
    ontology = _ontology()
    assert canonical(ontology, "Company", "KSK-19") == ("Kedron Street Kitchen", "kedron street kitchen")
    assert canonical(ontology, "Person", "Dr. Mei Tan") == ("Mei Tan", "mei tan")
    assert name_key("Dr. Mei Tan") == name_key("Mei Tan")


def test_unknown_types_are_dropped_and_legal_links_are_kept():
    ontology = _ontology()
    accepted = accept(
        ontology,
        {
            "entities": [
                {"type": "Person", "name": "Arun Shah"},
                {"type": "Policy", "name": "Returns"},
            ],
            "links": [
                {
                    "from_type": "person",
                    "from": "Arun",
                    "type": "WORKS_AT",
                    "to_type": "Company",
                    "to": "KSK-19",
                },
                {
                    "from_type": "Person",
                    "from": "Arun Shah",
                    "type": "owns",
                    "to_type": "Product",
                    "to": "Soy",
                },
            ],
        },
    )
    assert accepted["entities"] == [
        {"type": "Person", "name": "Arun Shah"},
        {"type": "Person", "name": "Arun"},
        {"type": "Company", "name": "Kedron Street Kitchen"},
    ]
    assert accepted["links"] == [
        {
            "from_type": "Person",
            "from": "Arun",
            "type": "works_at",
            "to_type": "Company",
            "to": "Kedron Street Kitchen",
        }
    ]


def test_empty_relationships_keep_the_names():
    ontology = Ontology(
        entities=(EntityType("Product", ""),),
        relationships=(),
        aliases=(),
        digest="test",
    )
    accepted = accept(
        ontology,
        {
            "entities": [{"type": "Product", "name": "Jasmine rice"}],
            "links": [
                {
                    "from_type": "Product",
                    "from": "Jasmine rice",
                    "type": "superseded_by",
                    "to_type": "Product",
                    "to": "Jasmine rice",
                }
            ],
        },
    )
    assert accepted["entities"] == [{"type": "Product", "name": "Jasmine rice"}]
    assert accepted["links"] == []


def test_a_name_matches_on_words():
    assert mentioned("Please return the NL-RICE-20 pack", "NL-RICE-20")
    assert mentioned("Jasmine rice is SGD 42", "jasmine rice")
    assert not mentioned("the price is 42", "rice")
    assert not mentioned("return the soy", "light soy sauce")


def test_fact_pointer_uses_the_chunk_that_names_it():
    chunks = [(1, "the price is 42"), (2, "jasmine rice, SKU NL-RICE-20")]
    assert _fact_for(chunks, "NL-RICE-20") == 2
    assert _fact_for(chunks, "carriage bolts") == 1


def test_a_bad_relationship_names_the_line(tmp_path: Path):
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entities:\n  - name: Person\nrelationships:\n  - {from: Staff, type: works_at, to: Person}\n",
        encoding="utf-8",
    )
    with pytest.raises(OntologyError, match="relationships\\[0\\].from 'Staff'"):
        load(path)
