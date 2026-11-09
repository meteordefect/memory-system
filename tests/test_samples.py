from pathlib import Path

from app.ingest import ingest_text, new_sample_files, sample_markdown


def test_samples_include_the_demo_folder():
    directory = Path("sample")
    relative = {path.relative_to(directory).as_posix() for path in sample_markdown(directory)}
    assert relative == {
        "bolt-price.md",
        "demo/email-harbour-pantry-short-delivery.md",
        "demo/email-kedron-soy-return.md",
        "demo/invoice-1044.md",
        "demo/invoice-1047.md",
        "price-list.md",
        "rice-price-2024.md",
        "staff-lunch.md",
    }


def test_boot_skips_samples_already_stored():
    directory = Path("sample")
    pending = new_sample_files(directory, sample_markdown(directory), {"sample:price-list.md"})
    assert [path.relative_to(directory).as_posix() for path in pending] == [
        "bolt-price.md",
        "demo/email-harbour-pantry-short-delivery.md",
        "demo/email-kedron-soy-return.md",
        "demo/invoice-1044.md",
        "demo/invoice-1047.md",
        "rice-price-2024.md",
        "staff-lunch.md",
    ]


def test_a_graph_error_does_not_fail_the_save(monkeypatch):
    monkeypatch.setattr("app.ingest.chunks", lambda text: ["hello"])
    monkeypatch.setattr("app.ingest.embed_passages", lambda pieces: [[0.0]])
    monkeypatch.setattr("app.ingest.replace_source", lambda *args: None)

    def boom(*args, **kwargs):
        raise RuntimeError("graph")

    monkeypatch.setattr("app.ingest.refresh_source", boom)
    assert ingest_text(None, "id", "name.md", "hello") == 1
