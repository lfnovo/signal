"""Provider choices from the installed Esperanto registry. No secrets leave this module."""

import asyncio
import os
import re

import httpx
from esperanto.factory import AIFactory
from esperanto.providers.llm.profiles import get_profile

LABELS = {
    "google": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "azure": "Azure OpenAI",
    "xai": "xAI",
    "groq": "Groq",
    "ollama": "Ollama",
    "voyage": "Voyage AI",
    "jina": "Jina AI",
    "openai-compatible": "OpenAI-compatible",
    "vertex": "Google Vertex AI",
}


def credential_status(provider, kind):
    if provider in ("ollama", "transformers"):
        return "Local setup"
    if provider == "vertex":
        return "External credentials"
    keys = {
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "azure": [
            "AZURE_OPENAI_API_KEY_"
            + {"language": "LLM", "embedding": "EMBEDDING", "speech_to_text": "STT"}[kind],
            "AZURE_OPENAI_API_KEY",
        ],
        "openai-compatible": ["OPENAI_COMPATIBLE_API_KEY"],
    }.get(provider, [provider.upper().replace("-", "_") + "_API_KEY"])
    profile = get_profile(provider)
    if profile:
        keys = [profile.api_key_env]
    return "Key found" if any(os.getenv(key) for key in keys) else "Set key in .env"


def provider_choices():
    available = AIFactory.get_available_providers()
    return {
        kind: [
            {
                "id": name,
                "label": LABELS.get(name, name.title()),
                "credentials": credential_status(name, kind),
            }
            for name in sorted(available[kind])
        ]
        for kind in ("language", "embedding", "speech_to_text")
    }


def validate_provider(provider, kind):
    if (
        kind not in ("language", "embedding", "speech_to_text")
        or provider not in AIFactory.get_available_providers()[kind]
    ):
        raise ValueError("Choose a supported provider for this model type.")


async def discover_models(provider, kind):
    validate_provider(provider, kind)
    # Discovery is opt-in, not a request to every provider on every page load.
    try:
        async with asyncio.timeout(20):
            if provider == "google":
                # Esperanto 2.27's generic discovery excludes all Gemini embeddings.
                # Use Google's capability metadata rather than suggest language models here.
                key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
                if not key:
                    raise ValueError("Missing Gemini key")
                host = os.getenv(
                    "GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com"
                ).rstrip("/")
                method = "embedContent" if kind == "embedding" else "generateContent"
                found, token = set(), None
                async with httpx.AsyncClient(timeout=15) as client:
                    while True:
                        params = {"pageSize": 100}
                        if token:
                            params["pageToken"] = token
                        response = await client.get(
                            host + "/v1beta/models", params=params, headers={"x-goog-api-key": key}
                        )
                        response.raise_for_status()
                        data = response.json()
                        found.update(
                            model["name"].removeprefix("models/")
                            for model in data.get("models", [])
                            if method in model.get("supportedGenerationMethods", [])
                        )
                        token = data.get("nextPageToken")
                        if not token:
                            return sorted(found)
            models = await asyncio.to_thread(
                AIFactory.get_provider_models, provider, model_type=kind
            )
    except Exception:
        raise ValueError(
            "Could not fetch models. Check this provider’s setup in .env, or enter a model ID manually."
        ) from None

    def matches(model):
        if model.type:
            return model.type == kind
        # Unknown modalities are suggestions only; manual deployment IDs remain supported.
        embedding_name = re.search(r"embed|bge|e5-|voyage", model.id, re.IGNORECASE)
        if kind == "speech_to_text":
            return bool(
                re.search(
                    r"whisper|transcri|scribe|nova|flux|voxtral|gemini", model.id, re.IGNORECASE
                )
            )
        return bool(embedding_name) if kind == "embedding" else not embedding_name

    return sorted({model.id.removeprefix("models/") for model in models if matches(model)})
