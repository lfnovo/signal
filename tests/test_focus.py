import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.database import public
from signal_inbox.intake import add_url
from signal_inbox.web import create_app


@pytest.mark.asyncio
async def test_focus_is_independent_of_collection_and_preserved_on_duplicate(db):
    first, _ = await add_url(db, "https://example.com/focus")
    second, _ = await add_url(db, "https://example.com/other")
    sid = public(first["id"])
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        endpoint = f"/api/sources/{sid}/focus"
        assert await db.sources(focused=True) == []
        assert (await client.put(endpoint, json={"focused": True})).status_code == 200
        saved = await db.get(sid)
        assert saved["collection"] == "inbox" and saved["status"] == "pending"
        assert saved["created_at"] == first["created_at"]
        assert len(await db.sources(collection="inbox", focused=True)) == 1
        assert len((await client.get("/api/sources?focused=true")).json()) == 1
        assert (await client.get("/focus")).status_code == 200
        assert "/sources/" + sid in (await client.get("/focus")).text
        assert "/sources/" + public(second["id"]) not in (await client.get("/focus")).text
        await client.put(f"/api/sources/{sid}/collection", json={"collection": "library"})
        duplicate, created = await add_url(db, "https://example.com/focus")
        assert not created and duplicate["focused"] and duplicate["collection"] == "library"
        assert len(await db.sources(collection="library", focused=True)) == 1
        assert (await client.put(endpoint, json={"focused": "yes"})).status_code == 422
        assert (await client.put(endpoint, json={"focused": False})).status_code == 200
        assert await db.sources(focused=True) == []
        assert (await db.get(sid))["collection"] == "library"
        assert (
            await client.put("/api/sources/" + "f" * 64 + "/focus", json={"focused": True})
        ).status_code == 404
