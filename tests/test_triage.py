import asyncio
import io
from pathlib import Path

import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.database import public
from signal_inbox.intake import add_file, add_url
from signal_inbox.topics import Topics
from signal_inbox.web import create_app
from signal_inbox.worker import process_source

pytestmark = pytest.mark.asyncio


async def test_triage_legacy_defaults_moves_and_topic_lists(db):
    ai = FakeAI(db.settings)
    source, _ = await add_file(db, io.BytesIO(b"# Triage\nA keeper."), "triage.md")
    await process_source(db, ai, source)
    sid = public(source["id"])
    await db.query("UPDATE $source UNSET collection", {"source": source["id"]})
    before = await db.get(sid)
    assert len(await db.sources(collection="inbox")) == 1
    assert await db.sources(collection="library") == []
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        inbox = await client.get("/")
        assert "Leitura" in inbox.text
        assert (
            await client.put(f"/api/sources/{sid}/collection", json={"collection": "library"})
        ).status_code == 200
        assert await db.sources(collection="inbox") == []
        assert len(await db.sources(collection="library")) == 1
        saved = await db.get(sid)
        for key in ("content", "summary", "chunks", "created_at", "status"):
            assert saved[key] == before[key]
        assert (await client.get("/library")).status_code == 200
        assert "Triage" in (await client.get("/library")).text
        topic = (await Topics(db).for_source(sid))[0]
        page = await client.get("/topics/" + public(topic["id"]))
        assert page.status_code == 200 and 'id="topics-workspace"' in page.text
        inspected = (await client.get("/api/topics/" + public(topic["id"]))).json()
        assert inspected["sources"][0]["collection"] == "library"
        assert inspected["sources"][0]["title"] == "Triage"
        duplicate, created = await add_file(db, io.BytesIO(b"# Triage\nA keeper."), "other.md")
        assert not created and duplicate["collection"] == "library"
        assert (
            await client.put(f"/api/sources/{sid}/collection", json={"collection": "inbox"})
        ).status_code == 200
        assert len(await db.sources(collection="inbox")) == 1
        assert (
            await client.put(f"/api/sources/{sid}/collection", json={"collection": "other"})
        ).status_code == 422


async def test_confirmed_delete_removes_source_copy_chat_links_not_global_topics(db):
    ai = FakeAI(db.settings)
    data = b"# Delete test\nSaved file."
    source, _ = await add_file(db, io.BytesIO(data), "delete.md")
    await process_source(db, ai, source)
    sid = public(source["id"])
    stored = Path(source["file_path"])
    await db.save_turn(sid, "Q", "A", [], "fake")
    topics_before = await Topics(db).list()
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        endpoint = f"/api/sources/{sid}"
        assert (await client.delete(endpoint)).status_code == 422
        assert (
            await client.request("DELETE", endpoint, json={"confirm": False})
        ).status_code == 422
        assert stored.exists() and await db.get(sid)
        response = await client.request("DELETE", endpoint, json={"confirm": True})
        assert response.status_code == 200
        assert await db.get(sid) is None and not stored.exists()
        assert await db.history(sid) == []
        assert await Topics(db).for_source(sid) == []
        assert len(await Topics(db).list()) == len(topics_before)
        assert (await client.get("/sources/" + sid)).status_code == 404
        assert not list(stored.parent.glob(".delete-*"))
        fresh, created = await add_file(db, io.BytesIO(data), "again.md")
        assert created and fresh["collection"] == "inbox" and Path(fresh["file_path"]).exists()


async def test_deleted_inflight_job_cannot_modify_recaptured_source(db):
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedAI(FakeAI):
        async def summary_bundle(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return {"summary": "Stale result", "topics": ["Stale Topic"]}

    ai = PausedAI(db.settings)
    source, _ = await add_url(db, "https://example.com/triage")
    await db.update(source["id"], {"content": "Already extracted", "title": "Old source"})
    task = asyncio.create_task(process_source(db, ai, await db.get(source["id"])))
    await asyncio.wait_for(entered.wait(), 10)
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    sid = public(source["id"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        assert (
            await client.request("DELETE", f"/api/sources/{sid}", json={"confirm": True})
        ).status_code == 200
        fresh, created = await add_url(db, "https://example.com/triage")
        assert created
        release.set()
        await task
        saved = await db.get(sid)
        assert saved["status"] == "pending" and saved["summary"] == ""
        assert saved["created_at"] == fresh["created_at"]
        assert await Topics(db).list() == []


async def test_triage_reader_renders_safe_summary_topics_and_full_page_link(db):
    source, _ = await add_url(db, "https://example.com/triage-fragment")
    sid = public(source["id"])
    await db.update(
        sid, {"summary": "**Review me** <script>bad()</script>", "content": "Full text"}
    )
    await Topics(db).create("A useful topic", source["id"])
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        response = await client.get(f"/sources/{sid}/triage")
        assert response.status_code == 200
        assert "<strong>Review me</strong>" in response.text
        assert "<script>" not in response.text
        assert "A useful topic" in response.text and 'id="topic-editor"' in response.text
        assert f'href="/sources/{sid}"' in response.text
        assert 'data-triage-action="collection"' in response.text
        assert (await client.get("/sources/" + "f" * 64 + "/triage")).status_code == 404
        inbox = await client.get("/")
        assert 'id="triage-reader"' in inbox.text and 'id="url-form"' in inbox.text
