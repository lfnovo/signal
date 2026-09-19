import asyncio

import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.database import public
from signal_inbox.intake import add_url
from signal_inbox.topics import Topics
from signal_inbox.web import create_app
from signal_inbox.worker import process_source

pytestmark = pytest.mark.asyncio


async def test_title_edit_validation_and_preservation_during_processing(db, monkeypatch):
    source, _ = await add_url(db, "https://example.com/inline-title")
    sid = public(source["id"])
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        for title in ["", "   ", "a" * 501]:
            assert (
                await client.put(f"/api/sources/{sid}/title", json={"title": title})
            ).status_code == 422
        assert (
            await client.put("/api/sources/missing/title", json={"title": "Missing"})
        ).status_code == 404

        async def extraction(*args):
            # Edit after the worker took its source snapshot, while extraction runs.
            response = await client.put(
                f"/api/sources/{sid}/title", json={"title": "  My <research> title  "}
            )
            assert response.json()["title"] == "My <research> title"
            return {
                "title": "Extractor title",
                "content": "A useful piece about 400 books.",
                "metadata": {},
                "identified_type": "article",
                "extraction_settings": {},
            }

        monkeypatch.setattr("signal_inbox.worker.run_extraction", extraction)
        await process_source(db, FakeAI(db.settings), source)
        saved = await db.get(sid)
        assert saved["status"] == "ready", saved.get("error")
        assert saved["title"] == saved["title_override"] == "My <research> title"
        for path in [f"/sources/{sid}", f"/sources/{sid}/triage"]:
            html = (await client.get(path)).text
            assert "data-source-title=" in html and "My &lt;research&gt; title" in html
        assert (
            await client.put(f"/api/sources/{sid}/title", json={"title": "A better title"})
        ).status_code == 200
        after = await db.get(sid)
        for key in ["content", "summary", "chunks", "summary_embedding", "created_at", "status"]:
            assert after[key] == saved[key]
        # Reprocessing publishes an extracted title in its draft; the manual one wins.
        await Topics(db).publish(
            source["id"],
            {"title": "Reprocessed title"},
            [],
            expected_created_at=source["created_at"],
        )
        assert (await db.get(sid))["title"] == "A better title"


async def test_context_inline_updates_are_independent_and_can_be_cleared(db):
    topics = Topics(db)
    topic = await topics.create("Research")
    tid = public(topic["id"])
    await topics.save_context(tid, "Original definition", "Original interest")
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        responses = await asyncio.gather(
            *[
                client.patch(f"/api/topics/{tid}/context", json={key: value})
                for key, value in [
                    ("definition", "New definition"),
                    ("personal_context", "New interest"),
                ]
            ]
        )
        assert all(r.status_code == 200 for r in responses)
        saved = await topics.get(tid)
        assert (
            saved["definition"] == "New definition" and saved["personal_context"] == "New interest"
        )
        response = await client.patch(f"/api/topics/{tid}/context", json={"definition": ""})
        assert (
            response.json()["definition"] == ""
            and response.json()["personal_context"] == "New interest"
        )
        assert (
            await client.patch(f"/api/topics/{tid}/context", json={"definition": "a" * 12001})
        ).status_code == 422
        assert (
            await client.patch(f"/api/topics/{tid}/context", json={"official": False})
        ).status_code == 422
        assert (
            await client.patch("/api/topics/missing/context", json={"definition": ""})
        ).status_code == 404
