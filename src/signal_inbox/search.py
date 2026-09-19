"""Local hybrid retrieval over the personal catalog; no generated answers."""

import asyncio
import math
import re
import time
import unicodedata
from collections import OrderedDict

from .ai import cosine
from .database import public
from .topics import Topics


def normalize(text):
    return " ".join(
        "".join(
            c
            for c in unicodedata.normalize("NFKD", text.casefold())
            if not unicodedata.combining(c)
        ).split()
    )


def terms(query):
    return [
        normalize(phrase or word)
        for phrase, word in re.findall(r'"([^"]+)"|([^\s"]+)', query)
        if normalize(phrase or word)
    ]


def contains(text, term):
    return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))


def lexical(source, query_terms, partial=False):
    matches = (lambda text, term: term in text) if partial else contains
    fields = [
        (source.get("title", ""), 8),
        (" ".join(t["name"] for t in source.get("topics", [])), 6),
        (source.get("summary", ""), 3),
        (source.get("content", ""), 1),
    ]
    normalized = [(normalize(text), weight) for text, weight in fields]
    if not query_terms or not all(
        any(matches(text, term) for text, _ in normalized) for term in query_terms
    ):
        return 0
    return sum(
        weight
        * sum(1 + math.log1p(text.count(term)) for term in query_terms if matches(text, term))
        for text, weight in normalized
    )


def excerpt(text, query_terms, size=340):
    text = " ".join(text.split())
    normalized = normalize(text)
    positions = [normalized.find(term) for term in query_terms if term in normalized]
    start = max(0, min(positions) - 85) if positions else 0
    return (
        ("…" if start else "")
        + text[start : start + size]
        + ("…" if len(text) > start + size else "")
    )


def fuse(keyword, semantic):
    scores = {}
    for ranking, weight in ((keyword, 1.2), (semantic, 1)):
        for index, identifier in enumerate(ranking):
            scores[identifier] = scores.get(identifier, 0) + weight / (60 + index + 1)
    return sorted(scores, key=lambda identifier: (-scores[identifier], identifier))


