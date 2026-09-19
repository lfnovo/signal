"""Environment configuration. Never expose provider secrets in the UI."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PREFERENCE_KEYS = (
    "language",
    "llm_provider",
    "llm_model",
    "embedding_provider",
    "embedding_model",
    "url_engine",
    "document_engine",
    "stt_provider",
    "stt_model",
)
EXTRACTION_KEYS = ("url_engine", "document_engine", "stt_provider", "stt_model")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_url: str
    namespace: str
    database: str
    db_user: str
    db_password: str
    data_dir: Path
    api_url: str
    llm_provider: str
    llm_model: str
    embedding_provider: str
    embedding_model: str
    language: str
    password: str | None = None
    host: str = "127.0.0.1"
    allowed_hosts: tuple[str, ...] = ()
    url_engine: str = "simple"
    document_engine: str = "simple"
    stt_provider: str = "google"
    stt_model: str = "gemini-2.5-flash"
    max_upload_bytes: int = 100 * 1024 * 1024
    chunk_chars: int = 6000
    context_chars: int = 60000

    @classmethod
    def load(cls):
        root = Path(os.getenv("SIGNAL_HOME", project_root())).expanduser().resolve()
        load_dotenv(root / ".env", override=False)
        return cls(
            db_url=os.getenv("SIGNAL_DB_URL", "ws://127.0.0.1:8019/rpc"),
            namespace=os.getenv("SIGNAL_DB_NAMESPACE", "signal"),
            database=os.getenv("SIGNAL_DB_DATABASE", "signal"),
            db_user=os.getenv("SIGNAL_DB_USER", "root"),
            db_password=os.getenv("SIGNAL_DB_PASSWORD", "root"),
            data_dir=Path(os.getenv("SIGNAL_DATA_DIR", str(root / ".signal")))
            .expanduser()
            .resolve(),
            api_url=os.getenv("SIGNAL_API_URL", "http://127.0.0.1:8020").rstrip("/"),
            llm_provider=os.getenv("SIGNAL_LLM_PROVIDER", "google"),
            llm_model=os.getenv("SIGNAL_LLM_MODEL", "gemini-2.5-flash"),
            embedding_provider=os.getenv("SIGNAL_EMBEDDING_PROVIDER", "google"),
            embedding_model=os.getenv("SIGNAL_EMBEDDING_MODEL", "gemini-embedding-001"),
            language=os.getenv("SIGNAL_SUMMARY_LANGUAGE", "Português"),
            password=os.getenv("SIGNAL_PASSWORD") or None,
            host=os.getenv("SIGNAL_HOST", "127.0.0.1"),
            allowed_hosts=tuple(
                value.strip()
                for value in os.getenv("SIGNAL_ALLOWED_HOSTS", "").split(",")
                if value.strip()
            ),
            url_engine=os.getenv("SIGNAL_URL_ENGINE", "simple"),
            document_engine=os.getenv("SIGNAL_DOCUMENT_ENGINE", "simple"),
            stt_provider=os.getenv(
                "SIGNAL_STT_PROVIDER", os.getenv("CCORE_STT_PROVIDER", "google")
            ),
            stt_model=os.getenv(
                "SIGNAL_STT_MODEL", os.getenv("CCORE_STT_MODEL", "gemini-2.5-flash")
            ),
        )
