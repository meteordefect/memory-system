from pathlib import Path

from app.embed import embed_passages
from app.store import replace_source, stored_source_ids
from app.text import chunks


def sample_markdown(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".md")


def new_sample_files(paths: list[Path], stored_ids: set[str]) -> list[Path]:
    return [path for path in paths if f"sample:{path.name}" not in stored_ids]


def load_new_samples(conn, directory: Path) -> list[str]:
    stored = stored_source_ids(conn)
    loaded = []
    for path in new_sample_files(sample_markdown(directory), stored):
        text = path.read_text(encoding="utf-8")
        ingest_text(conn, f"sample:{path.name}", path.name, text)
        loaded.append(path.name)
    return loaded


def ingest_text(conn, source_id: str, source_name: str, text: str) -> int:
    pieces = chunks(text)
    vectors = embed_passages(pieces)
    rows = [
        (index, piece, vector)
        for index, (piece, vector) in enumerate(zip(pieces, vectors))
    ]
    replace_source(conn, source_id, source_name, rows)
    return len(rows)
