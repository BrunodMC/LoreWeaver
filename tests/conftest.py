from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from src.app import auth, db, llm
from src.app.main import create_app


@pytest.fixture(autouse=True)
def no_real_llm_calls():
    """Every test starts on an unscripted fake: an LLM call nobody scripted fails instead of spending quota."""
    llm.set_backend(llm.FakeBackend())
    yield
    llm.set_backend(None)


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


@pytest.fixture(autouse=True)
def quick_password_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1_000)  # the real figure costs ~0.3 s per hash


@pytest.fixture
def client(temp_db):
    """The DM, signed in. (The test client isn't loopback, so without the cookie it would be a player.)"""
    with TestClient(create_app()) as test_client:
        with closing(db.get_connection()) as connection:
            test_client.cookies.set(auth.COOKIE, auth.issue(connection))
        yield test_client


@pytest.fixture
def player(temp_db):
    """Someone on the network without a DM session."""
    with TestClient(create_app()) as test_client:
        yield test_client
