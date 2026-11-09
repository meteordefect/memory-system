import logging
from pathlib import Path

from app.embed import embed_passages
from app.graph import refresh_source
from app.ontology import load_or_empty
from app.store import replace_source, stored_source_ids
from app.text import chunks

log = logging.getLogger("company-brain.ingest")


def sample_markdown(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*.md") if path.is_file())


def sample_source_id(directory: Path, path: Path) -> str:
    return "sample:" + path.relative_to(directory).as_posix()


def new_sample_files(directory: Path, paths: list[Path], stored_ids: set[str]) -> list[Path]:
    return [path for path in paths if sample_source_id(directory, path) not in stored_ids]


def load_new_samples(conn, directory: Path) -> list[str]:
    stored = stored_source_ids(conn)
    loaded = []
    for path in new_sample_files(directory, sample_markdown(directory), stored):
        relative = path.relative_to(directory).as_posix()
        text = path.read_text(encoding="utf-8")
        # The graph thread extracts samples after boot, so this call does not hold up /health.
        ingest_text(conn, sample_source_id(directory, path), relative, text, refresh=False)
        loaded.append(relative)
    return loaded


def ingest_text(conn, source_id: str, source_name: str, text: str, refresh: bool = True) -> int:
    pieces = chunks(text)
    vectors = embed_passages(pieces)
    rows = [
        (index, piece, vector)
        for index, (piece, vector) in enumerate(zip(pieces, vectors))
    ]
    replace_source(conn, source_id, source_name, rows)
    if refresh:
        try:
            refresh_source(conn, load_or_empty(), source_id, text)
        except Exception:
            log.exception("Graph update failed for %s", source_id)
    return len(rows)
