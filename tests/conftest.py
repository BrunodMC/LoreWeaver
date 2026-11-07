from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from src.app import db
from src.app.main import create_app


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("LOREWEAVER_DB", str(path))
    return path


@pytest.fixture
def conn(temp_db):
    db.init_db()
    with closing(db.get_connection()) as connection:
        yield connection


@pytest.fixture
def client(temp_db):
    with TestClient(create_app()) as test_client:
        yield test_client
