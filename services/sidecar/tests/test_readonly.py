"""B20: the read-only guarantee (PRD rule 1, doc 05).

A real engine process (with its worker processes) runs the whole pipeline over the 50-file
fixture folder, made read-only, and every edit the API offers. The test passes only if:

1. the audit hook (tests/readonly_audit/sitecustomize.py) saw no write, create, delete, rename,
   re-time or re-permission of anything under the source folder, in any engine process;
2. the folder is byte-for-byte as before: same paths, sizes, mtimes and SHA-256;
3. no job failed with a permission error (that would mean something tried to write).

Native code (libvips, ExifTool) is invisible to the audit hook; (2) catches what it might do,
and ExifTool's argument guard (`assert_read_only`) is tested directly below.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fixtures_gen import build
from PIL import Image

from ivsidecar.probes import find_exiftool
from ivsidecar.stages.exiftool import ExifToolError, assert_read_only

if not find_exiftool():
    if os.environ.get("IV_REQUIRE_EXIFTOOL") == "1":
        raise RuntimeError("IV_REQUIRE_EXIFTOOL=1 but exiftool was not found")
    pytest.skip("exiftool not installed", allow_module_level=True)

TOKEN = "r" * 32
AUDIT_DIR = Path(__file__).parent / "readonly_audit"
PERMISSION_WORDS = ("permission", "errno 13", "read-only", "readonly", "access is denied")


def snapshot(root: Path) -> dict[str, tuple]:
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.stat()
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            out[rel + "/"] = ("dir", st.st_mtime_ns)
        else:
            out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    out["./"] = ("dir", root.stat().st_mtime_ns)
    return out


def set_read_only(root: Path, read_only: bool) -> None:
    """POSIX: no write bits. Windows: the read-only attribute on files (folders ignore it)."""
    paths = [*root.rglob("*"), root]
    for p in paths:
        if p.is_dir():
            if os.name != "nt":
                p.chmod(0o555 if read_only else 0o755)
        else:
            p.chmod(stat.S_IREAD if read_only else stat.S_IREAD | stat.S_IWRITE)


class Engine:
    def __init__(self, library: Path, source: Path, log: Path):
        env = {
            **os.environ,
            "IV_TOKEN": TOKEN,
            "IV_AUDIT_ROOTS": str(source),
            "IV_AUDIT_LOG": str(log),
            "PYTHONPATH": os.pathsep.join([str(AUDIT_DIR), os.environ.get("PYTHONPATH", "")]),
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ivsidecar", "--library", str(library)],
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
        line = self.proc.stdout.readline()  # type: ignore[union-attr]
        ready = json.loads(line)
        assert ready["event"] == "ready", line
        self.http = httpx.Client(
            base_url=f"http://127.0.0.1:{ready['port']}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60
        )

    def get(self, url, **params):
        r = self.http.get(url, params=params)
        assert r.status_code == 200, r.text
        return r.json()

    def send(self, method, url, ok=(200, 201, 204), **kw):
        r = self.http.request(method, url, **kw)
        assert r.status_code in ok, f"{method} {url}: {r.status_code} {r.text}"
        return r.json() if r.content else None

    def idle(self, timeout=240.0) -> None:
        """Waits until no scan runs and no job is queued or running."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sources = self.get("/v1/sources")
            jobs = self.get("/v1/jobs/stats")["jobs"]
            if not any(s["scanning"] for s in sources) and not jobs.get("queued") and not jobs.get("running"):
                return
            time.sleep(0.5)
        raise AssertionError(f"engine still busy after {timeout} s: {jobs}")

    def stop(self) -> None:
        self.http.close()
        self.proc.terminate()
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture()
def world(tmp_path):
    src = tmp_path / "Photos"
    src.mkdir()
    build(src)
    (src / "sub" / "deeper").mkdir(parents=True)
    shutil.copy2(next(src.glob("*.jpg")), src / "sub" / "deeper" / "nested.jpg")
    set_read_only(src, True)
    log = tmp_path / "audit.jsonl"
    log.touch()
    yield src, tmp_path / "Library", log
    set_read_only(src, False)


def all_assets(eng: Engine) -> list[dict]:
    items = []
    for hidden in (False, True):
        cursor = None
        while True:
            page = eng.get("/v1/assets", hidden=hidden, limit=200, **({"cursor": cursor} if cursor else {}))
            items += page["items"]
            cursor = page["next_cursor"]
            if not cursor:
                break
    return items


