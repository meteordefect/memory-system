#!/usr/bin/env python3
"""Ask the company bank. Default is recall. --reflect asks for a written answer."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BANK = "company"


def load_dotenv():
    path = Path(".env")
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Set {name} in .env. See README.md.")
    if name == "HINDSIGHT_URL":
        return value.rstrip("/")
    return value


def call(url, key, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"{exc.code} from {url}\n{detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach {url}\n{exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach {url}\n{exc.reason}")


def print_recall(body):
    results = body.get("results") or []
    if not results:
        print(json.dumps(body, indent=2))
        return
    for item in results:
        if not isinstance(item, dict):
            print(item)
            continue
        text = item.get("text") or item.get("content")
        context = item.get("context")
        if text:
            print(text)
            if context:
                print(f"({context})")
        else:
            print(json.dumps(item, indent=2))
        print()


def main():
    load_dotenv()
    reflect = "--reflect" in sys.argv
    words = [arg for arg in sys.argv[1:] if arg != "--reflect"]
    if not words:
        sys.exit('Usage: python scripts/ask.py "What is the price of jasmine rice?"')
    base = required("HINDSIGHT_URL")
    key = required("HINDSIGHT_API_KEY")
    query = " ".join(words)
    if reflect:
        body = call(
            f"{base}/v1/default/banks/{BANK}/reflect",
            key,
            {"query": query, "budget": "low"},
        )
        print(body.get("text") or json.dumps(body, indent=2))
        return
    body = call(
        f"{base}/v1/default/banks/{BANK}/memories/recall",
        key,
        {"query": query, "budget": "mid"},
    )
    print_recall(body)


if __name__ == "__main__":
    main()
