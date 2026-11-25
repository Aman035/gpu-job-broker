import pytest
from fastapi.testclient import TestClient

from broker.api import create_app, parse_time
from broker.broker import Broker
from broker.config import BrokerConfig
from broker.provider import FakeProvider
from broker.store import Store
from conftest import reliable_provider_config

START = parse_time("2025-03-03T00:00:00Z")  # same default as broker-serve


@pytest.fixture
def client():
    broker = Broker(FakeProvider(reliable_provider_config()), Store(), BrokerConfig(), start_time=START)
    return TestClient(create_app(broker))


def submit(client, key=None, **body):
    payload = {"team": "nlp", "gpus": 1, "estimated_minutes": 30, "priority": "normal", **body}
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/jobs", json=payload, headers=headers)


def test_submit_and_get(client):
    r = submit(client)
    assert r.status_code == 201
    job = r.json()
    assert job["status"] == "queued" and job["submitted_at"] == "2025-03-03T00:00:00Z"
    assert client.get(f"/jobs/{job['id']}").json()["id"] == job["id"]


def test_idempotency_key(client):
    first = submit(client, key="abc")
    again = submit(client, key="abc")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    assert submit(client, key="abc", gpus=2).status_code == 409
    assert len(client.get("/jobs").json()) == 1


def test_validation_errors(client):
    assert submit(client, gpus=3).status_code == 422
    assert submit(client, priority="urgent").status_code == 422
    assert submit(client, estimated_minutes=0).status_code == 422
    assert client.get("/jobs/nope").status_code == 404


def test_lifecycle_through_api(client):
    job_id = submit(client).json()["id"]
    assert client.get("/nodes").json()[0]["state"] == "booting"
    assert [j["id"] for j in client.get("/queue").json()] == [job_id]

    client.post("/clock/advance", json={"seconds": 600})
    assert client.get(f"/jobs/{job_id}").json()["status"] == "running"
    assert client.get("/queue").json() == []

    r = client.post(f"/jobs/{job_id}/complete", json={"outcome": "succeeded"})
    assert r.json()["status"] == "succeeded"
    assert client.post(f"/jobs/{job_id}/cancel").status_code == 409


def test_cancel(client):
    job_id = submit(client).json()["id"]
    assert client.post(f"/jobs/{job_id}/cancel").json()["status"] == "cancelled"


def test_cost_report(client):
    submit(client)
    client.post("/clock/advance", json={"seconds": 3600})
    report = client.get("/reports/cost").json()
    assert report["teams"][0]["team"] == "nlp"
    assert report["total_cost"] == pytest.approx(1.0)
    window = client.get(
        "/reports/cost", params={"start": "2025-03-03T00:00:00Z", "end": "2025-03-03T00:30:00Z"}
    ).json()
    assert window["total_cost"] == pytest.approx(0.5)
    assert client.get("/reports/cost", params={"start": "yesterday"}).status_code == 422
