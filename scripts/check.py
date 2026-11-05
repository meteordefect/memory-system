#!/usr/bin/env python3
"""Show that the service is up, and that a caller with no key is refused."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


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


def get(url, key=None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach {url}\n{exc.reason}")


def main():
    load_dotenv()
    base = os.environ.get("HINDSIGHT_URL", "").strip().rstrip("/")
    if not base or "xxxx" in base:
        sys.exit("Set HINDSIGHT_URL in .env to the service URL from Render.")
    status, body = get(f"{base}/health")
    print(f"health {status}")
    print(body)
    req = urllib.request.Request(
        f"{base}/v1/default/banks/company/memories/recall",
        data=json.dumps({"query": "rice"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach the recall URL\n{exc.reason}")
    print(f"recall without a key: {code}")
    if code != 401:
        sys.exit("Expected 401. The lock is not on. Check the tenant variables in render.yaml.")
    print("Locked.")


if __name__ == "__main__":
    main()
