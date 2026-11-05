import gc
import io
import logging
import os
import threading
from datetime import datetime, timezone

from app.ingest import ingest_text
from app.store import connect, delete_absent, set_meta, source_mtimes

log = logging.getLogger("company-brain.drive")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
MAX_BYTES = 20_000_000
INTERVAL_SECONDS = 15 * 60


def configured() -> bool:
    return bool(os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON") and os.environ.get("GDRIVE_FOLDER_ID"))


def start() -> None:
    if not configured():
        log.info("Drive sync is off. GDRIVE_FOLDER_ID or the service account is unset.")
        return
    thread = threading.Thread(target=_loop, name="drive-sync", daemon=True)
    thread.start()


def _loop() -> None:
    while True:
        try:
            sync_once()
        except Exception:
            log.exception("Drive sync failed")
        threading.Event().wait(INTERVAL_SECONDS)


def sync_once() -> None:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = _account_info()
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder_id = os.environ["GDRIVE_FOLDER_ID"]
    files = _list_files(service, folder_id)
    with connect() as conn:
        mtimes = source_mtimes(conn)
    queue = [item for item in files if _needs_read(item, mtimes)]
    queue.sort(key=lambda item: (item.get("name") or "", item["id"]))
    log.info("Drive queue: %s file(s)", len(queue))
    failed = False
    for index, item in enumerate(queue, start=1):
        name = item.get("name") or item["id"]
        log.info("Drive queue %s/%s %s", index, len(queue), name)
        text = ""
        try:
            text = _read(service, item)
            with connect() as conn:
                count = ingest_text(conn, item["id"], name, text)
                modified = item.get("modifiedTime")
                if modified:
                    set_meta(conn, f"mtime:{item['id']}", modified)
            log.info("Ingested %s (%s chunks)", name, count)
        except Exception:
            failed = True
            log.exception("Failed to ingest %s", name)
        finally:
            text = ""
            gc.collect()
    with connect() as conn:
        delete_absent(conn, {item["id"] for item in files})
        if not failed:
            set_meta(conn, "last_success", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _account_info() -> dict:
    import json

    return json.loads(os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"])


def _list_files(service, folder_id: str) -> list[dict]:
    files = []
    pending = [folder_id]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        page = None
        while True:
            response = (
                service.files()
                .list(
                    q=f"'{current}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                    pageSize=100,
                    pageToken=page,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for item in response.get("files", []):
                if item["mimeType"] == "application/vnd.google-apps.folder":
                    pending.append(item["id"])
                else:
                    files.append(item)
            page = response.get("nextPageToken")
            if not page:
                break
    return files


def _needs_read(item: dict, mtimes: dict[str, str]) -> bool:
    if _kind(item) is None:
        return False
    size = int(item.get("size") or 0)
    if size > MAX_BYTES:
        return False
    previous = mtimes.get(item["id"])
    modified = item.get("modifiedTime")
    if not previous or not modified:
        return True
    return _parsed_time(modified) > _parsed_time(previous)


def _parsed_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _kind(item: dict) -> str | None:
    mime = item.get("mimeType", "")
    name = item.get("name", "").lower()
    if mime == "application/vnd.google-apps.document":
        return "gdoc"
    if mime == "application/vnd.google-apps.spreadsheet":
        return "gsheet"
    if mime == "application/vnd.google-apps.presentation":
        return "gslides"
    if mime == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith(".xlsx"):
        return "xlsx"
    if mime in {"text/plain", "text/markdown"} or name.endswith((".md", ".txt")):
        return "text"
    return None


def _read(service, item: dict) -> str:
    kind = _kind(item)
    if kind == "gdoc":
        return _download(service, item["id"], export="text/plain").decode("utf-8", "replace")
    if kind == "gsheet":
        return _download(service, item["id"], export="text/csv").decode("utf-8", "replace")
    if kind == "gslides":
        return _download(service, item["id"], export="text/plain").decode("utf-8", "replace")
    data = _download(service, item["id"])
    if kind == "pdf":
        return _pdf_text(data)
    if kind == "docx":
        from docx import Document

        document = Document(io.BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    if kind == "xlsx":
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            lines = []
            for sheet in book.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    cells = ["" if cell is None else str(cell) for cell in row]
                    if any(cells):
                        lines.append(",".join(cells))
            return "\n".join(lines)
        finally:
            book.close()
    return data.decode("utf-8", "replace")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)
    del reader
    parts = []
    for index in range(page_count):
        reader = PdfReader(io.BytesIO(data))
        parts.append(reader.pages[index].extract_text() or "")
        del reader
    return "\n".join(parts)


def _download(service, file_id: str, export: str | None = None) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    request = (
        service.files().export_media(fileId=file_id, mimeType=export)
        if export
        else service.files().get_media(fileId=file_id)
    )
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()
