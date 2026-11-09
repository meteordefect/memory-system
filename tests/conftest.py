import os

import psycopg
import pytest

from app.store import init_schema


@pytest.fixture()
def conn():
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    with psycopg.connect(url) as connection:
        init_schema(connection)
        connection.execute("TRUNCATE entities, facts, chunk_feedback RESTART IDENTITY CASCADE")
        connection.execute("DELETE FROM meta WHERE key LIKE 'graph:%%'")
        connection.commit()
        yield connection
