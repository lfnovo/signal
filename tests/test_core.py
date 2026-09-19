import io
from dataclasses import replace

import pytest

from signal_inbox.ai import AI, chunk_text, cosine
from signal_inbox.intake import add_file, add_url, normalize_url
from signal_inbox.worker import worker_lock


def test_url_canonicalization():
    assert normalize_url("HTTPS://Example.COM:443/a?q=1#section") == "https://example.com/a?q=1"
    assert normalize_url("http://EXAMPLE.com:80") == "http://example.com/"
    assert normalize_url("https://example.com/?b=2&a=1") != normalize_url(
        "https://example.com/?a=1&b=2"
    )
    for value in [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@example.com",
        "https://",
    ]:
        with pytest.raises(ValueError):
            normalize_url(value)


def test_chunking_covers_entire_unicode_document():
    text = ("Olá mundo! 你好 🌍\n" * 3000) + "FINAL IMPORTANTE"
    chunks = chunk_text(text, size=1000, overlap=100)
    covered = set()
    for chunk in chunks:
        assert chunk["text"] == text[chunk["start"] : chunk["end"]]
        assert len(chunk["text"]) <= 1000
        covered.update(range(chunk["start"], chunk["end"]))
    assert covered == set(range(len(text)))
    assert chunks[-1]["text"].endswith("FINAL IMPORTANTE")
    assert cosine([1, 0], [1, 0]) == 1
    with pytest.raises(ValueError):
        cosine([1], [1, 2])


def test_worker_lock_prevents_two_consumers_and_releases(settings):
    with worker_lock(settings) as first:
        assert first
        with worker_lock(settings) as second:
            assert not second
    with worker_lock(settings) as third:
        assert third


class RecordingAI(AI):
    def __init__(self, settings):
        super().__init__(settings)
        self.requests = []
        self.embedding_request = None

    async def complete(self, messages):
        self.requests.append(messages)
        return "Resumo curto da parte."

    async def embed(self, texts, provider=None, model=None):
        self.embedding_request = (texts, provider, model)
        return [[1.0, 0.0] for _ in texts]


async def test_large_summary_reads_every_part(settings):
    ai = RecordingAI(settings)
    text = "a" * 50000 + "CONCLUSAO"
    await ai.summarize(text, "Português")
    first_pass = [r[1]["content"] for r in ai.requests[:3]]
    assert "".join(first_pass) == text
    assert all(len(r[1]["content"]) <= 24000 for r in ai.requests)
    assert all("Português" in r[0]["content"] for r in ai.requests)


async def test_retrieval_uses_stored_embedding_model_and_relevant_evidence(settings):
    ai = RecordingAI(replace(settings, context_chars=10))
    chunks = [
        {
            "index": i,
            "text": f"Trecho {i}",
            "start": i * 10,
            "end": i * 10 + 8,
            "embedding": [1.0, 0.0] if i == 15 else [0.0, 1.0],
        }
        for i in range(20)
    ]
    source = {
        "title": "Fonte",
        "summary": "Resumo",
        "content": "x" * 200,
        "chunks": chunks,
        "embedding_provider": "original-provider",
        "embedding_model": "original-model",
    }
    _, evidence = await ai.chat(source, [], "Minha pergunta", "Português")
    assert len(evidence) == 8
    assert 15 in [c["index"] for c in evidence]
    assert ai.embedding_request[1:] == ("original-provider", "original-model")
    assert "embedding" not in evidence[0]
    assert "[15]" not in ai.requests[-1][0]["content"]  # labels are 1-based XML numbers
    assert 'numero="16"' in ai.requests[-1][0]["content"]


async def test_file_limits_leave_no_temporary_files(settings):
    class StubDB:
        def __init__(self):
            self.settings = replace(settings, max_upload_bytes=5)

    db = StubDB()
    for content in [b"", b"123456"]:
        with pytest.raises(ValueError):
            await add_file(db, io.BytesIO(content), "../../test.txt")
    assert list((settings.data_dir / "uploads").iterdir()) == []


async def test_url_intake_rejects_invalid_before_database():
    with pytest.raises(ValueError):
        await add_url(None, "javascript:alert(1)")


async def test_embedding_preserves_all_bytes_and_bounds_provider_inputs(settings):
    from signal_inbox.ai import embedding_parts

    text = ("你好🌍 Olá! " * 1000) + "FINAL"
    parts = embedding_parts(text)
    assert "".join(parts) == text
    assert all(len(part.encode("utf-8")) <= 1800 for part in parts)

    class Embedder:
        def __init__(self):
            self.seen = []

        async def aembed(self, texts):
            assert len(texts) <= 32
            self.seen.extend(texts)
            return [[1.0, 0.0] for _ in texts]

    ai = AI(settings)
    provider = Embedder()
    ai._embedders[(settings.embedding_provider, settings.embedding_model)] = provider
    vectors = await ai.embed([text, "short"])
    assert vectors == [[1.0, 0.0], [1.0, 0.0]]
    assert "".join(provider.seen) == text + "short"


async def test_model_preferences_are_isolated_between_concurrent_requests(settings, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    created = []

    def factory(provider, model, config):
        created.append((provider, model))
        return SimpleNamespace(provider=provider, model=model)

    monkeypatch.setattr("signal_inbox.ai.AIFactory.create_language", factory)
    ai = AI(settings)
    ready = asyncio.Event()
    release = asyncio.Event()

    async def first():
        with ai.use_preferences({"llm_provider": "openai", "llm_model": "first-model"}):
            initial = ai.llm
            ready.set()
            await release.wait()
            assert ai.llm is initial
            assert ai.settings.llm_model == "first-model"

    async def second():
        await ready.wait()
        with ai.use_preferences({"llm_provider": "google", "llm_model": "second-model"}):
            assert ai.llm.model == "second-model"
            release.set()
            await asyncio.sleep(0)
            assert ai.settings.llm_provider == "google"

    await asyncio.gather(first(), second())
    assert ai.settings == settings
    assert created == [("openai", "first-model"), ("google", "second-model")]
    with ai.use_preferences({"llm_provider": "openai", "llm_model": "first-model"}):
        assert ai.llm.model == "first-model"
    assert len(created) == 2  # Reuse only the matching cached client.


async def test_joint_summary_topics_and_steering(settings, monkeypatch):
    ai = AI(settings)
    calls = []

    async def complete(messages):
        calls.append(messages)
        return '```json\n{"summary":"## Practical takeaways\\nA useful summary.","topics":["Knowledge", "Reading", "Libraries"]}\n```'

    monkeypatch.setattr(ai, "complete", complete)
    result = await ai.summary_bundle(
        "Source text with facts.", "Português", ["Knowledge"], "Focus on practice"
    )
    assert result["topics"] == ["Knowledge", "Reading", "Libraries"]
    assert "Focus on practice" in calls[0][0]["content"]
    assert '["Knowledge"]' in calls[0][0]["content"]
    assert "Português" in calls[0][0]["content"]
    assert calls[0][1]["content"] == "Source text with facts."


@pytest.mark.parametrize(
    "response",
    [
        '{"summary":"ok","topics":[]}',
        '{"summary":"","topics":["One"]}',
        '{"summary":"ok","topics":[1]}',
        "not json",
    ],
)
async def test_invalid_summary_topics_are_rejected(settings, monkeypatch, response):
    ai = AI(settings)

    async def complete(messages):
        return response

    monkeypatch.setattr(ai, "complete", complete)
    with pytest.raises(ValueError):
        await ai.summary_bundle("Facts", "Português", [])