class Search:
    def __init__(self, db, ai):
        self.db, self.ai = db, ai
        self.cache = OrderedDict()
        self.slots = asyncio.Semaphore(3)

    async def vectors(self, provider, model, texts):
        now = time.monotonic()
        missing = list(
            dict.fromkeys(
                text
                for text in texts
                if (provider, model, text) not in self.cache
                or self.cache[(provider, model, text)][0] < now
            )
        )
        if missing:
            async with self.slots:
                async with asyncio.timeout(25):
                    vectors = await self.ai.embed(missing, provider=provider, model=model)
            if len(vectors) != len(missing):
                raise ValueError("Incomplete embeddings")
            for text, vector in zip(missing, vectors, strict=True):
                self.cache[(provider, model, text)] = (now + 300, vector)
        result = [self.cache[(provider, model, text)][1] for text in texts]
        while len(self.cache) > 1000:
            self.cache.popitem(last=False)
        return result

    async def run(
        self,
        query,
        mode="hybrid",
        collection="",
        focused=False,
        kind="",
        topic="",
        page=1,
        limit=20,
        partial=False,
    ):
        query = query.strip()
        if not query:
            return {"sources": [], "topics": [], "total": 0, "warnings": [], "has_next": False}
        conditions, values = [], {}
        if collection:
            conditions.append(
                "collection = 'library'" if collection == "library" else "collection != 'library'"
            )
        if focused:
            conditions.append("focused = true")
        if kind:
            conditions.append("kind = $kind")
            values["kind"] = kind
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        columns = (
            "id, title, kind, original, status, created_at, collection, focused, summary, content"
        )
        if mode != "keywords":
            columns += ", chunks, summary_embedding, embedding_provider, embedding_model"
        rows = public(
            await self.db.with_topics(
                await self.db.query(
                    "SELECT " + columns + " FROM source" + where + " ORDER BY created_at DESC",
                    values,
                )
            )
        )
        if topic:
            rows = [row for row in rows if any(t["id"] == topic for t in row["topics"])]
        catalog = public(await Topics(self.db).list())
        scoped = bool(collection or focused or kind or topic)
        if scoped:
            ids = {t["id"] for row in rows for t in row["topics"]}
            catalog = [t for t in catalog if t["id"] in ids]
        query_terms = terms(query)
        keyword_scores = {row["id"]: lexical(row, query_terms, partial) for row in rows}
        keyword_rank = sorted(
            (sid for sid in keyword_scores if keyword_scores[sid]),
            key=lambda sid: -keyword_scores[sid],
        )
        topic_keyword = [
            t["id"]
            for t in catalog
            if query_terms
            and all(
                (term in normalize(t["name"])) if partial else contains(normalize(t["name"]), term)
                for term in query_terms
            )
        ]
        scores, passages, topic_scores, warnings = {}, {}, {}, []
        if mode != "keywords":
            groups = {}
            unindexed = 0
            for row in rows:
                if (
                    row.get("embedding_provider")
                    and row.get("embedding_model")
                    and (row.get("chunks") or row.get("summary_embedding"))
                ):
                    groups.setdefault(
                        (row["embedding_provider"], row["embedding_model"]), []
                    ).append(row)
                else:
                    unindexed += 1
            if unindexed:
                warnings.append(
                    f"{unindexed} finds have no semantic index yet; keyword search can still find them."
                )

            async def group_search(key, group):
                try:
                    vector = (await self.vectors(*key, [query]))[0]
                    for row in group:
                        candidates = [
                            (row.get("summary", ""), row.get("summary_embedding", []), "summary")
                        ]
                        candidates += [
                            (chunk["text"], chunk.get("embedding", []), "content")
                            for chunk in row.get("chunks", [])
                        ]
                        valid = [
                            (cosine(vector, embedding), text, label)
                            for text, embedding, label in candidates
                            if text and embedding and len(embedding) == len(vector)
                        ]
                        if not valid:
                            warnings.append(
                                "Some stored embeddings are incompatible with their recorded model."
                            )
                            continue
                        score, text, label = max(valid, key=lambda item: item[0])
                        if score >= 0.25:
                            scores[row["id"]] = score
                            passages[row["id"]] = (text, label)
                except Exception:
                    warnings.append(
                        f"Semantic search unavailable for {key[0]} / {key[1]}. Results may be incomplete."
                    )

            async def topic_search():
                if not catalog:
                    return
                selected = await self.db.preferences()
                key = (selected["embedding_provider"], selected["embedding_model"])
                try:
                    # Names share the same model as this query. Rename naturally changes the cache key.
                    vector = (await self.vectors(*key, [query]))[0]
                    for start in range(0, len(catalog), 32):
                        batch = catalog[start : start + 32]
                        vectors = await self.vectors(
                            *key,
                            [
                                "\n".join(
                                    str(t.get(k) or "")
                                    for k in ("name", "definition", "personal_context")
                                    if t.get(k)
                                )
                                for t in batch
                            ],
                        )
                        for t, embedding in zip(batch, vectors, strict=True):
                            score = cosine(vector, embedding)
                            if score >= 0.35:
                                topic_scores[t["id"]] = score
                except Exception:
                    warnings.append(
                        "Topic-name semantic matching is unavailable. Related topics may still come from matching finds."
                    )

            await asyncio.gather(
                *(group_search(key, group) for key, group in groups.items()), topic_search()
            )
            for row in rows:
                if scores.get(row["id"], 0) >= 0.35:
                    for t in row["topics"]:
                        topic_scores[t["id"]] = max(
                            topic_scores.get(t["id"], 0), scores[row["id"]] * 0.9
                        )
        semantic_rank = sorted(scores, key=lambda sid: -scores[sid])
        topic_semantic = sorted(topic_scores, key=lambda tid: -topic_scores[tid])[:30]
        ranked = (
            keyword_rank
            if mode == "keywords"
            else semantic_rank
            if mode == "semantic"
            else fuse(keyword_rank, semantic_rank)
        )
        ranked_topics = (
            topic_keyword
            if mode == "keywords"
            else topic_semantic
            if mode == "semantic"
            else fuse(topic_keyword, topic_semantic)
        )
        by_id = {row["id"]: row for row in rows}
        results = []
        for sid in ranked[(page - 1) * limit : page * limit]:
            row = by_id[sid]
            is_keyword = bool(keyword_scores[sid]) and mode != "semantic"
            text, label = passages.get(
                sid,
                (
                    row.get("summary") or row.get("content") or row["title"],
                    "summary" if row.get("summary") else "content",
                ),
            )
            if is_keyword:
                # Prefer a matching passage, including literal hits deep in the source.
                for field in ("content", "summary", "title"):
                    if any(contains(normalize(row.get(field, "")), term) for term in query_terms):
                        text, label = row[field], field
                        break
                else:
                    text, label = ", ".join(t["name"] for t in row["topics"]), "topics"
            result = {
                key: row[key]
                for key in (
                    "id",
                    "title",
                    "kind",
                    "original",
                    "status",
                    "created_at",
                    "collection",
                    "topics",
                )
            }
            result.update(
                focused=row.get("focused", False),
                summary=excerpt(text, query_terms if is_keyword else []),
                match_label=label,
                match_type="Keywords + meaning"
                if is_keyword and sid in scores
                else "Keywords"
                if is_keyword
                else "Meaning",
            )
            results.append(result)
        topics_by_id = {t["id"]: t for t in catalog}
        topic_results = [
            {key: topics_by_id[tid][key] for key in ("id", "name", "official")}
            | {
                "match_type": "Name match"
                if tid in topic_keyword and mode != "semantic"
                else "Related meaning"
            }
            for tid in ranked_topics[:12]
            if tid in topics_by_id
        ]
        return {
            "sources": results,
            "topics": topic_results,
            "total": len(ranked),
            "warnings": list(dict.fromkeys(warnings)),
            "has_next": page * limit < len(ranked),
        }
