#!/usr/bin/env python3
"""Load one or more files into the company memory bank."""

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BANK = "company"
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".html", ".htm"}


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


def call(url, key, data=None, headers=None, timeout=300):
    hdrs = {"Authorization": f"Bearer {key}"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"{exc.code} from {url}\n{detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach {url}\n{exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"Could not reach {url}\n{exc.reason}")


def encode_multipart(path, meta):
    boundary = f"----hindsight{uuid.uuid4().hex}"
    filename = path.name.replace('"', "").replace("\r", "").replace("\n", "")
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_bytes = path.read_bytes()
    request_json = json.dumps({"files_metadata": [meta]}).encode()
    chunks = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_bytes,
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="request"\r\n',
        b"Content-Type: application/json\r\n\r\n",
        request_json,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


def wait_for_operation(base, key, operation_id):
    url = f"{base}/v1/default/banks/{BANK}/operations/{operation_id}"
    deadline = time.time() + 300
    while time.time() < deadline:
        body = call(url, key, timeout=60)
        status = body.get("status")
        print(f"  {operation_id} {status}")
        if status == "completed":
            return
        if status in {"failed", "cancelled", "not_found"}:
            sys.exit(body.get("error_message") or f"operation {status}")
        time.sleep(3)
    sys.exit(f"operation {operation_id} still running after 5 minutes")


def retain_text(base, key, path):
    meta_context = f"Source file: {path.name}"
    payload = {
        "async": False,
        "items": [
            {
                "content": path.read_text(encoding="utf-8"),
                "document_id": path.stem,
                "context": meta_context,
                "tags": ["all"],
            }
        ],
    }
    body = call(
        f"{base}/v1/default/banks/{BANK}/memories",
        key,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    print(f"Retained {path.name} ({body.get('items_count', 0)} item)")


def retain_file(base, key, path):
    meta = {
        "document_id": path.stem,
        "context": f"Source file: {path.name}",
        "tags": ["all"],
    }
    body, content_type = encode_multipart(path, meta)
    result = call(
        f"{base}/v1/default/banks/{BANK}/files/retain",
        key,
        data=body,
        headers={"Content-Type": content_type},
    )
    operation_ids = result.get("operation_ids") or []
    if not operation_ids:
        sys.exit(f"No operation id returned for {path.name}: {result}")
    print(f"Uploaded {path.name}")
    for operation_id in operation_ids:
        wait_for_operation(base, key, operation_id)


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        sys.exit("Usage: python scripts/retain.py <file-or-folder> [more]")
    base = required("HINDSIGHT_URL")
    key = required("HINDSIGHT_API_KEY")
    call(f"{base}/health", key, timeout=30)

    paths = []
    for arg in sys.argv[1:]:
        path = Path(arg)
        if path.is_dir():
            found = [p for p in sorted(path.iterdir()) if p.is_file() and not p.name.startswith(".")]
            if not found:
                sys.exit(f"No files in {path}")
            paths.extend(found)
        elif path.is_file():
            paths.append(path)
        else:
            sys.exit(f"Not a file: {path}")

    for path in paths:
        if path.suffix.lower() in TEXT_SUFFIXES:
            retain_text(base, key, path)
        else:
            retain_file(base, key, path)

    print(f"Bank: {BANK}")
    print(f"Claude URL: {base}/mcp/{BANK}/")


if __name__ == "__main__":
    main()
