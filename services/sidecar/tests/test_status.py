"""B8b: error format, jobs, stats, health, live events (doc 04 §1-2)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ivsidecar import db, handlers, scanner
from ivsidecar.app import create_app
from ivsidecar.events import bus

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WS_OK = ["ivsidecar", f"bearer.{TOKEN}"]


@pytest.fixture()
def api(tmp_path):
    conn = db.open_library(tmp_path / "Library")
    conn.execute("INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('s', 'local_folder', 'P', '/p')")
    for i in range(3):
        handlers.register_file(conn, 1, f"f{i}.jpg", f"k{i}", 1, "1")
    conn.execute(
        "INSERT INTO assets (uid, sha256, mime_type, byte_size, status) VALUES ('a', zeroblob(32), 'image/jpeg', 1, 'failed')"
    )
    conn.execute("UPDATE file_locations SET asset_id = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO jobs (asset_id, stage, status, pipeline_version, last_error, attempts) "
        "VALUES (1, 'thumbnail', 'failed', '1', 'decode_failed: truncated', 3)"
    )
    client = TestClient(create_app(conn, TOKEN, time.time()))
    yield conn, client
    conn.close()


# --------------------------------------------------------------------------- errors
def test_errors_are_problem_json(api):
    conn, client = api
    r = client.get("/v1/assets/999", headers=AUTH)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "not_found" and body["status"] == 404 and body["title"] and body["type"]
    assert "999" in body["detail"]

    r = client.get("/v1/assets", headers=AUTH, params={"limit": 0})
    assert r.status_code == 422 and r.json()["code"] == "validation_failed"
    assert "limit" in r.json()["detail"] and r.json()["errors"]

    r = client.get("/v1/stats")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    r = client.post("/v1/tags", headers=AUTH, json={"name": "a"})
    assert client.post("/v1/tags", headers=AUTH, json={"name": "A"}).json()["code"] == "conflict"
    assert client.get("/v1/nope", headers=AUTH).json()["code"] == "not_found"


# --------------------------------------------------------------------------- jobs
def test_jobs_list_paging_and_retry(api):
    conn, client = api
    page = client.get("/v1/jobs", headers=AUTH, params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_before_id"]
    rest = client.get("/v1/jobs", headers=AUTH, params={"before_id": page["next_before_id"]}).json()
    assert len(page["items"]) + len(rest["items"]) == 4 and rest["next_before_id"] is None

    failed = client.get("/v1/jobs", headers=AUTH, params={"status": "failed"}).json()["items"]
    assert len(failed) == 1
    job = failed[0]
    assert job["stage"] == "thumbnail" and job["path"] == "f0.jpg" and "truncated" in job["last_error"]
    assert client.get("/v1/jobs", headers=AUTH, params={"status": "bogus"}).status_code == 422

    queued = client.get("/v1/jobs", headers=AUTH, params={"status": "queued", "stage": "identify"}).json()["items"]
    assert len(queued) == 3 and {j["path"] for j in queued} == {"f0.jpg", "f1.jpg", "f2.jpg"}
    assert client.post(f"/v1/jobs/{queued[0]['id']}/retry", headers=AUTH).status_code == 409
    assert client.post("/v1/jobs/999/retry", headers=AUTH).status_code == 404

    r = client.post(f"/v1/jobs/{job['id']}/retry", headers=AUTH)
    assert r.json() == {"id": job["id"], "status": "queued"}
    assert conn.execute("SELECT status, attempts FROM jobs WHERE id = ?", (job["id"],)).fetchone() == ("queued", 0)
    assert conn.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "processing"


# --------------------------------------------------------------------------- stats, health
def test_stats_and_health(api):
    conn, client = api
    conn.execute(
        "INSERT INTO assets (uid, sha256, mime_type, byte_size, status, is_favorite) "
        "VALUES ('b', randomblob(32), 'image/png', 1, 'ready', 1)"
    )
    s = client.get("/v1/stats", headers=AUTH).json()
    p = s["photos"]
    assert p["total"] == 2 and p["favorites"] == 1 and p["present"] == 1 and p["missing"] == 1
    assert p["by_status"] == {"failed": 1, "ready": 1}
    assert s["jobs"]["queued"] == 3 and s["sources"] == {"active": 1}
    assert s["db_version"] == s["db_latest"] == db.latest_version()
    h = client.get("/v1/health", headers=AUTH).json()
    assert h["db_version"] == db.latest_version()
    assert h["queue"] == {"queued": 3, "running": 0, "failed": 1}


# --------------------------------------------------------------------------- live events
def test_websocket_rejects_bad_token_and_origin(api):
    conn, client = api
    for protocols, headers in (
        (["ivsidecar", "bearer.wrong-token-0000000000000000"], {}),
        (["ivsidecar"], {}),
        ([f"bearer.{TOKEN}"], {}),  # must also ask for the ivsidecar protocol
        (WS_OK, {"origin": "https://evil.example"}),
    ):
        with (
            pytest.raises(WebSocketDisconnect) as e,
            client.websocket_connect("/v1/events", subprotocols=protocols, headers=headers),
        ):
            pass
        assert e.value.code == 1008
    assert bus.subscribers() == 0


def test_websocket_pushes_coalesced_changes(api):
    conn, client = api
    with client.websocket_connect("/v1/events", subprotocols=WS_OK, headers={"origin": "http://tauri.localhost"}) as ws:
        assert ws.accepted_subprotocol == "ivsidecar"
        assert ws.receive_json()["type"] == "hello"
        assert bus.subscribers() == 1
        client.patch("/v1/assets/1", headers=AUTH, json={"is_favorite": True})
        client.post("/v1/assets/1/tags", headers=AUTH, json={"names": ["x"]})
        client.post("/v1/collections", headers=AUTH, json={"name": "c"})
        msg = ws.receive_json()
        assert msg["type"] == "batch"
        types = {e["type"]: e for e in msg["events"]}
        assert types["asset.updated"]["ids"] == [1]  # three edits, one entry
        assert {"tags.updated", "collections.updated"} <= set(types)
    time.sleep(0.1)
    assert bus.subscribers() == 0


def test_10k_file_scan_sends_few_messages(tmp_path):
    """Plan B8 acceptance: a 10,000-file scan sends fewer than 100 messages."""
    src = tmp_path / "Photos"
    for d in range(100):
        (src / f"d{d:03d}").mkdir(parents=True)
        for i in range(100):
            (src / f"d{d:03d}" / f"f{i}.jpg").write_bytes(b"")
    lib = tmp_path / "Library"
    conn = db.open_library(lib)
    sid = scanner.add_source(conn, str(src), lib)
    received: list[dict] = []
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def consume():
        _, q = bus.subscribe(loop)
        ready.set()
        while True:
            received.append(await q.get())

    def run_loop():
        loop.create_task(consume())
        loop.run_forever()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    ready.wait(5)
    sc = scanner.Scanner(lib)
    try:
        t0 = time.monotonic()
        sc.start(sid)
        deadline = time.monotonic() + 120
        while sc.running() and time.monotonic() < deadline:
            time.sleep(0.05)
        took = time.monotonic() - t0
        time.sleep(bus.flush_s * 3)  # last batch
        progress = [e for m in received for e in m["events"] if e["type"] == "scan.progress"]
        assert progress and progress[-1]["status"] == "succeeded" and progress[-1]["files_seen"] == 10000
        print(f"\n10k-file scan: {took:.1f}s, {len(received)} event messages")
        assert len(received) < 100
    finally:
        sc.stop()
        for sub_id in list(bus._subs):
            bus.unsubscribe(sub_id)

        def shut_down():  # cancel the consumer first, so it does not outlive its loop
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.call_soon(loop.stop)

        loop.call_soon_threadsafe(shut_down)
        t.join(5)
        loop.close()
        conn.close()
