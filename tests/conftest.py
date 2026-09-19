import os
import uuid
from dataclasses import replace

import pytest
import pytest_asyncio

from signal_inbox.config import Settings
from signal_inbox.database import Database


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_url="ws://127.0.0.1:8019/rpc",
        namespace="signal",
        database="signal_test_" + uuid.uuid4().hex,
        db_user="root",
        db_password="root",
        data_dir=tmp_path,
        api_url="http://127.0.0.1:8020",
        llm_provider="google",
        llm_model="gemini-2.5-flash",
        embedding_provider="google",
        embedding_model="gemini-embedding-001",
        language="Português",
    )


@pytest_asyncio.fixture
async def db(settings):
    if not os.getenv("SIGNAL_INTEGRATION"):
        pytest.skip("Set SIGNAL_INTEGRATION=1 to use local SurrealDB 3")
    config = replace(settings, db_url=os.getenv("SIGNAL_TEST_DB_URL", settings.db_url))
    database = Database(config)
    await database.initialize()
    try:
        yield database
    finally:
        # Only remove the random database created by this fixture.
        assert config.database.startswith("signal_test_")
        async with database.connection() as connection:
            await connection.query(f"REMOVE DATABASE `{config.database}`")
