import httpx
import pytest
from esperanto.common_types.model import Model

from signal_inbox.providers import credential_status, discover_models, provider_choices


def test_provider_catalog_exposes_status_not_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-never-leaves-server")
    assert credential_status("openai", "language") == "Key found"
    catalog = provider_choices()
    assert "secret-never-leaves-server" not in str(catalog)
    assert "anthropic" not in [p["id"] for p in catalog["embedding"]]


async def test_google_discovery_filters_capabilities_and_paginates(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "key=" not in str(request.url)
        calls.append(request.url.params.get("pageToken"))
        if request.url.params.get("pageToken"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/another-vector",
                            "supportedGenerationMethods": ["embedContent"],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/chat-only", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/vector-only", "supportedGenerationMethods": ["embedContent"]},
                ],
                "nextPageToken": "page-two",
            },
        )

    monkeypatch.setattr(
        "signal_inbox.providers.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert await discover_models("google", "embedding") == ["another-vector", "vector-only"]
    assert await discover_models("google", "language") == ["chat-only"]
    assert calls == [None, "page-two", None, "page-two"]


async def test_discovery_filters_other_modalities_and_sanitizes_failures(monkeypatch):
    monkeypatch.setattr(
        "signal_inbox.providers.AIFactory.get_provider_models",
        lambda *a, **k: [
            Model(id="chat", owned_by="test", type="language"),
            Model(id="text-embedding-test", owned_by="test", type="embedding"),
            Model(id="voice", owned_by="test", type="text_to_speech"),
        ],
    )
    assert await discover_models("openai", "embedding") == ["text-embedding-test"]
    assert await discover_models("openai", "language") == ["chat"]

    def fail(*args, **kwargs):
        raise RuntimeError("API key secret-never-leaves-server")

    monkeypatch.setattr("signal_inbox.providers.AIFactory.get_provider_models", fail)
    with pytest.raises(ValueError) as error:
        await discover_models("openai", "language")
    assert "secret-never-leaves-server" not in str(error.value)
    assert "manually" in str(error.value)
