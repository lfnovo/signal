"""Preserve error details while masking configured secret values."""

import os
import traceback
from urllib.parse import quote, quote_plus


def redact_secrets(text: str, secrets=()) -> str:
    values = [
        value
        for name, value in os.environ.items()
        if any(marker in name.upper() for marker in ("API_KEY", "TOKEN", "PASSWORD", "SECRET"))
    ]
    values.extend(value for value in secrets if isinstance(value, str))
    variants = {
        encoded
        for value in values
        if value
        for encoded in (value, quote(value, safe=""), quote_plus(value))
    }
    for value in sorted(variants, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text


def safe_error(exc, secrets=()):
    return redact_secrets(f"{type(exc).__name__}: {exc}", secrets)


def diagnostic_stack(exc, secrets=()):
    # Standard chained traceback, without dumping frame locals.
    return redact_secrets("".join(traceback.format_exception(exc)), secrets)
