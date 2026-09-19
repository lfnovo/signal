"""Esperanto adapters and file-based AI Prompter templates."""

import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path

from ai_prompter import Prompter
from esperanto.factory import AIFactory

from .config import Settings
from .topic_context import prompt_catalog


def chunk_text(text: str, size=6000, overlap=300):
    if size <= overlap or overlap < 0:
        raise ValueError("Chunk size must exceed overlap")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary + 1
        chunks.append({"index": len(chunks), "text": text[start:end], "start": start, "end": end})
        if end == len(text):
            break
        start = end - overlap
    return chunks


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError("Incompatible embedding dimensions")
    denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / denominator if denominator else 0.0


def embedding_parts(text, max_bytes=1800):
    """Conservative UTF-8 byte bound stays below Gemini 001's 2048-token limit."""
    raw = text.encode("utf-8")
    start = 0
    parts = []
    while start < len(raw):
        part = raw[start : start + max_bytes].decode("utf-8", errors="ignore")
        if not part:
            raise ValueError("Could not split this text for embeddings.")
        parts.append(part)
        start += len(part.encode("utf-8"))
    return parts


class AI:
    def __init__(self, settings: Settings):
        self._settings = ContextVar("signal_ai_settings", default=settings)
        self._llms = {}
        self._embedders = {}

    @property
    def settings(self):
        return self._settings.get()

    @contextmanager
    def use_preferences(self, preferences):
        """Snapshot settings per job/request without racing other async tasks."""
        token = self._settings.set(replace(self.settings, **preferences))
        try:
            yield self
        finally:
            self._settings.reset(token)

    @property
    def llm(self):
        key = (self.settings.llm_provider, self.settings.llm_model)
        if key not in self._llms:
            self._llms[key] = AIFactory.create_language(
                *key,
                config={"timeout": 120, "max_tokens": 2000},
            )
        return self._llms[key]

    def prompt(self, name, **values):
        return Prompter(
            prompt_template=name, prompt_dir=str(Path(__file__).with_name("prompts"))
        ).render(values)

    async def complete(self, messages):
        result = await self.llm.achat_complete(messages)
        text = result.choices[0].message.content
        if not text or not text.strip():
            raise ValueError("The model returned an empty response.")
        return text.strip()

    async def embed(self, texts, provider=None, model=None):
        key = (provider or self.settings.embedding_provider, model or self.settings.embedding_model)
        if key not in self._embedders:
            self._embedders[key] = AIFactory.create_embedding(*key, config={"timeout": 120})
        groups = [embedding_parts(text) for text in texts]
        if any(not group for group in groups):
            raise ValueError("Cannot embed empty text.")
        parts = [part for group in groups for part in group]
        # Esperanto 2.27 returns lists. Bound both individual input and total batch.
        vectors = []
        for start in range(0, len(parts), 32):
            vectors.extend(await self._embedders[key].aembed(parts[start : start + 32]))
        if len(vectors) != len(parts) or any(not v for v in vectors):
            raise ValueError("The provider returned incomplete embeddings.")
        if vectors and any(
            len(v) != len(vectors[0]) or any(not math.isfinite(x) for x in v) for v in vectors
        ):
            raise ValueError("The provider returned invalid embeddings.")
        result = []
        offset = 0
        for group in groups:
            group_vectors = vectors[offset : offset + len(group)]
            offset += len(group)
            if len(group) == 1:
                result.append(group_vectors[0])
                continue
            # Weighted pooling represents every part, including long summaries/questions.
            weights = [len(part.encode("utf-8")) for part in group]
            pooled = [
                sum(v[i] * w for v, w in zip(group_vectors, weights, strict=True)) / sum(weights)
                for i in range(len(group_vectors[0]))
            ]
            norm = math.sqrt(sum(x * x for x in pooled)) or 1
            result.append([x / norm for x in pooled])
        return result

    async def summarize(self, text, language, steering=""):
        pieces = chunk_text(text, size=24000, overlap=0)
        summaries = []
        for piece in pieces:
            summaries.append(
                await self.complete(
                    [
                        {
                            "role": "system",
                            "content": self.prompt(
                                "summary",
                                language=language,
                                partial=len(pieces) > 1,
                                steering=steering,
                            ),
                        },
                        {"role": "user", "content": piece["text"]},
                    ]
                )
            )
        if len(summaries) == 1:
            return summaries[0]
        combined = "\n\n".join(summaries)
        # Recursive reduction bounds every prompt, including book-sized sources.
        return await self.summarize(combined, language, steering=steering)

    async def summary_bundle(self, text, language, official_topics, steering=""):
        # Reduce large sources before the final joint summary/topic generation.
        if len(text) > 24000:
            text = await self.summarize(text, language, steering=steering)
        response = await self.complete(
            [
                {
                    "role": "system",
                    "content": self.prompt(
                        "summary_topics",
                        language=language,
                        official_topics=prompt_catalog(official_topics, text)
                        if official_topics and isinstance(official_topics[0], dict)
                        else official_topics,
                        steering=steering,
                    ),
                },
                {"role": "user", "content": text},
            ]
        )
        if response.startswith("```"):
            response = response.split("\n", 1)[1].rsplit("```", 1)[0]
        value = json.loads(response)
        from .topics import topic_name

        if (
            not isinstance(value, dict)
            or not isinstance(value.get("summary"), str)
            or not value["summary"].strip()
        ):
            raise ValueError("The model returned an invalid summary.")
        topics = value.get("topics")
        if (
            not isinstance(topics, list)
            or not 1 <= len(topics) <= 5
            or any(not isinstance(t, str) for t in topics)
        ):
            raise ValueError("The model returned invalid topics.")
        return {
            "summary": value["summary"].strip(),
            "topics": list(dict.fromkeys(topic_name(t)[0] for t in topics)),
            "personal_relevance": value.get("personal_relevance", "").strip()
            if isinstance(value.get("personal_relevance", ""), str)
            else "",
        }

    async def relevance(self, source, contexts, language):
        if not any(t.get("personal_context") for t in contexts):
            return ""
        text = source.get("content", "")
        if len(text) > 24000:
            text = await self.summarize(text, language)
        return await self.complete(
            [
                {
                    "role": "system",
                    "content": self.prompt(
                        "relevance", language=language, topic_context=prompt_catalog(contexts, text)
                    ),
                },
                {"role": "user", "content": text},
            ]
        )

    async def chat(self, source, history, question, language):
        chunks = source["chunks"]
        if len(source["content"]) > self.settings.context_chars:
            # Use the source's stored model so changing defaults cannot mix vector spaces.
            query = (
                await self.embed(
                    [question], source["embedding_provider"], source["embedding_model"]
                )
            )[0]
            ranked = sorted(chunks, key=lambda c: cosine(c["embedding"], query), reverse=True)
            chunks = sorted(ranked[:8], key=lambda c: c["index"])
        evidence = [{k: c[k] for k in ("index", "text", "start", "end")} for c in chunks]
        messages = [
            {
                "role": "system",
                "content": self.prompt(
                    "chat",
                    language=language,
                    title=source["title"],
                    summary=source["summary"],
                    chunks=evidence,
                    topic_context=prompt_catalog(source.get("topic_context", []), question),
                ),
            }
        ]
        # Persist the full history, but keep each request bounded.
        for turn in history[-10:]:
            messages.extend(
                [
                    {"role": "user", "content": turn["question"][:4000]},
                    {"role": "assistant", "content": turn["answer"][:8000]},
                ]
            )
        messages.append({"role": "user", "content": question})
        return await self.complete(messages), evidence

    async def close(self):
        for model in [*self._llms.values(), *self._embedders.values()]:
            if model is not None:
                client = getattr(model, "async_client", None)
                if client is not None:
                    await client.aclose()
                client = getattr(model, "client", None)
                if client is not None:
                    client.close()
