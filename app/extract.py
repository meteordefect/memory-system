import json
import logging
import os
import threading
import urllib.error
import urllib.request

from app.ontology import Ontology

log = logging.getLogger("company-brain.graph")
TEXT_LIMIT = 20_000
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
MAX_TOKENS = 1024
CALL_TIMEOUT = 50


def configured() -> bool:
    return bool(os.environ.get("EXTRACTION_API_KEY", "").strip())


def prompt_for(ontology: Ontology) -> str:
    types = "\n".join(
        f"- {entity.name}: {entity.hint}" if entity.hint else f"- {entity.name}"
        for entity in ontology.entities
    )
    if ontology.relationships:
        links = "\n".join(
            f"- {item.from_type} {item.type} {item.to_type}: {item.hint}"
            if item.hint
            else f"- {item.from_type} {item.type} {item.to_type}"
            for item in ontology.relationships
        )
    else:
        links = "- none. Return an empty links list."
    return (
        "Extract a knowledge graph from one company document. "
        "Return one JSON object with keys entities and links. "
        'entities is a list of {"type", "name"}. '
        'links is a list of {"from_type", "from", "type", "to_type", "to"}. '
        "Use only these entity types:\n"
        f"{types}\n"
        "Use only these relationships:\n"
        f"{links}\n"
        "Copy the name as it is written. When a product name and a SKU are both written, include each as its own entity. "
        "Do not invent a relationship, a type, or a match between two different names. "
        "If nothing fits, return empty lists."
    )


def extract_text(ontology: Ontology, text: str) -> dict | None:
    if not configured() or not ontology.entities:
        return None
    body = text.strip()
    if not body:
        return {"entities": [], "links": []}
    raw = complete(prompt_for(ontology), body[:TEXT_LIMIT])
    if not raw:
        return None
    try:
        payload = parse_payload(raw)
    except ValueError:
        log.warning("Extraction returned no JSON object")
        return None
    from app.ontology import accept

    return accept(ontology, payload)


def parse_payload(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("json was not an object")
    return payload


def complete(prompt: str, text: str) -> str | None:
    key = os.environ.get("EXTRACTION_API_KEY", "").strip()
    if not key:
        return None
    base = os.environ.get("EXTRACTION_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
    model = os.environ.get("EXTRACTION_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if _anthropic(base):
        return _anthropic_complete(base, key, model, prompt, text) or None
    messages = {
        "model": model,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    }
    content = _post(base, key, {**messages, "response_format": {"type": "json_object"}})
    if content is None:
        content = _post(base, key, messages)
    return content or None


def _anthropic(base: str) -> bool:
    return "api.anthropic.com" in base


def _anthropic_complete(base: str, key: str, model: str, prompt: str, text: str) -> str | None:
    request = urllib.request.Request(
        f"{base}/messages",
        data=json.dumps(
            {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "temperature": 0,
                "system": prompt,
                "messages": [{"role": "user", "content": text}],
            }
        ).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": "company-brain/1.0",
        },
        method="POST",
    )
    body = _exchange(request)
    if body is None or body == "":
        return body
    try:
        return body["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        log.warning("Extraction response had no message content")
        return ""


def _post(base: str, key: str, payload: dict) -> str | None:
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "company-brain/1.0",
            "x-opencode-session": "company-brain-extraction",
        },
        method="POST",
    )
    body = _exchange(request, payload)
    if body is None or body == "":
        return body
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        log.warning("Extraction response had no message content")
        return ""


def _exchange(request: urllib.request.Request, payload: dict | None = None):
    box = {}

    def run() -> None:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                box["body"] = json.loads(response.read().decode()[:500_000])
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(CALL_TIMEOUT)
    if thread.is_alive():
        log.warning("Extraction timed out")
        return ""
    error = box.get("error")
    if isinstance(error, urllib.error.HTTPError):
        detail = error.read().decode(errors="replace")[:300]
        if error.code == 400 and payload and "response_format" in payload:
            log.info("Extraction endpoint refused JSON mode. Retrying without it.")
            return None
        log.warning("Extraction failed: HTTP %s %s", error.code, detail)
        return ""
    if isinstance(error, urllib.error.URLError):
        log.warning("Extraction request failed: %s", error.reason)
        return ""
    if error is not None:
        raise error
    return box.get("body")
