import pytest

from signal_inbox.extraction import content_config, extraction_options, run_extraction


def test_selected_transcription_overrides_both_content_core_aliases(settings):
    options = extraction_options(settings) | {"stt_provider": "openai", "stt_model": "whisper-1"}
    config = content_config(options)
    assert config.audio_provider == config.stt_provider == "openai"
    assert config.audio_model == config.stt_model == "whisper-1"


@pytest.mark.asyncio
async def test_extraction_timeout_stops_subprocess(settings):
    with pytest.raises(TimeoutError):
        await run_extraction(
            {"kind": "file", "original": "none", "file_path": "/missing"},
            extraction_options(settings),
            timeout=0.001,
        )
