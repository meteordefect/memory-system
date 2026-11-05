from app.text import chunks, rrf


def test_short_text_is_one_chunk():
    assert chunks("jasmine rice costs 42 dollars") == ["jasmine rice costs 42 dollars"]


def test_overlap_moves_forward():
    words = [str(i) for i in range(1000)]
    parts = chunks(" ".join(words), size=130, overlap=26)
    assert len(parts) > 1
    assert parts[0].split()[0] == "0"
    assert parts[1].split()[0] != "0"


def test_rrf_prefers_a_document_in_both_lists():
    both = ("a", 0)
    only_vector = ("b", 0)
    ranked = rrf([[both, only_vector], [both]])
    assert ranked[0] == both
