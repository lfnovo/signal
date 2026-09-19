import asyncio
import io

import httpx
import pytest

from signal_inbox.ai import AI
from signal_inbox.config import PREFERENCE_KEYS
from signal_inbox.database import public
from signal_inbox.intake import add_file, add_url
from signal_inbox.web import create_app
from signal_inbox.worker import process_source, run_worker

pytestmark = pytest.mark.asyncio


class FakeAI(AI):
    def __init__(self, settings):
        super().__init__(settings)
        self.summary_calls = 0
        self.fail_embedding = False

    async def summarize(self, text, language):
        self.summary_calls += 1
        return f"Resumo em {language}. Biblioteca com 400 livros."

    async def summary_bundle(self, text, language, official_topics, steering=""):
        return {
            "summary": await self.summarize(text, language),
            "topics": ["Leitura", "Bibliotecas", "Livros"],
        }

    async def embed(self, texts, provider=None, model=None):
        if self.fail_embedding:
            raise RuntimeError("secret provider response")
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def chat(self, source, history, question, language):
        return f"400 livros [1]. Histórico: {len(history)}.", [
            {"index": 0, "text": "400 livros", "start": 0, "end": 10}
        ]


async def test_duplicate_capture_is_atomic_and_preserves_source(db):
    captures = await asyncio.gather(
        *[add_url(db, "https://example.com/#fragment") for _ in range(6)]
    )
    assert sum(created for _, created in captures) == 1
    assert len(await db.sources()) == 1
    original = captures[0][0]
    await db.update(original["id"], {"summary": "Preservado", "status": "ready"})
    duplicate, created = await add_url(db, "https://EXAMPLE.COM:443")
    assert not created
    assert duplicate["summary"] == "Preservado"
    assert duplicate["created_at"] == original["created_at"]


