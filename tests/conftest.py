import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh SQLite file per test, so tests never share state."""
    monkeypatch.setenv("BIKEPOOL_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("PLATFORM_UPI_ID", "test@upi")
    monkeypatch.setenv("PLATFORM_FEE_INR", "199")
    from app.main import app

    with TestClient(app) as c:  # context manager runs the lifespan (DB init + seed)
        yield c


@pytest.fixture()
def bike_id(client):
    return client.get("/api/bikes", params={"q": "Hunter"}).json()[0]["id"]


def join(client, bike_id, **over):
    body = {"bike_id": bike_id, "city": "hyderabad", "name": "Rahul Kumar", "phone": "9876543210", **over}
    return client.post("/api/pools/join", json=body)
