"""Portable topic context for prompts and future agent consumers."""

import hashlib
import json

from .database import public


def context_record(topic):
    return {
        "id": public(topic["id"]),
        "name": topic["name"],
        "definition": topic.get("definition") or "",
        "personal_context": topic.get("personal_context") or "",
    }


def context_signature(records):
    canonical = sorted(records, key=lambda item: item["id"])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def prompt_catalog(records, text="", max_chars=40000):
    """Bound prompt size; prefer context that overlaps the source when the catalog grows."""
    words = set(text.casefold().split())
    ranked = sorted(
        records,
        key=lambda r: (
            -len(
                words
                & set(
                    (r["name"] + " " + r["definition"] + " " + r["personal_context"])
                    .casefold()
                    .split()
                )
            )
        ),
    )
    selected, size = [], 0
    for record in ranked:
        length = len(json.dumps(record, ensure_ascii=False))
        if size + length > max_chars:
            continue
        selected.append(record)
        size += length
    return selected
