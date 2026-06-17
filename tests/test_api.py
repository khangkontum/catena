from __future__ import annotations

from fastapi.testclient import TestClient

from catena.api import create_app
from catena.config import Settings


def test_api_initializes_and_serves_default_table(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app) as client:
        health = client.get("/health")
        tables = client.get("/tables")
        matrix = client.get("/tables/1/matrix")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert tables.status_code == 200
    assert tables.json()[0]["name"] == "Default"
    assert matrix.status_code == 200
    assert matrix.json()["table"]["name"] == "Default"


def test_api_exposes_batch_paper_upload(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))

    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert "/papers/upload-batch" in schema["paths"]
