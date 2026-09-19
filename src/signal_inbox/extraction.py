"""Content-core configuration and a cancellable subprocess for heavy extraction."""

import asyncio
import importlib.util
import json
import os
import signal
import sys

from .config import EXTRACTION_KEYS

URL_ENGINES = ("auto", "simple", "jina", "firecrawl", "crawl4ai")
DOCUMENT_ENGINES = ("auto", "simple", "docling")
ENGINE_CHOICES = {
    "url_engine": [
        ("simple", "Simple — fast, lightweight pages"),
        ("auto", "Auto — let content-core choose"),
        ("jina", "Jina Reader"),
        ("firecrawl", "Firecrawl"),
        ("crawl4ai", "Crawl4AI"),
    ],
    "document_engine": [
        ("simple", "Simple — standard text extraction"),
        ("auto", "Auto — let content-core choose"),
        ("docling", "Docling — layout, tables & OCR"),
    ],
}


def validate_engines(values):
    for field, choices in (("url_engine", URL_ENGINES), ("document_engine", DOCUMENT_ENGINES)):
        if field in values and values[field] not in choices:
            raise ValueError(f"Choose a supported {field.replace('_', ' ')}.")


def extraction_options(settings):
    return {key: getattr(settings, key) for key in EXTRACTION_KEYS}


def content_config(options):
    from content_core import ContentCoreConfig

    validate_engines(options)
    # Set both aliases to prevent content-core's audio_* override from ignoring the chosen STT.
    return ContentCoreConfig(
        **options, audio_provider=options["stt_provider"], audio_model=options["stt_model"]
    )


async def extract_in_process(source, options):
    """Runs only in the child process, keeping Docling off the web event loop."""
    if options["document_engine"] == "docling" and importlib.util.find_spec("docling") is None:
        raise ValueError(
            "Docling is not installed. Run uv sync to install this project’s dependencies."
        )
    from content_core import check_file_support, extract_content
    from content_core.processors.document.docling import DOCLING_SUPPORTED

    config = content_config(options)
    kwargs = (
        {"url": source["original"]}
        if source["kind"] == "url"
        else {"file_path": source["file_path"]}
    )
    if source["kind"] == "file":
        support = await check_file_support(
            source["file_path"], config=config.model_copy(update={"document_engine": "simple"})
        )
        # content-core 2.0.7 routes *every* MIME to Docling if forced, including videos.
        # The document preference must not hijack media/text processors.
        if support.identified_type not in DOCLING_SUPPORTED:
            config = config.model_copy(update={"document_engine": "simple"})
    result = await extract_content(**kwargs, config=config)
    if not result.content.strip():
        raise ValueError(
            "No readable content was extracted. Try another engine or check the source format."
        )
    value = result.model_dump(mode="json")
    value["extraction_settings"] = options
    return value


async def run_extraction(source, options, timeout=3600):
    payload = {
        "source": {key: source[key] for key in ("kind", "original", "file_path") if key in source},
        "options": options,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "signal_inbox.extraction",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate(json.dumps(payload).encode())
        if process.returncode:
            raise RuntimeError("Extraction subprocess failed")
        response = json.loads(output)
        if "error" in response:
            raise ValueError(response["error"])
        return response["result"]
    finally:
        if process.returncode is None:
            # Also stop ffmpeg/browser children on cancellation or timeout.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def main():
    payload = json.load(sys.stdin)
    # Third-party engines sometimes print progress to stdout; reserve it for the JSON result.
    from contextlib import redirect_stdout

    try:
        with redirect_stdout(sys.stderr):
            value = asyncio.run(extract_in_process(payload["source"], payload["options"]))
        result = {"result": value}
    except Exception as exc:
        # Provider errors may contain keys in URLs. Never forward their raw messages.
        result = {
            "error": f"{type(exc).__name__}: extraction failed. Check the engine setup, file format and transcription provider."
        }
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
