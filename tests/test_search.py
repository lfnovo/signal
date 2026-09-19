import httpx
import pytest
from test_integration import FakeAI

from signal_inbox.database import public
from signal_inbox.intake import add_url
from signal_inbox.search import Search, lexical, terms
from signal_inbox.topics import Topics
from signal_inbox.web import create_app


class SearchAI(FakeAI):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = []
        self.fail_model = None

    async def embed(self, texts, provider=None, model=None):
        self.calls.append((provider, model, texts))
        if model == self.fail_model:
            raise RuntimeError("private credential error")
        return [[0.0, 1.0] if model == "other-model" else [1.0, 0.0] for text in texts]


async def seed(db, slug, title, content, model="model-a", vector=None, **extra):
    row, _ = await add_url(db, "https://example.com/" + slug)
    await db.update(
        row["id"],
        {
            "title": title,
            "content": content,
            "summary": "A summary.",
            "status": "ready",
            "embedding_provider": "google",
            "embedding_model": model,
            "summary_embedding": vector or [1.0, 0.0],
            "chunks": [{"text": content, "index": 0, "embedding": vector or [1.0, 0.0]}],
            **extra,
        },
    )
    return public(row["id"])


def test_keyword_phrases_boundaries_accents_and_field_weights():
    assert terms('"systems thinking" 350') == ["systems thinking", "350"]
    assert lexical({"title": "Systéms Thinking: 350 iPhones"}, terms('"systems thinking" 350')) > 0
    assert (
        lexical(
            {"title": "Systems are great for thinking: 3500 iPhones"},
            terms('"systems thinking" 350'),
        )
        == 0
    )
    assert lexical({"title": "Neuroscience"}, ["neuroscience"]) > lexical(
        {"content": "Neuroscience"}, ["neuroscience"]
    )


@pytest.mark.asyncio
async def test_keyword_search_all_fields_filters_and_no_chat_or_embedding_calls(db):
    ai = SearchAI(db.settings)
    search = Search(db, ai)
    first = await seed(
        db,
        "first",
        "Neuroscience notes",
        "Useful insights about concentration.",
        collection="library",
        focused=True,
    )
    second = await seed(
        db, "second", "Other", "Neuroscience in the full source.", collection="inbox"
    )
    topic = await Topics(db).create("Attention")
    await Topics(db).attach(first, topic["id"])
    await db.save_turn(first, "secret-chat-word", "secret-answer", [], "fake")
    result = await search.run("neuroscience", mode="keywords")
    assert [s["id"] for s in result["sources"]] == [first, second]
    assert result["sources"][1]["match_label"] == "content"
    assert await search.run("secret-chat-word", mode="keywords") == {
        "sources": [],
        "topics": [],
        "total": 0,
        "warnings": [],
        "has_next": False,
    }
    assert (await search.run("attention", mode="keywords"))["sources"][0]["id"] == first
    assert (
        len(
            (await search.run("neuroscience", mode="keywords", collection="library", focused=True))[
                "sources"
            ]
        )
        == 1
    )
    assert (await search.run("neuroscience", mode="keywords", kind="file"))["total"] == 0
    assert (await search.run("neuroscience", mode="keywords", topic=public(topic["id"])))[
        "total"
    ] == 1
    assert ai.calls == []


@pytest.mark.asyncio
async def test_semantics_matches_models_deduplicates_sources_and_finds_related_topics(db):
    ai = SearchAI(db.settings)
    search = Search(db, ai)
    first = await seed(db, "first", "Attention", "A deep paragraph about concentration.")
    second = await seed(
        db, "second", "Another model", "A relevant passage.", model="other-model", vector=[0.0, 1.0]
    )
    topic = await Topics(db).create("Atenção")
    await Topics(db).attach(first, topic["id"])
    result = await search.run("work without distractions", mode="semantic")
    assert {s["id"] for s in result["sources"]} == {first, second}
    assert len(result["sources"]) == 2
    assert result["topics"][0]["name"] == "Atenção"
    assert result["topics"][0]["match_type"] == "Related meaning"
    assert {"model-a", "other-model"} <= {call[1] for call in ai.calls}
    calls = len(ai.calls)
    await search.run("work without distractions", mode="semantic")
    assert len(ai.calls) == calls
    await Topics(db).rename(public(topic["id"]), "Deep work")
    assert (await search.run("work without distractions", mode="semantic"))["topics"][0][
        "name"
    ] == "Deep work"


@pytest.mark.asyncio
async def test_partial_provider_failure_preserves_keyword_results_and_hides_secrets(db):
    ai = SearchAI(db.settings)
    ai.fail_model = "other-model"
    search = Search(db, ai)
    first = await seed(db, "first", "Needle", "One source")
    second = await seed(
        db, "second", "Needle too", "Another source", model="other-model", vector=[0.0, 1.0]
    )
    result = await search.run("Needle")
    assert {s["id"] for s in result["sources"]} == {first, second}
    assert result["warnings"] and "private credential" not in str(result)


@pytest.mark.asyncio
async def test_search_routes_suggestions_pagination_and_html_safety(db):
    ai = SearchAI(db.settings)
    await seed(db, "first", "<script>alert(1)</script> Needle", 'A quoted "exact phrase" inside.')
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        suggestion = await client.get("/api/search/suggest", params={"q": "Need"})
        assert suggestion.status_code == 200 and len(suggestion.json()["sources"]) == 1
        assert ai.calls == []
        page = await client.get("/search", params={"q": '"exact phrase"', "mode": "keywords"})
        assert (
            page.status_code == 200
            and "&lt;script&gt;" in page.text
            and "<script>alert(1)</script>" not in page.text
        )
        assert "aria" in page.text and "data-row-action" in page.text
        assert (
            await client.get("/api/search", params={"q": "Needle", "mode": "keywords", "page": 2})
        ).json()["sources"] == []
        assert (await client.get("/api/search?mode=invalid")).status_code == 422
        assert (await client.get("/api/search?page=0")).status_code == 422
        assert (await client.get("/search")).status_code == 200
