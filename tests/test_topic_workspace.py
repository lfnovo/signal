import json

import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.ai import AI
from signal_inbox.database import public
from signal_inbox.intake import add_url
from signal_inbox.topic_context import context_signature
from signal_inbox.topics import Topics
from signal_inbox.web import create_app
from signal_inbox.worker import process_source

pytestmark = pytest.mark.asyncio


async def seed(db, name, collection="library", focused=False):
    source, _ = await add_url(db, "https://example.com/" + name)
    await db.update(
        source["id"],
        {
            "title": name,
            "collection": collection,
            "focused": focused,
            "content": "Evidence about human judgment and agents.",
        },
    )
    return source["id"]


async def test_graph_scope_normalization_and_exact_evidence(db):
    topics = Topics(db)
    a, b, c = [public((await topics.create(name))["id"]) for name in ["A", "B", "C"]]
    s1, s2, s3 = (
        await seed(db, "one", focused=True),
        await seed(db, "two"),
        await seed(db, "three", "inbox", True),
    )
    for sid, tids in [(s1, [a, b]), (s2, [a]), (s3, [b, c])]:
        for tid in tids:
            await topics.attach(sid, tid)
    graph = await topics.workspace()
    assert graph["scope"] == "library" and len(graph["sources"]) == 2
    edge = graph["edges"][0]
    assert edge["shared_count"] == 1 and edge["strength"] == pytest.approx(1 / 2**0.5)
    assert edge["source_ids"] == [public(s1)]
    assert {t["name"]: t["count"] for t in graph["topics"]} == {"A": 2, "B": 1, "C": 0}
    assert len((await topics.workspace(True))["edges"]) == 2
    await topics.attach(s1, b, remove=True)
    assert (await topics.workspace())["edges"] == []
    await topics.delete(c)
    assert all(t["id"] != c for t in (await topics.workspace(True))["topics"])


async def test_context_routes_merge_and_agent_read_payload(db):
    topics = Topics(db)
    first, second = await topics.create("Judgment"), await topics.create("Agents")
    a, b = public(first["id"]), public(second["id"])
    sid = await seed(db, "context")
    await topics.attach(sid, a)
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        r = await client.put(
            f"/api/topics/{a}/context",
            json={"definition": "Human decisions", "personal_context": "Studying delegation"},
        )
        assert r.status_code == 200 and r.json()["personal_context"] == "Studying delegation"
        r = await client.get(f"/api/topics/{a}")
        assert r.json()["topic"]["definition"] == "Human decisions"
        assert r.json()["sources"][0]["id"] == public(sid)
        assert (await client.get("/api/topics/workspace")).json()["scope"] == "library"
        assert (
            await client.put(f"/api/topics/{a}/context", json={"definition": "x" * 12001})
        ).status_code == 422
        await topics.save_context(b, "Software agents", "Building a team")
        assert (await client.post(f"/api/topics/{a}/merge", json={"target": b})).status_code == 200
        merged = await topics.get(b)
        assert (
            "Human decisions" in merged["definition"] and "Software agents" in merged["definition"]
        )
        assert (
            "Studying delegation" in merged["personal_context"]
            and "Building a team" in merged["personal_context"]
        )
        assert (await client.get(f"/api/topics/{a}")).status_code == 404


async def test_context_reaches_tagging_relevance_and_chat_without_changing_extraction(
    db, monkeypatch
):
    topics = Topics(db)
    official = await topics.create("Judgment")
    await topics.save_context(public(official["id"]), "Human decisions", "Researching delegation")
    sid = await seed(db, "ai-context")
    await topics.publish(sid, {}, ["Unapproved"])
    suggested = next(t for t in await topics.list() if not t["official"])
    await topics.save_context(public(suggested["id"]), "Exclude this from prompts", "Not approved")
    ai = FakeAI(db.settings)
    messages = []

    async def complete(prompt):
        messages.append(prompt)
        return json.dumps(
            {
                "summary": "Faithful source summary",
                "topics": ["Judgment"],
                "personal_relevance": "May inform your delegation research.",
            }
        )

    monkeypatch.setattr(ai, "complete", complete)
    monkeypatch.setattr(ai, "summary_bundle", AI.summary_bundle.__get__(ai))
    await process_source(db, ai, await db.get(sid))
    source = await db.get(sid)
    assert source["status"] == "ready"
    assert source["content"] == "Evidence about human judgment and agents."
    assert source["personal_relevance"] == "May inform your delegation research."
    assert source["summary"] == "Faithful source summary"
    assert "Human decisions" in messages[0][0]["content"]
    assert "Researching delegation" in messages[0][0]["content"]
    assert "Not approved" not in messages[0][0]["content"]
    source["topic_context"] = await topics.contexts(sid)
    await AI.chat(ai, source, [], "How is this relevant?", "Português")
    assert "Researching delegation" in messages[-1][0]["content"]
    assert "não evidência" in messages[-1][0]["content"]
    old_signature = source["relevance_signature"]
    await topics.save_context(public(official["id"]), "Human decisions", "New research direction")
    assert old_signature != context_signature(await topics.contexts())

    async def relevance_complete(prompt):
        messages.append(prompt)
        return "Fresh personal interpretation."

    monkeypatch.setattr(ai, "complete", relevance_complete)
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        path = public(sid)
        before = (await client.get(f"/sources/{path}/triage")).text
        assert "Your topic context has changed" in before
        response = await client.post(f"/api/sources/{path}/relevance")
        assert response.status_code == 200 and "Fresh personal" in response.json()["html"]
        after = await db.get(sid)
        assert after["summary"] == source["summary"] and after["content"] == source["content"]
        assert after["relevance_signature"] == context_signature(await topics.contexts())


async def test_relevance_does_not_overwrite_a_changed_source(db, monkeypatch):
    sid = await seed(db, "changing")
    await db.update(sid, {"status": "ready", "summary": "Before"})
    ai = FakeAI(db.settings)

    async def changed(source, contexts, language):
        await db.update(sid, {"summary": "After"})
        return "Stale interpretation"

    monkeypatch.setattr(ai, "relevance", changed)
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        response = await client.post(f"/api/sources/{public(sid)}/relevance")
        assert response.status_code == 409
        assert not (await db.get(sid)).get("personal_relevance")
