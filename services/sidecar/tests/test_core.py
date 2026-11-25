"""B3: settings API, log file, library folder default."""

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from ivsidecar import __main__ as entry
from ivsidecar import db, logs
from ivsidecar.app import create_app

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def lib(tmp_path):
    return tmp_path


@pytest.fixture()
def client(lib):
    return TestClient(create_app(db.open_library(lib), TOKEN, time.time()))


def test_resources_defaults(client):
    r = client.get("/v1/settings/resources", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["threads"] >= 1 and body["gpu"] == "auto" and body["max_ram_mb"] == 4096


def test_resources_requires_token(client):
    assert client.get("/v1/settings/resources").status_code == 401
    assert client.put("/v1/settings/resources", json={}).status_code == 401


def test_resources_saved_and_survive_restart(lib, client):
    new = {"threads": 3, "max_ram_mb": 8192, "only_when_idle": True, "only_on_ac": False, "gpu": "directml"}
    assert client.put("/v1/settings/resources", headers=AUTH, json=new).json() == new
    reopened = TestClient(create_app(db.open_library(lib), TOKEN, time.time()))
    assert reopened.get("/v1/settings/resources", headers=AUTH).json() == new


@pytest.mark.parametrize(
    "bad",
    [{"threads": 0}, {"threads": 1000}, {"gpu": "cuda"}, {"max_ram_mb": 10}, {"unknown": 1}, {"threads": "four"}],
)
def test_resources_rejects_invalid(client, bad):
    assert client.put("/v1/settings/resources", headers=AUTH, json=bad).status_code == 422
    assert client.get("/v1/settings/resources", headers=AUTH).json()["gpu"] == "auto"  # unchanged


def test_corrupt_stored_settings_fall_back_to_defaults(lib, client):
    conn = db.open_library(lib)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('resources', ?)", (json.dumps({"gpu": "x"}),))
    assert client.get("/v1/settings/resources", headers=AUTH).json()["gpu"] == "auto"


def test_log_file_written(tmp_path):
    path = logs.setup(tmp_path / "logs")
    logging.getLogger("ivsidecar.test").info("hello from test")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "hello from test" in path.read_text(encoding="utf-8")


def test_default_library_dir_is_separate_from_install_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert entry.default_library_dir() == tmp_path / "ImageVault" / "Library"