async def test_file_hash_dedup_and_copy_survives_original(db):
    row, created = await add_file(db, io.BytesIO(b"# Teste\n400 livros."), "../first.md")
    duplicate, second = await add_file(db, io.BytesIO(b"# Teste\n400 livros."), "renamed.txt")
    assert created and not second
    assert row["id"] == duplicate["id"]
    assert row["original"] == "first.md"
    ai = FakeAI(db.settings)
    await process_source(db, ai, row)
    saved = await db.get(row["id"])
    assert saved["status"] == "ready"
    assert saved["title"] == "Teste"
    assert saved["content"].startswith("# Teste")
    assert len(saved["summary_embedding"]) == 3
    assert saved["chunks"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert saved["summary_language"] == "Português"


async def test_failure_retry_checkpoints_and_crash_recovery(db):
    source, _ = await add_file(db, io.BytesIO(b"# Documento\nUma leitura."), "document.md")
    ai = FakeAI(db.settings)
    ai.fail_embedding = True
    await process_source(db, ai, source)
    saved = await db.get(source["id"])
    assert saved["status"] == "error" and saved["stage"] == "embedding"
    assert saved["content"] and saved["summary"]
    assert "secret provider response" not in saved["error"]
    ai.fail_embedding = False
    await db.update(source["id"], {"status": "processing"})  # Simulate a killed worker.
    await run_worker(db, ai, once=True)
    saved = await db.get(source["id"])
    assert saved["status"] == "ready"
    assert saved["attempts"] == 2
    assert ai.summary_calls == 1


async def test_web_upload_chat_history_clear_preferences_and_html(db):
    ai = FakeAI(db.settings)
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        response = await client.post(
            "/api/sources/upload",
            files={"file": ("test.md", b"# Teste\n400 livros.\n<script>alert(1)</script>")},
        )
        assert response.status_code == 202
        sid = response.json()["source"]["id"]
        assert (
            await client.post(f"/api/sources/{sid}/chat", json={"question": "Livros?"})
        ).status_code == 409
        await process_source(db, ai, await db.get(sid))
        page = await client.get(f"/sources/{sid}")
        assert page.status_code == 200
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text
        assert "script-src 'self'" in page.headers["content-security-policy"]
        question = {"question": "Quantos livros?"}
        first = await client.post(f"/api/sources/{sid}/chat", json=question)
        second = await client.post(f"/api/sources/{sid}/chat", json=question)
        assert first.status_code == second.status_code == 200
        assert "Histórico: 1" in second.json()["answer"]
        assert len((await client.get(f"/api/sources/{sid}/chat")).json()) == 2
        assert (await client.delete(f"/api/sources/{sid}/chat")).status_code == 200
        assert (await client.get(f"/api/sources/{sid}/chat")).json() == []
        assert (
            await client.put("/api/preferences", json={"language": "English"})
        ).status_code == 200
        assert (await db.preferences())["language"] == "English"
        assert (await db.get(sid))["summary_language"] == "Português"
        assert (await client.get("/")).status_code == 200
        assert (
            await client.post("/api/sources", json={"url": "file:///etc/passwd"})
        ).status_code == 400
        assert (
            await client.post(
                "/api/sources",
                json={"url": "https://example.com"},
                headers={"Origin": "https://evil.example"},
            )
        ).status_code == 403
        assert (await client.get("/sources/invalid")).status_code == 404
        assert (await client.post(f"/api/sources/{sid}/retry")).status_code == 409
        assert "chunks" not in (await client.get(f"/api/sources/{sid}")).json()


async def test_extension_cors_and_shared_inbox(db):
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    origin = "chrome-extension://" + "a" * 32
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8020"
    ) as client:
        preflight = await client.options(
            "/api/sources",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        first = await client.post(
            "/api/sources", json={"url": "https://example.com"}, headers={"Origin": origin}
        )
        assert first.status_code == 202 and first.json()["created"]
        duplicate, created = await add_url(db, "https://example.com")
        assert not created and public(duplicate["id"]) == first.json()["source"]["id"]


async def test_model_preferences_persist_apply_to_worker_chat_and_keep_old_sources(db):
    class TrackedAI(FakeAI):
        async def summarize(self, text, language):
            self.summary_settings = self.settings
            return await super().summarize(text, language)

        async def embed(self, texts, provider=None, model=None):
            self.embedding_settings = self.settings
            return await super().embed(texts, provider, model)

        async def chat(self, source, history, question, language):
            self.chat_settings = self.settings
            assert language == "Português"
            return await super().chat(source, history, question, language)

    from signal_inbox.database import Database

    ai = TrackedAI(db.settings)
    original, _ = await add_file(db, io.BytesIO(b"# Original\n400 books."), "old.md")
    await process_source(db, ai, original)
    old = await db.get(original["id"])
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        selected = {
            "language": "Português",
            "llm_provider": "openai",
            "llm_model": "custom-chat-v2",
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-small",
        }
        response = await client.put("/api/preferences", json=selected)
        assert response.status_code == 200 and response.json() == (await db.preferences())
        saved_preferences = await Database(db.settings).preferences()
        assert all(saved_preferences[key] == value for key, value in selected.items())
        fresh, _ = await add_file(db, io.BytesIO(b"# New\n500 books."), "new.md")
        await process_source(db, ai, fresh)
        saved = await db.get(fresh["id"])
        assert saved["summary_model"] == ai.summary_settings.llm_model == "custom-chat-v2"
        assert saved["embedding_provider"] == ai.embedding_settings.embedding_provider == "openai"
        assert saved["summary_language"] == "Português"
        sid = public(original["id"])
        chat = await client.post(f"/api/sources/{sid}/chat", json={"question": "Quantos livros?"})
        assert chat.status_code == 200
        assert ai.chat_settings.llm_model == "custom-chat-v2"
        assert chat.json()["model"] == "custom-chat-v2" and chat.json()["provider"] == "openai"
        preserved = await db.get(original["id"])
        for key in ("summary", "summary_model", "embedding_model", "chunks"):
            assert preserved[key] == old[key]
        await client.put("/api/preferences", json={"language": "English"})
        assert (await db.preferences())["llm_model"] == "custom-chat-v2"
        page = await client.get("/")
        assert '<html lang="en">' in page.text
        assert "DUMP YOUR FINDINGS" in page.text
        assert 'value="custom-chat-v2"' in page.text
        assert "Save preferences" in page.text
        detail = await client.get("/sources/" + public(fresh["id"]))
        assert "THE SHORT VERSION" in detail.text and "Resumo em Português" in detail.text


async def test_worker_uses_one_snapshot_when_preferences_change_mid_job(db):
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedAI(FakeAI):
        async def summarize(self, text, language):
            entered.set()
            await release.wait()
            return await super().summarize(text, language)

    ai = PausedAI(db.settings)
    source, _ = await add_file(
        db, io.BytesIO(b"# Snapshot\nKeep models consistent."), "snapshot.md"
    )
    task = asyncio.create_task(process_source(db, ai, source))
    await asyncio.wait_for(entered.wait(), 30)
    await db.set_preferences(
        {
            "llm_provider": "openai",
            "llm_model": "next-chat",
            "embedding_provider": "openai",
            "embedding_model": "next-embedding",
        }
    )
    release.set()
    await task
    saved = await db.get(source["id"])
    assert saved["summary_provider"] == saved["embedding_provider"] == "google"
    assert saved["summary_model"] == db.settings.llm_model
    assert saved["embedding_model"] == db.settings.embedding_model
    assert (await db.preferences())["llm_model"] == "next-chat"


async def test_legacy_language_setting_merges_defaults_and_rejects_invalid_config(db):
    from signal_inbox.database import ensure_record_id

    async with db.connection() as connection:
        await connection.upsert(
            ensure_record_id("preferences", "setting"), {"language": "Português"}
        )
    assert (await db.preferences())["llm_model"] == db.settings.llm_model
    app = create_app(db.settings, database=db, ai=FakeAI(db.settings), start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        for values in (
            {"llm_model": "  "},
            {"language": None},
            {"api_key": "never-store-me"},
            {"embedding_provider": "anthropic"},
            {"llm_provider": "openai"},
        ):
            assert (await client.put("/api/preferences", json=values)).status_code in (400, 422)
        saved = (await client.get("/api/preferences")).json()
        assert set(saved) == set(PREFERENCE_KEYS)
        assert saved["llm_provider"] == "google"
        assert "never-store-me" not in (await client.get("/")).text
        assert (await client.get("/api/models?provider=invalid&kind=embedding")).status_code == 400


async def test_reprocess_preserves_published_data_on_failure_and_versions_chat(db, monkeypatch):
    ai = FakeAI(db.settings)
    source, _ = await add_file(db, io.BytesIO(b"# Before\nOriginal text."), "before.md")
    await process_source(db, ai, source)
    original = await db.get(source["id"])
    sid = public(source["id"])
    seen = []

    async def extract(source, options):
        seen.append(options)
        return {
            "content": "# After\nImproved extraction.",
            "title": "After",
            "metadata": {},
            "identified_type": "text/markdown",
            "extraction_settings": options,
        }

    monkeypatch.setattr("signal_inbox.worker.run_extraction", extract)
    app = create_app(db.settings, database=db, ai=ai, start_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8020"
    ) as client:
        assert (
            await client.post(f"/api/sources/{sid}/chat", json={"question": "Before?"})
        ).status_code == 200
        endpoint = f"/api/sources/{sid}/reprocess"
        assert (await client.post(endpoint, json={"document_engine": "invalid"})).status_code == 400
        assert (await client.post(endpoint, json={"stt_provider": "anthropic"})).status_code == 400
        options = {"document_engine": "docling", "stt_provider": "openai", "stt_model": "whisper-1"}
        assert (await client.post(endpoint, json=options)).status_code == 202
        assert (await client.post(endpoint, json=options)).status_code == 409
        ai.fail_embedding = True
        await process_source(db, ai, await db.get(sid))
        failed = await db.get(sid)
        assert failed["status"] == "error"
        for key in ("content", "summary", "chunks", "summary_embedding", "created_at"):
            assert failed[key] == original[key]
        assert failed["processing_draft"]["content"].startswith("# After")
        assert "processing_draft" not in (await client.get(f"/api/sources/{sid}")).json()
        ai.fail_embedding = False
        assert (await client.post(f"/api/sources/{sid}/retry")).status_code == 200
        await process_source(db, ai, await db.get(sid))
        saved = await db.get(sid)
        assert saved["status"] == "ready" and saved["revision"] == 1
        assert saved["content"].startswith("# After")
        assert saved["created_at"] == original["created_at"]
        assert len(seen) == 1 and seen[0]["document_engine"] == "docling"
        assert seen[0]["stt_model"] == "whisper-1"
        assert ai.summary_calls == 2
        assert (await db.preferences())["document_engine"] == "simple"
        assert len(await db.history(sid)) == 1
        assert await db.history(sid, revision=1) == []
        reply = await client.post(f"/api/sources/{sid}/chat", json={"question": "After?"})
        assert reply.status_code == 200 and "Histórico: 0" in reply.json()["answer"]
        assert (await client.get(f"/sources/{sid}")).status_code == 200
        await client.delete(f"/api/sources/{sid}/chat")
        assert await db.history(sid) == []
