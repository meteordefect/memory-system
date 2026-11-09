import io
import json
import urllib.error

from app.extract import complete, extract_text, parse_payload, prompt_for
from app.ontology import EntityType, Ontology, Relationship


def _ontology() -> Ontology:
    return Ontology(
        entities=(EntityType("Person", "A named individual."),),
        relationships=(Relationship("Person", "works_at", "Company"),),
        aliases=(),
        digest="test",
    )


def test_prompt_is_built_from_the_ontology():
    text = prompt_for(_ontology())
    assert "Person: A named individual." in text
    assert "Person works_at Company" in text
    assert "include each as its own entity" in text


def test_fenced_json_parses():
    assert parse_payload('```json\n{"entities": [], "links": []}\n```') == {
        "entities": [],
        "links": [],
    }


def test_complete_is_skipped_without_a_key(monkeypatch):
    monkeypatch.delenv("EXTRACTION_API_KEY", raising=False)

    def boom(request, timeout=0):
        raise AssertionError("request")

    monkeypatch.setattr("app.extract.urllib.request.urlopen", boom)
    assert complete("prompt", "hello") is None
    assert extract_text(_ontology(), "Arun Shah") is None


def test_json_mode_refusal_retries_without_it(monkeypatch):
    monkeypatch.setenv("EXTRACTION_API_KEY", "test-key")
    monkeypatch.setenv("EXTRACTION_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("EXTRACTION_MODEL", "demo-model")
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": '{"entities": [], "links": []}'}}]}
            ).encode()

    def fake_open(request, timeout=0):
        calls.append(json.loads(request.data.decode()))
        assert request.get_header("Authorization") == "Bearer test-key"
        assert request.get_header("User-agent") == "company-brain/1.0"
        assert request.get_header("X-opencode-session") == "company-brain-extraction"
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "bad",
                None,
                io.BytesIO(b"response_format is unsupported"),
            )
        return Response()

    monkeypatch.setattr("app.extract.urllib.request.urlopen", fake_open)
    assert complete("prompt", "Arun") == '{"entities": [], "links": []}'
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert calls[1]["model"] == "demo-model"
