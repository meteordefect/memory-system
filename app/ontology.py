import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import yaml

_TITLES = {"mr", "mrs", "ms", "dr", "prof"}


class OntologyError(ValueError):
    pass


@dataclass(frozen=True)
class EntityType:
    name: str
    hint: str


@dataclass(frozen=True)
class Relationship:
    from_type: str
    type: str
    to_type: str
    hint: str = ""


@dataclass(frozen=True)
class Alias:
    type: str
    name: str
    also: tuple[str, ...]


@dataclass(frozen=True)
class Ontology:
    entities: tuple[EntityType, ...]
    relationships: tuple[Relationship, ...]
    aliases: tuple[Alias, ...]
    digest: str


def ontology_path() -> Path:
    override = os.environ.get("ONTOLOGY_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "ontology.yaml"


def load(path: Path | None = None) -> Ontology:
    file = path or ontology_path()
    if not file.is_file():
        raise OntologyError(f"Ontology file not found: {file}")
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise OntologyError("Ontology file must be a mapping of entities, relationships, and aliases.")
    entities = _entities(raw.get("entities") or [])
    known = {entity.name for entity in entities}
    relationships = _relationships(raw.get("relationships") or [], known)
    aliases = _aliases(raw.get("aliases") or [], known)
    digest = sha256(
        json.dumps(
            {
                "entities": [(item.name, item.hint) for item in entities],
                "relationships": [(item.from_type, item.type, item.to_type, item.hint) for item in relationships],
                "aliases": [(item.type, item.name, item.also) for item in aliases],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:16]
    return Ontology(tuple(entities), tuple(relationships), tuple(aliases), digest)


def load_or_empty() -> Ontology:
    try:
        return load()
    except Exception:
        import logging

        logging.getLogger("company-brain.graph").exception(
            "Ontology file was not loaded. Search still works. The graph step is skipped."
        )
        return Ontology((), (), (), "")


def name_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    parts = key.split()
    if parts and parts[0] in _TITLES:
        parts = parts[1:]
    return " ".join(parts)


def canonical(ontology: Ontology, type_name: str, name: str) -> tuple[str, str]:
    key = name_key(name)
    for alias in ontology.aliases:
        if alias.type != type_name:
            continue
        known = {name_key(alias.name), *(name_key(item) for item in alias.also)}
        if key in known:
            return alias.name, name_key(alias.name)
    return _display_name(name), key


def mentioned_aliases(ontology: Ontology, text: str) -> list[dict]:
    found = []
    seen = set()
    for alias in ontology.aliases:
        names = (alias.name, *alias.also)
        if not any(mentioned(text, name) for name in names):
            continue
        key = (alias.type, alias.name)
        if key in seen:
            continue
        seen.add(key)
        found.append({"type": alias.type, "name": alias.name})
    return found


def mentioned(text: str, name: str) -> bool:
    needle = name_key(name)
    if len(needle) < 3:
        return False
    parts = [re.escape(part) for part in needle.split()]
    return re.search(r"\b" + r"\s+".join(parts) + r"\b", name_key(text)) is not None


def accept(ontology: Ontology, payload) -> dict:
    if not isinstance(payload, dict):
        return {"entities": [], "links": []}
    types = {entity.name.lower(): entity.name for entity in ontology.entities}
    allowed = {
        (item.from_type, item.type.lower(), item.to_type): item.type
        for item in ontology.relationships
    }
    entities: list[dict] = []
    index: dict[tuple[str, str], dict] = {}

    def add(type_name: str, name: str) -> dict | None:
        canonical_type = types.get(type_name.strip().lower(), "")
        if not canonical_type:
            return None
        display, key = canonical(ontology, canonical_type, name)
        if not key:
            return None
        slot = index.get((canonical_type, key))
        if slot is None:
            slot = {"type": canonical_type, "name": display}
            index[(canonical_type, key)] = slot
            entities.append(slot)
        return slot

    for item in payload.get("entities") or []:
        if isinstance(item, dict):
            add(str(item.get("type") or ""), str(item.get("name") or ""))

    links = []
    seen = set()
    for item in payload.get("links") or []:
        if not isinstance(item, dict):
            continue
        from_type = types.get(str(item.get("from_type") or "").strip().lower(), "")
        to_type = types.get(str(item.get("to_type") or "").strip().lower(), "")
        relation = allowed.get((from_type, str(item.get("type") or "").strip().lower(), to_type))
        if relation is None:
            continue
        left = add(from_type, str(item.get("from") or ""))
        right = add(to_type, str(item.get("to") or ""))
        if left is None or right is None:
            continue
        if left["type"] == right["type"] and left["name"] == right["name"]:
            continue
        key = (left["type"], left["name"], relation, right["type"], right["name"])
        if key in seen:
            continue
        seen.add(key)
        links.append(
            {
                "from_type": left["type"],
                "from": left["name"],
                "type": relation,
                "to_type": right["type"],
                "to": right["name"],
            }
        )
    return {"entities": entities, "links": links}


def _entities(value) -> list[EntityType]:
    if not isinstance(value, list):
        raise OntologyError("entities must be a list.")
    entities = []
    seen = set()
    for index, item in enumerate(value):
        if isinstance(item, str):
            name, hint = item.strip(), ""
        elif isinstance(item, dict):
            name, hint = str(item.get("name") or "").strip(), str(item.get("hint") or "").strip()
        else:
            raise OntologyError(f"entities[{index}] must be a name or a mapping with name.")
        if not name:
            raise OntologyError(f"entities[{index}] needs a name.")
        if name.lower() in seen:
            raise OntologyError(f"Duplicate entity type: {name}.")
        seen.add(name.lower())
        entities.append(EntityType(name, hint))
    return entities


def _relationships(value, known: set[str]) -> list[Relationship]:
    if not isinstance(value, list):
        raise OntologyError("relationships must be a list.")
    relationships = []
    seen = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise OntologyError(f"relationships[{index}] must be a mapping with from, type, and to.")
        from_type = str(item.get("from") or "").strip()
        relation = str(item.get("type") or "").strip()
        to_type = str(item.get("to") or "").strip()
        if from_type not in known:
            raise OntologyError(f"relationships[{index}].from '{from_type}' is not an entity type.")
        if to_type not in known:
            raise OntologyError(f"relationships[{index}].to '{to_type}' is not an entity type.")
        if not relation:
            raise OntologyError(f"relationships[{index}] needs a type.")
        hint = str(item.get("hint") or "").strip()
        key = (from_type, relation, to_type)
        if key in seen:
            raise OntologyError(f"Duplicate relationship: {from_type} {relation} {to_type}.")
        seen.add(key)
        relationships.append(Relationship(from_type, relation, to_type, hint))
    return relationships


def _aliases(value, known: set[str]) -> list[Alias]:
    if not isinstance(value, list):
        raise OntologyError("aliases must be a list.")
    aliases = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise OntologyError(f"aliases[{index}] must be a mapping with type, name, and also.")
        type_name = str(item.get("type") or "").strip()
        name = str(item.get("name") or "").strip()
        if type_name not in known:
            raise OntologyError(f"aliases[{index}].type '{type_name}' is not an entity type.")
        if not name:
            raise OntologyError(f"aliases[{index}] needs a name.")
        aliases.append(Alias(type_name, name, tuple(_names(item.get("also"), f"aliases[{index}].also"))))
    return aliases


def _names(value, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise OntologyError(f"{label} must be a list of names.")
    names = []
    for item in value:
        name = str(item).strip()
        if name:
            names.append(name)
    return names


def _display_name(name: str) -> str:
    parts = name.split()
    if len(parts) > 1 and re.sub(r"[^a-z0-9]+", "", parts[0].lower()) in _TITLES:
        parts = parts[1:]
    return " ".join(parts)