def test_the_engine_never_changes_a_source_folder(world):
    src, library, log = world
    before = snapshot(src)
    eng = Engine(library, src, log)
    try:
        # 1. Add the folder: full scan, then every stage on every file.
        source_id = eng.send("POST", "/v1/sources", json={"path": str(src)})["id"]
        eng.idle()
        assets = all_assets(eng)
        n_files = sum(1 for p in src.rglob("*") if p.is_file())
        seen = sum(eng.get("/v1/sources")[0]["files"].values())
        assert seen == n_files >= 50, (seen, n_files)  # every file found (duplicates share one asset)
        assert len(assets) >= 40

        # 2. Previews, reveal and every edit the API offers.
        images = [a for a in assets if a["kind"] in ("image", "screenshot") and a["status"] == "ready"]
        assert images
        for a in images[:25]:
            eng.send("POST", f"/v1/assets/{a['id']}/preview", ok=(200, 409, 422))
            eng.send("POST", f"/v1/assets/{a['id']}/reveal", ok=(200, 409))
        a = images[0]["id"]
        eng.send("PATCH", f"/v1/assets/{a}", json={"title": "Front porch", "is_favorite": True, "is_reviewed": True})
        tag = eng.send("POST", f"/v1/assets/{a}/tags", json={"names": ["porch"]})
        note = eng.send("POST", f"/v1/assets/{a}/notes", json={"body": "check the railing"})
        eng.send("PATCH", f"/v1/assets/{a}/notes/{note['id']}", json={"body": "railing is 1.1 m"})
        coll = eng.send("POST", "/v1/collections", json={"name": "Site visit"})
        ids = [x["id"] for x in images[:10]]
        eng.send("POST", f"/v1/collections/{coll['id']}/assets", json={"ids": ids})
        eng.send("POST", "/v1/assets:bulk", json={"ids": ids, "add_tags": ["visit"], "set_favorite": True})
        eng.send("POST", f"/v1/assets/{a}/hide")
        eng.send("POST", f"/v1/assets/{a}/unhide")
        eng.send("POST", "/v1/assets:bulk", json={"ids": ids, "hide": True})
        eng.send("POST", "/v1/assets:bulk", json={"ids": ids, "hide": False})
        porch = (
            next(t for t in tag["tags"] if t["name"] == "porch") if isinstance(tag, dict) and "tags" in tag else None
        )
        if porch:
            eng.send("DELETE", f"/v1/assets/{a}/tags/{porch['id']}")
        eng.send("DELETE", f"/v1/assets/{a}/notes/{note['id']}")

        # 3. Something else adds a file to the folder: the watcher picks it up, read-only.
        set_read_only(src, False)
        Image.new("RGB", (800, 600), (12, 200, 90)).save(src / "sub" / "added later.jpg", quality=90)
        set_read_only(src, True)
        before = snapshot(src)  # the test's own change is the new baseline
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not any(x["file_name"] == "added later.jpg" for x in all_assets(eng)):
            time.sleep(0.5)
        assert any(x["file_name"] == "added later.jpg" for x in all_assets(eng)), "watcher did not see the new file"
        eng.idle()

        # 4. A full rescan (the reconcile path), then removing the source.
        eng.send("POST", f"/v1/sources/{source_id}/scan")
        eng.idle()
        failed = eng.get("/v1/jobs", status="failed", limit=500)
        dead = eng.get("/v1/jobs", status="dead", limit=500)
        problems = [
            j
            for j in [*failed["items"], *dead["items"]]
            if any(w in (j["last_error"] or "").lower() for w in PERMISSION_WORDS)
        ]
        assert problems == [], f"a stage tried to write: {problems}"
        preview = eng.get(f"/v1/sources/{source_id}/removal")
        eng.send("DELETE", f"/v1/sources/{source_id}", params={"confirm": preview["assets_only_here"]})
    finally:
        eng.stop()

    writes = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert writes == [], f"the engine tried to change the source folder: {writes[:20]}"
    assert snapshot(src) == before


def test_the_audit_hook_catches_a_write(world):
    """The guard itself works: a deliberate write inside the source is recorded."""
    src, _library, log = world
    set_read_only(src, False)
    code = f"open({str(src / 'evil.txt')!r}, 'w').close(); import os; os.utime({str(src)!r})"
    env = {
        **os.environ,
        "IV_AUDIT_ROOTS": str(src),
        "IV_AUDIT_LOG": str(log),
        "PYTHONPATH": str(AUDIT_DIR),
    }
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
    events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert events == ["open", "os.utime"]


@pytest.mark.parametrize(
    "args",
    [
        ["-Title=x", "a.jpg"],
        ["-overwrite_original", "a.jpg"],
        ["-o", "out.jpg", "a.jpg"],
        ["-w", "%d%f.txt", "a.jpg"],
        ["-TagsFromFile", "b.jpg", "a.jpg"],
        ["-geotag", "track.gpx", "a.jpg"],
        ["-delete_original!", "a.jpg"],
        ["-Comment<Title", "a.jpg"],
    ],
)
def test_exiftool_refuses_writing_arguments(args):
    with pytest.raises(ExifToolError):
        assert_read_only(args)


def test_exiftool_allows_the_reads_the_engine_uses():
    assert_read_only(["-j", "-n", "-G", "-charset", "filename=utf8", "-api", "LargeFileSupport=1", "a.jpg"])
    assert_read_only(["-b", "-PreviewImage", "-charset", "filename=utf8", "a.cr2"])
    assert_read_only(["-b", "-JpgFromRaw", "a.nef"])
