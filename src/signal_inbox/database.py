"""Short-lived async connections and parameterized SurrealDB 3 queries."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from surrealdb import AsyncSurreal, RecordID

from .config import PREFERENCE_KEYS, Settings


def now():
    return datetime.now(UTC)


def ensure_record_id(value: str | RecordID, table: str = "source") -> RecordID:
    if isinstance(value, RecordID):
        return value
    if ":" in value:
        name, identifier = value.split(":", 1)
        if name != table:
            raise ValueError("Invalid record table")
        return RecordID(name, identifier)
    return RecordID(table, value)


def public(value):
    if isinstance(value, RecordID):
        return str(value.id)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def one(value):
    """SDK 2.0 returns lists for some record operations against SurrealDB 3."""
    return (value[0] if value else None) if isinstance(value, list) else value


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    @asynccontextmanager
    async def connection(self):
        async with AsyncSurreal(self.settings.db_url) as db:
            await db.signin(
                {"username": self.settings.db_user, "password": self.settings.db_password}
            )
            await db.use(self.settings.namespace, self.settings.database)
            yield db

    async def query(self, sql, variables=None):
        async with self.connection() as db:
            return await db.query(sql, variables or {})

    async def initialize(self):
        # SDK query() checks only the first statement; validate every migration.
        schema = Path(__file__).with_name("schemas.surrealql").read_text()
        for statement in schema.split(";"):
            if statement.strip():
                await self.query(statement)

    async def get(self, identifier):
        async with self.connection() as db:
            return one(await db.select(ensure_record_id(identifier)))

    async def create_source(self, identifier, data):
        async with self.connection() as db:
            return one(await db.create(ensure_record_id(identifier), data))

    async def update(self, identifier, data, expected_created_at=None):
        if expected_created_at is not None:
            return one(
                await self.query(
                    (
                        "UPDATE $source MERGE "
                        + (
                            "object::extend($values, {title: title_override ?? $values.title})"
                            if "title" in data and "title_override" not in data
                            else "$values"
                        )
                        + " WHERE created_at = $created RETURN AFTER"
                    ),
                    {
                        "source": ensure_record_id(identifier),
                        "values": {**data, "updated_at": now()},
                        "created": expected_created_at,
                    },
                )
            )
        async with self.connection() as db:
            return one(await db.merge(ensure_record_id(identifier), {**data, "updated_at": now()}))

    async def sources(self, offset=0, limit=100, collection=None, focused=None):
        if collection not in (None, "inbox", "library"):
            raise ValueError("Choose Inbox or Library.")
        condition = (
            ""
            if collection is None
            else (
                " WHERE collection = 'library'"
                if collection == "library"
                else " WHERE collection != 'library'"
            )
        )
        if focused is not None:
            condition += (" AND " if condition else " WHERE ") + (
                "focused = true" if focused else "focused != true"
            )
        rows = await self.query(
            "SELECT id, title, kind, original, status, stage, error, created_at, collection, focused, "
            "summary, summary_language FROM source"
            + condition
            + " ORDER BY created_at DESC LIMIT $limit START $offset",
            {"limit": limit, "offset": offset},
        )
        return await self.with_topics(rows)

    async def with_topics(self, rows):
        if not rows:
            return rows
        links = await self.query(
            "SELECT source, topic.id AS id, topic.name AS name, topic.official AS official FROM source_topic "
            "WHERE source IN $sources AND excluded != true AND topic.deleted != true ORDER BY name ASC",
            {"sources": [row["id"] for row in rows]},
        )
        for row in rows:
            row["collection"] = row.get("collection", "inbox")
            row["topics"] = [
                {key: link[key] for key in ("id", "name", "official")}
                for link in links
                if link["source"] == row["id"]
            ]
        return rows

    async def preferences(self):
        defaults = {key: getattr(self.settings, key) for key in PREFERENCE_KEYS}
        async with self.connection() as db:
            row = one(await db.select(ensure_record_id("preferences", "setting")))
            return defaults | {key: row[key] for key in defaults if row and key in row}

    async def set_preferences(self, values):
        allowed = set(PREFERENCE_KEYS)
        if values.keys() - allowed:
            raise ValueError("Unknown preference.")
        await self.query(
            "UPSERT $record MERGE $values RETURN AFTER",
            {
                "record": ensure_record_id("preferences", "setting"),
                "values": values,
            },
        )
        return await self.preferences()

    async def set_language(self, language):
        return await self.set_preferences({"language": language})

    async def history(self, identifier, revision=None):
        condition = ""
        if revision is not None:
            condition = " AND (revision = $revision OR (revision = NONE AND $revision = 0))"
        return await self.query(
            "SELECT * FROM conversation WHERE source = $source"
            + condition
            + " ORDER BY created_at ASC, id ASC",
            {
                "source": ensure_record_id(identifier),
                "revision": revision if revision is not None else 0,
            },
        )

    async def save_turn(
        self, identifier, question, answer, evidence, model, provider="", revision=0
    ):
        async with self.connection() as db:
            return one(
                await db.create(
                    "conversation",
                    {
                        "source": ensure_record_id(identifier),
                        "question": question,
                        "answer": answer,
                        "evidence": evidence,
                        "model": model,
                        "provider": provider,
                        "revision": revision,
                        "created_at": now(),
                    },
                )
            )

    async def clear_history(self, identifier):
        await self.query(
            "DELETE conversation WHERE source = $source", {"source": ensure_record_id(identifier)}
        )
