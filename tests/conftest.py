import os

# Set before the app is imported: the tests never call the real model or touch the dev database.
os.environ["LLM_MODE"] = "mock"
os.environ["ADMIN_PASSWORD"] = "test-password"
os.environ["DATABASE_PATH"] = os.devnull

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.security import limiter


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "test.db")
    limiter.reset()
    with TestClient(app) as test_client:  # runs startup: creates tables and the demo bot
        yield test_client


@pytest.fixture
def admin(client):
    response = client.post("/admin/api/login", json={"password": "test-password"})
    assert response.status_code == 200
    return client
