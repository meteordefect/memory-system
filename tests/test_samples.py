from pathlib import Path

from app.ingest import new_sample_files, sample_markdown


def test_demo_samples_are_in_the_folder():
    names = {path.name for path in sample_markdown(Path("sample"))}
    assert names == {
        "bolt-price.md",
        "price-list.md",
        "rice-price-2024.md",
        "staff-lunch.md",
    }


def test_boot_skips_samples_already_stored():
    pending = new_sample_files(sample_markdown(Path("sample")), {"sample:price-list.md"})
    assert [path.name for path in pending] == [
        "bolt-price.md",
        "rice-price-2024.md",
        "staff-lunch.md",
    ]
