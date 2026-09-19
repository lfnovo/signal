"""Global topic vocabulary and source associations, published transactionally."""

import unicodedata
from itertools import combinations
from math import sqrt

from .database import ensure_record_id, now, one, public
from .topic_context import context_record


def topic_name(name):
    name = " ".join(unicodedata.normalize("NFKC", name).split())
    if not name or len(name) > 80:
        raise ValueError("Use a topic name between 1 and 80 characters.")
    return name, name.casefold()


class Topics:
    def __init__(self, db):
        self.db = db

    async def transaction(self, sql, values):
        async with self.db.connection() as connection:
            result = await connection.query_raw(
                "BEGIN TRANSACTION; " + sql + "; COMMIT TRANSACTION;", values
            )
        if "error" in result or any(row.get("status") != "OK" for row in result.get("result", [])):
            raise ValueError(
                "This change could not be saved. The topic may already exist or have changed; refresh and try again."
            )
        rows = result.get("result", [])
        return next(
            (row["result"] for row in reversed(rows) if row.get("result") is not None), None
        )

    async def list(self, official=False):
        return await self.db.query(
            "SELECT *, (SELECT VALUE source FROM source_topic WHERE topic = $parent.id AND excluded != true) AS sources "
            "FROM topic WHERE deleted != true"
            + (" AND official = true" if official else "")
            + " ORDER BY name ASC"
        )

    async def get(self, identifier):
        rows = await self.db.query(
            "SELECT * FROM $topic WHERE deleted != true",
            {"topic": ensure_record_id(identifier, "topic")},
        )
        return one(rows)

    async def for_source(self, identifier):
        return await self.db.query(
            "SELECT topic.id AS id, topic.name AS name, topic.official AS official, "
            "topic.definition AS definition, topic.personal_context AS personal_context, manual "
            "FROM source_topic WHERE source = $source AND excluded != true AND topic.deleted != true ORDER BY name ASC",
            {"source": ensure_record_id(identifier)},
        )

    async def contexts(self, source=None):
        records = await self.for_source(source) if source else await self.list(official=True)
        return [context_record(t) for t in records if t.get("official")]

    async def save_context(self, identifier, definition, personal_context):
        await self.db.query(
            "UPDATE $topic SET definition = $definition, personal_context = $personal, "
            "context_updated_at = time::now() WHERE deleted != true",
            {
                "topic": ensure_record_id(identifier, "topic"),
                "definition": definition.strip(),
                "personal": personal_context.strip(),
            },
        )

    async def patch_context(self, identifier, values):
        await self.db.query(
            "UPDATE $topic MERGE $values WHERE deleted != true",
            {
                "topic": ensure_record_id(identifier, "topic"),
                "values": {
                    **{
                        key: value.strip()
                        for key, value in values.items()
                        if key in {"definition", "personal_context"}
                    },
                    "context_updated_at": now(),
                },
            },
        )

    async def workspace(self, include_inbox=False):
        """One scoped evidence set drives counts, graph edges, and intersection drill-downs."""
        catalog = public(
            await self.db.query("SELECT * FROM topic WHERE deleted != true ORDER BY name ASC")
        )
        rows = public(
            await self.db.query(
                "SELECT source.id AS source_id, source.title AS title, source.created_at AS created_at, "
                "source.collection AS collection, source.focused AS focused, topic AS topic_id "
                "FROM source_topic WHERE excluded != true AND topic.deleted != true "
                "AND source.id != NONE"
                + ("" if include_inbox else " AND source.collection = 'library'")
            )
        )
        by_source, sources = {}, {}
        memberships = {t["id"]: set() for t in catalog}
        for row in rows:
            tid, sid = row["topic_id"], row["source_id"]
            if tid not in memberships:
                continue
            memberships[tid].add(sid)
            by_source.setdefault(sid, set()).add(tid)
            sources[sid] = {
                "id": sid,
                "title": row["title"],
                "created_at": row["created_at"],
                "collection": row.get("collection") or "inbox",
                "focused": bool(row.get("focused")),
            }
        shared = {}
        for sid, tids in by_source.items():
            for pair in combinations(sorted(tids), 2):
                shared.setdefault(pair, set()).add(sid)
        for topic in catalog:
            ids = memberships[topic["id"]]
            topic["count"] = len(ids)
            topic["source_ids"] = sorted(ids)
            topic["last_activity"] = max((sources[sid]["created_at"] for sid in ids), default="")
            topic.setdefault("definition", "")
            topic.setdefault("personal_context", "")
        edges = [
            {
                "source": a,
                "target": b,
                "shared_count": len(ids),
                "source_ids": sorted(ids),
                "strength": len(ids) / sqrt(len(memberships[a]) * len(memberships[b])),
                "kind": "shared_sources",
            }
            for (a, b), ids in shared.items()
        ]
        edges.sort(key=lambda e: (-e["strength"], -e["shared_count"], e["source"], e["target"]))
        return {
            "topics": catalog,
            "edges": edges,
            "sources": sorted(sources.values(), key=lambda s: s["created_at"], reverse=True),
            "scope": "all" if include_inbox else "library",
        }

    async def sources(self, identifier, offset=0, limit=51):
        return await self.db.query(
            "SELECT id, title, kind, original, status, stage, created_at, summary, collection, focused FROM source "
            "WHERE id IN (SELECT VALUE source FROM source_topic WHERE topic = $topic AND excluded != true) "
            "ORDER BY created_at DESC LIMIT $limit START $offset",
            {"topic": ensure_record_id(identifier, "topic"), "offset": offset, "limit": limit},
        )

    async def create(self, name, source=None):
        name, normalized = topic_name(name)
        values = {"name": name, "normalized": normalized}
        sql = "LET $existing = (SELECT * FROM topic WHERE normalized = $normalized OR $normalized IN (aliases ?? []) LIMIT 1); "
        sql += "LET $t = IF array::len($existing) > 0 { $existing[0].id } ELSE { (CREATE topic SET name = $name, normalized = $normalized, official = true, created_at = time::now())[0].id }; "
        sql += 'IF $existing[0].merged_into != NONE { THROW "Use the merged topic"; }; UPDATE $t SET official = true, deleted = false; '
        if source:
            values["source"] = ensure_record_id(source)
            sql = (
                'LET $source_record = (SELECT * FROM ONLY $source); IF $source_record = NONE { THROW "Source missing"; }; '
                + sql
            )
            sql += "UPSERT source_topic SET source = $source, topic = $t, manual = true, excluded = false WHERE source = $source AND topic = $t; "
        sql += "RETURN (SELECT * FROM ONLY $t)"
        return await self.transaction(sql, values)

    async def attach(self, source, topic, remove=False):
        await self.transaction(
            'LET $source_record = (SELECT * FROM ONLY $source); IF $source_record = NONE { THROW "Source missing"; }; LET $record = (SELECT * FROM ONLY $topic); IF $record.deleted = true OR $record.name = NONE { THROW "Topic missing"; }; '
            "UPSERT source_topic SET source = $source, topic = $topic, manual = true, excluded = $excluded WHERE source = $source AND topic = $topic",
            {
                "source": ensure_record_id(source),
                "topic": ensure_record_id(topic, "topic"),
                "excluded": remove,
            },
        )

    async def approve(self, identifier):
        await self.db.query(
            "UPDATE $topic SET official = true WHERE deleted != true",
            {"topic": ensure_record_id(identifier, "topic")},
        )

    async def rename(self, identifier, name):
        name, normalized = topic_name(name)
        await self.transaction(
            "LET $collision = (SELECT id FROM topic WHERE id != $topic AND "
            "(normalized = $normalized OR $normalized IN (aliases ?? []))); "
            "IF array::len($collision) > 0 { THROW 'Name already in use'; }; "
            "UPDATE $topic SET aliases = array::distinct(array::append(aliases ?? [], normalized)), "
            "name = $name, normalized = $normalized WHERE deleted != true",
            {
                "topic": ensure_record_id(identifier, "topic"),
                "name": name,
                "normalized": normalized,
            },
        )

    async def delete(self, identifier):
        await self.transaction(
            "UPDATE $topic SET deleted = true; DELETE source_topic WHERE topic = $topic",
            {"topic": ensure_record_id(identifier, "topic")},
        )

    async def merge(self, identifier, target):
        if identifier == target:
            raise ValueError("Choose a different destination topic.")
        await self.transaction(
            """
            LET $origin = (SELECT * FROM ONLY $from); LET $destination = (SELECT * FROM ONLY $to);
            IF $origin.deleted = true OR $destination.deleted = true OR $destination.name = NONE { THROW "Topic missing"; };
            LET $links = (SELECT * FROM source_topic WHERE topic = $from);
            FOR $link IN $links {
                LET $existing = (SELECT * FROM source_topic WHERE source = $link.source AND topic = $to LIMIT 1);
                IF array::len($existing) = 0 {
                    CREATE source_topic SET source = $link.source, topic = $to, manual = $link.manual, excluded = $link.excluded;
                } ELSE {
                    UPDATE source_topic SET manual = manual = true OR $link.manual = true,
                        excluded = excluded = true AND $link.excluded = true WHERE source = $link.source AND topic = $to;
                };
            };
            UPDATE $to SET official = official = true OR $origin.official = true;
            UPDATE $to SET
                definition = IF string::len($origin.definition ?? '') > 0 {
                    string::concat($destination.definition ?? '', '\n\n', $origin.name, ': ', $origin.definition)
                } ELSE { $destination.definition ?? '' },
                personal_context = IF string::len($origin.personal_context ?? '') > 0 {
                    string::concat($destination.personal_context ?? '', '\n\n', $origin.name, ': ', $origin.personal_context)
                } ELSE { $destination.personal_context ?? '' }, context_updated_at = time::now();
            UPDATE topic SET merged_into = $to WHERE merged_into = $from;
            UPDATE $from SET deleted = true, merged_into = $to;
            DELETE source_topic WHERE topic = $from
        """,
            {
                "from": ensure_record_id(identifier, "topic"),
                "to": ensure_record_id(target, "topic"),
            },
        )

    async def publish(self, source, values, names, expected_created_at=None):
        candidates = []
        for name in names:
            label, normalized = topic_name(name)
            if normalized not in {item["normalized"] for item in candidates}:
                candidates.append({"name": label, "normalized": normalized})
        await self.transaction(
            """
            LET $current = (SELECT * FROM ONLY $source);
            IF $current = NONE OR ($expected != NONE AND $current.created_at != $expected) { THROW "Source no longer exists"; };
            DELETE source_topic WHERE source = $source AND manual != true;
            FOR $candidate IN $candidates {
                LET $found = (SELECT * FROM topic WHERE normalized = $candidate.normalized OR $candidate.normalized IN (aliases ?? []) LIMIT 1);
                LET $topic = IF array::len($found) > 0 { $found[0].id } ELSE {
                    (CREATE topic SET name = $candidate.name, normalized = $candidate.normalized,
                        official = false, deleted = false, created_at = time::now())[0].id
                };
                LET $target = IF $found[0].merged_into != NONE { $found[0].merged_into } ELSE { $topic };
                LET $record = (SELECT * FROM ONLY $target);
                IF $record.deleted != true {
                    LET $links = (SELECT * FROM source_topic WHERE source = $source AND topic = $target LIMIT 1);
                    IF array::len($links) = 0 {
                        CREATE source_topic SET source = $source, topic = $target, manual = false, excluded = false;
                    };
                };
            };
            UPDATE $source MERGE $values;
            UPDATE $source SET title = title_override WHERE title_override != NONE;
        """,
            {
                "source": ensure_record_id(source),
                "values": values,
                "candidates": candidates,
                "expected": expected_created_at,
            },
        )
