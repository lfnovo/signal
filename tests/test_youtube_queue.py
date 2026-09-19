import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.database import Database, public
from signal_inbox.intake import add_url
from signal_inbox.web import create_app
from signal_inbox.worker import process_source, run_worker
from signal_inbox.youtube_queue import (
    cooldown_until,
    is_youtube_source,
    needs_youtube_extraction,
    paced_extraction,
    record_cooldown,
)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/watch?v=abc", True),
        ("https://youtu.be/abc", True),
        ("https://m.youtube.com/shorts/abc", True),
        ("https://music.youtube.com/watch?v=abc", True),
        ("https://www.youtube.com/live/abc", True),
        ("https://www.youtube-nocookie.com/embed/abc", True),
        ("https://youtube.com.evil.test/watch?v=abc", False),
        ("https://example.com/?url=https://youtube.com/abc", False),
        ("https://[broken", False),
    ],
)
def test_youtube_host_detection(url, expected):
    assert is_youtube_source({"kind": "url", "original": url}) is expected
    assert not is_youtube_source({"kind": "file", "original": url})


def test_only_jobs_that_need_extraction_are_throttled():
    source = {"kind": "url", "original": "https://youtu.be/abc", "content": "Saved transcript"}
    assert not needs_youtube_extraction(source)
    assert not needs_youtube_extraction({**source, "regenerate_requested": True})
    assert needs_youtube_extraction({**source, "reprocess_requested": True})
    assert not needs_youtube_extraction(
        {**source, "reprocess_requested": True, "processing_draft": {"content": "Checkpoint"}}
    )
    assert needs_youtube_extraction({**source, "content": ""})


@pytest.mark.asyncio
async def test_queue_skips_youtube_without_blocking_other_work_and_survives_restart(
    db, monkeypatch
):
    clock = [datetime(2030, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr("signal_inbox.youtube_queue.now", lambda: clock[0])
    monkeypatch.setattr("signal_inbox.worker.now", lambda: clock[0])
    intervals = iter([210, 270])

    def jitter(low, high):
        assert (low, high) == (180, 300)
        return next(intervals)

    monkeypatch.setattr("signal_inbox.youtube_queue.random.randint", jitter)
    sources = []
    for url in [
        "https://youtu.be/first",
        "https://youtube.com/watch?v=second",
        "https://example.com/article",
    ]:
        source, _ = await add_url(db, url)
        sources.append(source)
    calls = []

    async def extract(source, options):
        calls.append(source["original"])
        if "second" in source["original"]:
            raise RuntimeError("A provider failure")
        if is_youtube_source(source):
            # The delay is measured from the end of extraction, not its beginning.
            clock[0] += timedelta(seconds=45)
        return {
            "title": "Extracted",
            "content": "Useful text about books.",
            "metadata": {},
            "identified_type": "article",
            "extraction_settings": {},
        }

    monkeypatch.setattr("signal_inbox.worker.run_extraction", extract)
    ai = FakeAI(db.settings)
    await run_worker(db, ai, once=True)
    first, second, article = [await db.get(s["id"]) for s in sources]
    assert first["status"] == article["status"] == "ready"
    assert second["status"] == "pending" and second["stage"] == "youtube_wait"
    assert second.get("attempts", 0) == 0
    assert calls == [sources[0]["original"], sources[2]["original"]]
    deadline = await cooldown_until(db)
    assert deadline == clock[0] + timedelta(seconds=210)
    assert second["youtube_resume_at"] == deadline
    restarted = Database(db.settings)
    assert await cooldown_until(restarted) == deadline
    clock[0] = deadline - timedelta(seconds=1)
    await run_worker(restarted, ai, once=True)
    assert len(calls) == 2
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    sid = public(second["id"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        assert "YouTube cooldown" in (await client.get("/")).text
        assert "A short YouTube breather" in (await client.get(f"/sources/{sid}")).text
        assert "A short YouTube breather" in (await client.get(f"/sources/{sid}/triage")).text
    clock[0] = deadline
    await run_worker(restarted, ai, once=True)
    assert (await db.get(second["id"]))["status"] == "error"
    assert await cooldown_until(db) == clock[0] + timedelta(seconds=270)
    # A manual retry respects the same persisted cooldown and spends no attempt.
    await db.update(second["id"], {"status": "pending"})
    await process_source(db, ai, await db.get(second["id"]))
    assert len(calls) == 3
    assert (await db.get(second["id"]))["stage"] == "youtube_wait"


@pytest.mark.asyncio
async def test_checkpoint_and_regeneration_proceed_during_cooldown(db, monkeypatch):
    clock = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("signal_inbox.youtube_queue.now", lambda: clock)
    monkeypatch.setattr("signal_inbox.worker.now", lambda: clock)
    await record_cooldown(db, 300)
    source, _ = await add_url(db, "https://youtu.be/checkpoint")
    await db.update(source["id"], {"content": "Already extracted text"})

    async def forbidden(*args):
        raise AssertionError("Must use the existing transcript")

    monkeypatch.setattr("signal_inbox.worker.run_extraction", forbidden)
    ai = FakeAI(db.settings)
    await run_worker(db, ai, once=True)
    assert (await db.get(source["id"]))["status"] == "ready"
    await db.update(
        source["id"], {"status": "pending", "regenerate_requested": True, "processing_draft": {}}
    )
    await run_worker(db, ai, once=True)
    assert (await db.get(source["id"]))["status"] == "ready"
    assert await cooldown_until(db) == clock + timedelta(seconds=300)
    # A reprocess with no extraction checkpoint waits despite its published content.
    await db.update(
        source["id"],
        {
            "status": "pending",
            "regenerate_requested": False,
            "reprocess_requested": True,
            "processing_draft": {},
        },
    )
    await run_worker(db, ai, once=True)
    assert (await db.get(source["id"]))["stage"] == "youtube_wait"


@pytest.mark.asyncio
async def test_cancellation_leaves_a_persisted_cooldown(db, monkeypatch):
    clock = [datetime(2030, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr("signal_inbox.youtube_queue.now", lambda: clock[0])
    monkeypatch.setattr("signal_inbox.youtube_queue.random.randint", lambda low, high: 180)
    entered = asyncio.Event()

    async def extraction(*args):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        paced_extraction(db, {"kind": "url", "original": "https://youtu.be/cancel"}, {}, extraction)
    )
    await entered.wait()
    assert await cooldown_until(db) == clock[0] + timedelta(seconds=180)
    clock[0] += timedelta(seconds=30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await cooldown_until(db) == clock[0] + timedelta(seconds=180)
