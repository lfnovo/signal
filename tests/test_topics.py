import io

import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.database import public
from signal_inbox.intake import add_file
from signal_inbox.topics import Topics
from signal_inbox.web import create_app
from signal_inbox.worker import process_source

pytestmark = pytest.mark.asyncio


async def test_topic_global_lifecycle_and_manual_associations(db):
    topics = Topics(db)
    source, _ = await add_file(db, io.BytesIO(b"# Books\n400 books."), "books.md")
    second, _ = await add_file(db, io.BytesIO(b"# Other\n300 books."), "other.md")
    ai = FakeAI(db.settings)
    await process_source(db, ai, source)
    await process_source(db, ai, second)
    items = await topics.list()
    assert len(items) == 3 and all(not item["official"] for item in items)
    assert all(len(item["sources"]) == 2 for item in items)
    sid = public(source["id"])
    tid = public(items[0]["id"])
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        assert (await client.get("/topics")).status_code == 200
        assert (await client.get("/topics/" + tid)).status_code == 200
        assert (await client.post(f"/api/topics/{tid}/approve")).status_code == 200
        assert (await topics.get(tid))["official"]
        assert (
            await client.put(f"/api/topics/{tid}", json={"name": "My Books"})
        ).status_code == 200
        assert any(t["name"] == "My Books" for t in await topics.for_source(second["id"]))
        created = await client.post(f"/api/sources/{sid}/topics", json={"name": "  New   Thread  "})
        assert created.status_code == 201
        target = created.json()["id"]
        assert created.json()["official"] and created.json()["name"] == "New Thread"
        duplicate = await client.post("/api/topics", json={"name": "new thread"})
        assert duplicate.json()["id"] == target
        assert (await client.put(f"/api/sources/{sid}/topics/{tid}")).status_code == 200
        merge = await client.post(f"/api/topics/{tid}/merge", json={"target": target})
        assert merge.status_code == 200
        assert await topics.get(tid) is None
        assert len(await topics.sources(target)) == 2
        assert len([t for t in await topics.for_source(sid) if public(t["id"]) == target]) == 1
        assert (
            await client.post(f"/api/topics/{target}/merge", json={"target": target})
        ).status_code == 400
        assert (await client.delete(f"/api/sources/{sid}/topics/{target}")).status_code == 200
        assert len(await topics.sources(target)) == 1
        assert (await client.delete(f"/api/topics/{target}")).status_code == 200
        assert await topics.get(target) is None
        assert (await db.get(sid))["content"]
        assert (await db.get(second["id"]))["content"]
        assert (await client.post("/api/topics", json={"name": "   "})).status_code == 400


async def test_regenerate_steering_official_catalog_and_safe_retry(db, monkeypatch):
    topics = Topics(db)
    ai = FakeAI(db.settings)
    source, _ = await add_file(db, io.BytesIO(b"# Books\n400 books."), "books.md")
    await process_source(db, ai, source)
    sid = public(source["id"])
    original = await db.get(sid)
    initial = await topics.for_source(sid)
    kept, removed = initial[:2]
    await topics.attach(sid, kept["id"])
    await topics.attach(sid, removed["id"], remove=True)
    official = await topics.create("Official Knowledge")
    seen = []

    async def bundle(text, language, official_topics, steering=""):
        seen.append((text, language, official_topics, steering))
        return {
            "summary": "A fresh focus.",
            "topics": [removed["name"], "Fresh Topic", official["name"]],
        }

    async def no_extraction(*args, **kwargs):
        raise AssertionError("Summary regeneration must not extract again")

    monkeypatch.setattr(ai, "summary_bundle", bundle)
    monkeypatch.setattr("signal_inbox.worker.run_extraction", no_extraction)
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        endpoint = f"/api/sources/{sid}/regenerate"
        assert (
            await client.post(endpoint, json={"steering": "Focus on practical applications"})
        ).status_code == 202
        assert (await client.post(endpoint, json={})).status_code == 409
        assert (await client.post(f"/api/sources/{sid}/reprocess", json={})).status_code == 409
        ai.fail_embedding = True
        await process_source(db, ai, await db.get(sid))
        failed = await db.get(sid)
        assert failed["status"] == "error" and failed["summary"] == original["summary"]
        assert not any(t["name"] == "Fresh Topic" for t in await topics.list())
        ai.fail_embedding = False
        await client.post(f"/api/sources/{sid}/retry")
        await process_source(db, ai, await db.get(sid))
        saved = await db.get(sid)
        assert saved["status"] == "ready" and saved["summary"] == "A fresh focus."
        for key in ("content", "chunks", "created_at", "revision", "extraction_settings"):
            assert saved[key] == original[key]
        assert len(seen) == 1
        assert [t["name"] for t in seen[0][2]] == ["Official Knowledge"]
        assert seen[0][3] == "Focus on practical applications"
        attached = await topics.for_source(sid)
        assert {t["name"] for t in attached} == {kept["name"], "Fresh Topic", "Official Knowledge"}
        page = await client.get(f"/sources/{sid}")
        assert page.status_code == 200
        assert page.text.count('id="regenerate-dialog"') == 1
        assert page.text.count('id="reprocess-dialog"') == 1
        assert "<title>Books / Signal</title>" in page.text


async def test_merge_aliases_and_deleted_suggestions_do_not_return(db):
    topics = Topics(db)
    source, _ = await add_file(db, io.BytesIO(b"Example"), "example.txt")
    await topics.publish(source["id"], {}, ["First", "Second", "Discard"])
    ids = {t["name"]: public(t["id"]) for t in await topics.list()}
    await topics.merge(ids["First"], ids["Second"])
    await topics.delete(ids["Discard"])
    await topics.publish(source["id"], {}, ["First", "Discard", "Other"])
    assert {t["name"] for t in await topics.for_source(source["id"])} == {"Second", "Other"}


async def test_renamed_topic_handles_inflight_old_names_and_conflicts(db):
    topics = Topics(db)
    source, _ = await add_file(db, io.BytesIO(b"Rename sample"), "rename.txt")
    old = await topics.create("Original Name")
    identifier = public(old["id"])
    await topics.rename(identifier, "Better Name")
    await topics.publish(source["id"], {}, ["Original Name"])
    attached = await topics.for_source(source["id"])
    assert len(attached) == 1 and attached[0]["name"] == "Better Name"
    other = await topics.create("Another")
    with pytest.raises(ValueError):
        await topics.rename(public(other["id"]), "Original Name")
    assert (await topics.get(public(other["id"])))["name"] == "Another"
