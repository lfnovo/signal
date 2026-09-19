"""Durable inbox consumer. A local OS lock serializes workers and recovery."""

import asyncio
import fcntl
import logging
from contextlib import contextmanager

from .ai import AI, chunk_text
from .config import EXTRACTION_KEYS
from .database import Database, now
from .extraction import extraction_options, run_extraction
from .topic_context import context_signature
from .topics import Topics
from .youtube_queue import (
    cooldown_until,
    defer,
    needs_youtube_extraction,
    next_pending_source,
    paced_extraction,
)

logger = logging.getLogger(__name__)


@contextmanager
def worker_lock(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / "worker.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def safe_error(exc):
    # Provider exceptions can contain response bodies, URLs or credentials.
    return f"{type(exc).__name__}: this step failed. Check your connection, model and provider settings, then retry."


async def process_source(db: Database, ai: AI, source):
    if needs_youtube_extraction(source):
        deadline = await cooldown_until(db)
        if deadline and deadline > now():
            await defer(db, source, deadline)
            return
    preferences = await db.preferences()
    overrides = source.get("reprocess_options", {}) if source.get("reprocess_requested") else {}
    preferences.update({key: overrides[key] for key in EXTRACTION_KEYS if key in overrides})
    with ai.use_preferences(preferences):
        await _process_source(db, ai, source)


async def _process_source(db: Database, ai: AI, source):
    identifier = source["id"]
    created_at = source["created_at"]

    async def update(identifier, values):
        return await db.update(identifier, values, expected_created_at=created_at)

    original_revision = source.get("revision", 0)
    reprocessing = source.get("reprocess_requested", False)
    regenerating = source.get("regenerate_requested", False)
    replacing = reprocessing or regenerating
    steering = source.get("summary_steering", "")
    published = source
    draft = dict(source.get("processing_draft") or {}) if replacing else {}
    if replacing:
        source = {
            key: source[key]
            for key in ("id", "kind", "original", "file_path", "title", "attempts")
            if key in source
        } | draft
        if regenerating:
            source.update(
                {
                    key: published[key]
                    for key in (
                        "content",
                        "chunks",
                        "extraction_settings",
                        "metadata",
                        "identified_type",
                    )
                    if key in published
                }
            )

    async def checkpoint(values):
        source.update(values)
        if replacing:
            draft.update(values)
            await update(identifier, {"processing_draft": draft})
        else:
            await update(identifier, values)

    try:
        await update(
            identifier,
            {"status": "processing", "error": "", "attempts": source.get("attempts", 0) + 1},
        )
        if not source.get("content"):
            await update(identifier, {"stage": "extracting"})
            result = await paced_extraction(
                db, source, extraction_options(ai.settings), run_extraction
            )
            if not result["content"].strip():
                raise ValueError("No readable content was extracted")
            title = result["title"] or source["title"]
            if source["kind"] == "file":
                # Extractors often use our internal hash filename as the title.
                from pathlib import Path

                if title in (Path(source["file_path"]).name, Path(source["file_path"]).stem):
                    title = source["original"]
                first_line = result["content"].strip().splitlines()[0]
                if first_line.startswith("# "):
                    title = first_line[2:].strip()[:300]
            await checkpoint(
                {
                    "content": result["content"],
                    "title": title,
                    "metadata": result["metadata"],
                    "identified_type": result["identified_type"],
                    "extraction_settings": result["extraction_settings"],
                }
            )
        if not source.get("summary") or "suggested_topics" not in source:
            await update(identifier, {"stage": "summarizing"})
            language = ai.settings.language
            official = await Topics(db).contexts()
            bundle = await ai.summary_bundle(source["content"], language, official, steering)
            source["summary"] = bundle["summary"]
            await checkpoint(
                {
                    "summary": source["summary"],
                    "suggested_topics": bundle["topics"],
                    "summary_steering": steering,
                    "summary_language": language,
                    "summary_provider": ai.settings.llm_provider,
                    "summary_model": ai.settings.llm_model,
                    "personal_relevance": bundle.get("personal_relevance", "")
                    if any(t.get("personal_context") for t in official)
                    else "",
                    "relevance_context": official,
                    "relevance_signature": context_signature(official),
                    "relevance_generated_at": now(),
                },
            )
        await update(identifier, {"stage": "embedding"})
        chunks = chunk_text(source["content"], size=db.settings.chunk_chars)
        vectors = []
        for start in range(0, 0 if regenerating else len(chunks), 32):
            vectors.extend(await ai.embed([c["text"] for c in chunks[start : start + 32]]))
        if regenerating:
            chunks = published["chunks"]
        else:
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk["embedding"] = vector
        provider = (
            published["embedding_provider"] if regenerating else ai.settings.embedding_provider
        )
        model = published["embedding_model"] if regenerating else ai.settings.embedding_model
        summary_vector = (await ai.embed([source["summary"]], provider=provider, model=model))[0]
        await Topics(db).publish(
            identifier,
            {
                **draft,
                "revision": original_revision + 1 if reprocessing else original_revision,
                "reprocess_requested": False,
                "regenerate_requested": False,
                "updated_at": now(),
                "processing_draft": {},
                "reprocess_options": {},
                "chunks": chunks,
                "summary_embedding": summary_vector,
                "embedding_provider": provider,
                "embedding_model": model,
                "embedding_dimensions": len(summary_vector),
                "status": "ready",
                "stage": "complete",
                "error": "",
            },
            source["suggested_topics"],
            expected_created_at=created_at,
        )
    except asyncio.CancelledError:
        await update(identifier, {"status": "pending"})
        raise
    except Exception as exc:
        logger.error("Source %s failed (%s)", identifier, type(exc).__name__)
        await update(identifier, {"status": "error", "error": safe_error(exc)})


async def run_worker(db: Database, ai: AI, once=False):
    """Only the lock owner may recover jobs; OS releases the lock after crashes."""
    while True:
        with worker_lock(db.settings) as acquired:
            if acquired:
                await db.query("UPDATE source SET status = 'pending' WHERE status = 'processing'")
                while True:
                    source = await next_pending_source(db)
                    if source:
                        await process_source(db, ai, source)
                    elif once:
                        return True
                    else:
                        await asyncio.sleep(1)
            elif once:
                return False
        await asyncio.sleep(1)


async def supervise_worker(db, ai):
    while True:
        try:
            await run_worker(db, ai)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Worker connection failed (%s); retrying in 5s", type(exc).__name__)
            await asyncio.sleep(5)
