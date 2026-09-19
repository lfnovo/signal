"""Persisted pacing for YouTube extraction attempts; never sleep inside a job."""

import random
from datetime import timedelta
from urllib.parse import urlsplit

from .database import now, one


def is_youtube_source(source):
    if source.get("kind") != "url":
        return False
    try:
        host = (urlsplit(source.get("original", "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host in {"youtu.be", "youtube.com", "youtube-nocookie.com"} or host.endswith(
        (".youtube.com", ".youtu.be", ".youtube-nocookie.com")
    )


def needs_youtube_extraction(source):
    if not is_youtube_source(source):
        return False
    if source.get("regenerate_requested"):
        return not source.get("content")
    if source.get("reprocess_requested"):
        return not (source.get("processing_draft") or {}).get("content")
    return not source.get("content")


async def cooldown_until(db):
    record = one(await db.query("SELECT * FROM setting:youtube_queue"))
    return record.get("next_extraction_at") if record else None


async def record_cooldown(db, seconds):
    deadline = now() + timedelta(seconds=seconds)
    await db.query(
        "UPSERT setting:youtube_queue SET next_extraction_at = $deadline, "
        "interval_seconds = $seconds, updated_at = $current",
        {"deadline": deadline, "seconds": seconds, "current": now()},
    )
    return deadline


async def defer(db, source, deadline):
    if source.get("stage") != "youtube_wait" or source.get("youtube_resume_at") != deadline:
        await db.update(
            source["id"],
            {"status": "pending", "stage": "youtube_wait", "youtube_resume_at": deadline},
            expected_created_at=source["created_at"],
        )


async def next_pending_source(db):
    # Skip cooling-down videos rather than blocking the FIFO queue behind them.
    rows = await db.query("SELECT * FROM source WHERE status = 'pending' ORDER BY created_at ASC")
    deadline = await cooldown_until(db) if any(needs_youtube_extraction(s) for s in rows) else None
    current = now()
    for source in rows:
        if deadline and deadline > current and needs_youtube_extraction(source):
            await defer(db, source, deadline)
        else:
            return source
    return None


async def paced_extraction(db, source, options, extract):
    if not is_youtube_source(source):
        return await extract(source, options)
    seconds = random.randint(180, 300)
    # Reserve before network access, so a crash also leaves a persisted cooldown.
    await record_cooldown(db, seconds)
    try:
        return await extract(source, options)
    finally:
        # Give the next attempt a full 3–5 minute breather after success or failure.
        await record_cooldown(db, seconds)
