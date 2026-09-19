"""Content-addressed intake shared by web, extension and CLI."""

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .database import Database, now


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("Enter a valid HTTP or HTTPS URL.")
    if parts.username or parts.password:
        raise ValueError("URLs containing credentials are not supported.")
    host = parts.hostname.encode("idna").decode().lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port and (parts.scheme.lower(), port) not in [("http", 80), ("https", 443)]:
        host += f":{port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))


async def _insert(db: Database, fingerprint: str, kind: str, original: str, path=""):
    identifier = hashlib.sha256(fingerprint.encode()).hexdigest()
    existing = await db.get(identifier)
    if existing:
        return existing, False
    data = {
        "fingerprint": fingerprint,
        "kind": kind,
        "original": original,
        "file_path": path,
        "title": Path(original).name if kind == "file" else original,
        "status": "pending",
        "collection": "inbox",
        "stage": "waiting",
        "error": "",
        "content": "",
        "summary": "",
        "created_at": now(),
        "updated_at": now(),
        "attempts": 0,
    }
    try:
        return await db.create_source(identifier, data), True
    except Exception:
        # A concurrent capture may have created the deterministic record first.
        existing = await db.get(identifier)
        if existing:
            return existing, False
        raise


async def add_url(db: Database, url: str):
    url = normalize_url(url)
    return await _insert(db, "url:" + url, "url", url)


async def add_file(db: Database, file, filename: str):
    """Copy before enqueueing, so moving the original cannot break the job."""
    uploads = db.settings.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    name = Path(filename.replace("\\", "/")).name or "file"
    suffix = Path(name).suffix.lower()[:16]
    descriptor, temporary = tempfile.mkstemp(dir=uploads)
    try:
        with os.fdopen(descriptor, "wb") as output:
            while block := file.read(1024 * 1024):
                total += len(block)
                if total > db.settings.max_upload_bytes:
                    raise ValueError("This file is over the 100 MB limit.")
                digest.update(block)
                output.write(block)
        if not total:
            raise ValueError("This file is empty.")
        fingerprint = "file:" + digest.hexdigest()
        identifier = hashlib.sha256(fingerprint.encode()).hexdigest()
        existing = await db.get(identifier)
        if existing:
            return existing, False
        destination = uploads / (digest.hexdigest() + suffix)
        os.replace(temporary, destination)
        return await _insert(db, fingerprint, "file", name, str(destination))
    finally:
        Path(temporary).unlink(missing_ok=True)
