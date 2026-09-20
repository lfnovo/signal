from unittest.mock import AsyncMock

import pytest

from signal_inbox.ai import AI
from signal_inbox.database import Database
from signal_inbox.diagnostics import diagnostic_stack, safe_error
from signal_inbox.worker import _process_source


def test_original_message_and_chained_traceback_redact_known_secrets(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "private-provider-key")
    try:
        try:
            raise ValueError("Model gemini-test unavailable: private-provider-key")
        except ValueError as cause:
            raise RuntimeError("Summary failed with password p@ss word") from cause
    except RuntimeError as exc:
        stack = diagnostic_stack(exc, ("p@ss word",))
        assert "raise ValueError" in stack
        assert "ValueError: Model gemini-test unavailable: [REDACTED]" in stack
        assert "RuntimeError: Summary failed with password [REDACTED]" in stack
        assert "direct cause" in stack
        assert "private-provider-key" not in stack
        assert "p@ss word" not in stack
        assert (
            safe_error(exc, ("p@ss word",))
            == "RuntimeError: Summary failed with password [REDACTED]"
        )
    assert safe_error(ValueError("Google API key not found. Set GOOGLE_API_KEY.")) == (
        "ValueError: Google API key not found. Set GOOGLE_API_KEY."
    )
    assert "[REDACTED]" in safe_error(ValueError("https://host/?key=p%40ss%20word"), ("p@ss word",))


@pytest.mark.asyncio
async def test_worker_logs_context_and_original_error_and_persists_detail(
    settings, monkeypatch, caplog
):
    db = Database(settings)
    db.update = AsyncMock()
    ai = AI(settings)
    ai.summary_bundle = AsyncMock(
        side_effect=ValueError("Model test-model does not support this request")
    )
    monkeypatch.setattr("signal_inbox.worker.Topics.contexts", AsyncMock(return_value=[]))
    await _process_source(
        db,
        ai,
        {
            "id": "a" * 64,
            "created_at": "2026-01-01",
            "content": "Test content",
        },
    )
    assert "stage=summarizing" in caplog.text
    assert f"provider={settings.llm_provider!r}" in caplog.text
    assert f"model={settings.llm_model!r}" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "ValueError: Model test-model does not support this request" in caplog.text
    values = db.update.await_args.args[1]
    assert values["status"] == "error"
    assert (
        values["error"] == "summarizing: ValueError: Model test-model does not support this request"
    )
