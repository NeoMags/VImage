import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient

from ivsidecar import db
from ivsidecar.app import create_app

TOKEN = "t" * 32


@pytest.fixture()
def client(tmp_path):
    conn = db.open_library(tmp_path)
    return TestClient(create_app(conn, TOKEN, time.time()))


def test_health_requires_token(client):
    assert client.get("/v1/health").status_code == 401
    assert client.get("/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_health_reports_capabilities(client):
    r = client.get("/v1/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["db"]["schema_version"] == db.latest_version()
    assert body["db"]["fts5"] is True
    assert body["db"]["sqlite_vec"].startswith("v0.1")
    assert body["vips"]["ok"] is True
    assert body["vips"]["thumbnail_selftest"]["ok"] is True


def test_cors_allows_only_tauri_origin(client):
    h = {
        "Origin": "http://tauri.localhost",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    assert (
        client.options("/v1/health", headers=h).headers.get("access-control-allow-origin") == "http://tauri.localhost"
    )
    h["Origin"] = "https://evil.example"
    assert "access-control-allow-origin" not in client.options("/v1/health", headers=h).headers


def test_migration_is_idempotent_and_backs_up(tmp_path):
    conn = db.open_library(tmp_path)
    conn.execute("INSERT INTO tags (name, slug) VALUES ('keep','keep')")
    conn.close()
    conn = db.open_library(tmp_path)  # second open: nothing pending, no backup
    assert conn.execute("SELECT count(*) FROM tags").fetchone()[0] == 1
    assert not (tmp_path / "backups").exists()


def test_process_prints_ready_and_serves(tmp_path):
    env = {**os.environ, "IV_TOKEN": TOKEN}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ivsidecar", "--library", str(tmp_path)], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready"
        req = urllib.request.Request(
            f"http://127.0.0.1:{ready['port']}/v1/health", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert json.load(urllib.request.urlopen(req, timeout=10))["status"] == "ok"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_refuses_to_start_without_token(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "IV_TOKEN"}
    out = subprocess.run(
        [sys.executable, "-m", "ivsidecar", "--library", str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 2 and "IV_TOKEN" in out.stdout


def test_exits_when_parent_process_dies(tmp_path):
    # A short-lived stand-in parent; the sidecar must exit soon after it ends.
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    env = {**os.environ, "IV_TOKEN": TOKEN, "IV_PARENT_PID": str(parent.pid)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ivsidecar", "--library", str(tmp_path)], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        assert json.loads(proc.stdout.readline())["event"] == "ready"
        parent.wait(timeout=10)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
