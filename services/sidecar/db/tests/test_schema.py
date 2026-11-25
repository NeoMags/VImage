"""Schema checks + vector search benchmark for the local library DB.

Run:  python -I services/sidecar/db/tests/test_schema.py [n_assets]
Needs: Python sqlite3 with FTS5 and extension loading, `pip install sqlite-vec numpy`.
"""
import os, sqlite3, sys, time, uuid, hashlib

import numpy as np
import sqlite_vec

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATION = os.path.join(HERE, "..", "migrations", "0001_initial.sql")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
results = []


def check(name, ok):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def expect_error(db, sql, params=()):
    try:
        db.execute(sql, params)
        return False
    except sqlite3.DatabaseError:
        return True


db = sqlite3.connect(":memory:")
db.enable_load_extension(True)
sqlite_vec.load(db)
db.enable_load_extension(False)
db.executescript(open(MIGRATION, encoding="utf-8").read())
db.execute("PRAGMA foreign_keys = ON")
check("migration applies, user_version=1", db.execute("PRAGMA user_version").fetchone()[0] == 1)

db.execute("INSERT INTO sources (uid, kind, display_name, root_path) VALUES (?, 'local_folder', 'Pictures', 'C:/Users/me/Pictures')",
           (str(uuid.uuid4()),))

# --- bulk insert N assets with random CLIP vectors and pHashes
rng = np.random.default_rng(42)
vecs = rng.standard_normal((N, 512), dtype=np.float32)
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
phashes = rng.integers(0, 256, size=(N, 8), dtype=np.uint8)
t = time.perf_counter()
with db:
    for i in range(N):
        sha = hashlib.sha256(i.to_bytes(8, "little")).digest()
        aid = db.execute(
            "INSERT INTO assets (uid, sha256, phash, mime_type, byte_size, taken_at) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), sha, phashes[i].tobytes(), "image/jpeg", 1000, f"2026-01-01T00:00:{i % 60:02d}Z"),
        ).lastrowid
        eid = db.execute("INSERT INTO embeddings (asset_id, model_id) VALUES (?, 1)", (aid,)).lastrowid
        db.execute("INSERT INTO vec_clip_b32 (rowid, embedding) VALUES (?, ?)", (eid, vecs[i].tobytes()))
        db.execute("INSERT INTO vec_phash (rowid, phash) VALUES (?, vec_bit(?))", (aid, phashes[i].tobytes()))
        db.execute("INSERT INTO file_locations (source_id, asset_id, file_key, rel_path, size_bytes, mtime) VALUES (1,?,?,?,1000,'2026-01-01')",
                   (aid, f"vol1:{i}", f"img/{i}.jpg"))
print(f"info  inserted {N} assets + vectors in {time.perf_counter() - t:.1f}s")

# --- constraints
check("duplicate sha256 rejected", expect_error(db,
      "INSERT INTO assets (uid, sha256, mime_type, byte_size) VALUES ('x', ?, 'image/png', 1)",
      (hashlib.sha256((0).to_bytes(8, "little")).digest(),)))
check("same file_key twice in a source rejected", expect_error(db,
      "INSERT INTO file_locations (source_id, asset_id, file_key, rel_path, size_bytes, mtime) VALUES (1,1,'vol1:0','a',1,'x')"))
db.execute("INSERT INTO regions (asset_id, kind, x, y, w, h, origin) VALUES (1,'face',0.1,0.1,0.2,0.2,'ai')")
db.execute("INSERT INTO people (display_name) VALUES ('Test Person')")
check("face identity without confirmation rejected", expect_error(db,
      "INSERT INTO asset_faces (asset_id, region_id, person_id, model_id) VALUES (1,1,1,1)"))
check("out-of-range region rejected", expect_error(db,
      "INSERT INTO regions (asset_id, kind, x, y, w, h, origin) VALUES (1,'object',0.9,0.1,0.5,0.2,'ai')"))
db.execute("INSERT INTO audit_log (action, entity) VALUES ('asset.hide','asset')")
check("audit_log update blocked", expect_error(db, "UPDATE audit_log SET action='x'"))
check("audit_log delete blocked", expect_error(db, "DELETE FROM audit_log"))
check("duplicate decision 'delete' not allowed", expect_error(db,
      "INSERT INTO duplicate_groups (level) VALUES ('exact')") is False and expect_error(db,
      "INSERT INTO duplicate_group_members (group_id, asset_id, similarity, decision) VALUES (1,1,1.0,'delete')"))

# --- FTS
db.execute("INSERT INTO asset_fts (rowid, filename, ocr_text) VALUES (5, 'Screenshot 2026.png', 'psql: error: connection to server at localhost port 5432 refused')")
db.execute("INSERT INTO ocr_trigram (rowid, ocr_text) VALUES (5, 'Call +91 98220 12345 for details')")
check("FTS word match", db.execute("SELECT rowid FROM asset_fts WHERE asset_fts MATCH 'connection AND refused'").fetchall() == [(5,)])
check("trigram substring match (partial phone)", db.execute("SELECT rowid FROM ocr_trigram WHERE ocr_text MATCH '\"98220 123\"'").fetchall() == [(5,)])

# --- cascade cleans vectors
db.execute("DELETE FROM assets WHERE id = 2")
check("deleting asset removes its vector + phash rows",
      db.execute("SELECT count(*) FROM vec_clip_b32 WHERE rowid = 2").fetchone()[0] == 0
      and db.execute("SELECT count(*) FROM vec_phash WHERE rowid = 2").fetchone()[0] == 0)

# --- KNN benchmark (sqlite-vec 0.1.x is brute force; this measures real cost)
q = vecs[10]
def knn():
    return db.execute("SELECT rowid, distance FROM vec_clip_b32 WHERE embedding MATCH ? AND k = 20 ORDER BY distance",
                      (q.tobytes(),)).fetchall()
knn()
times = []
for _ in range(10):
    t = time.perf_counter(); rows = knn(); times.append((time.perf_counter() - t) * 1000)
check("CLIP KNN returns query vector first", rows[0][0] == 11 and rows[0][1] < 1e-4)  # embeddings.id 11 = asset 11
print(f"info  CLIP 512-d KNN k=20 over {N}: median {sorted(times)[5]:.0f} ms, max {max(times):.0f} ms")

qh = phashes[100].tobytes()
times = []
for _ in range(10):
    t = time.perf_counter()
    rows = db.execute("SELECT rowid, distance FROM vec_phash WHERE phash MATCH vec_bit(?) AND k = 10 ORDER BY distance", (qh,)).fetchall()
    times.append((time.perf_counter() - t) * 1000)
check("pHash Hamming KNN finds exact match at distance 0", rows[0] == (101, 0.0))
print(f"info  pHash 64-bit Hamming KNN k=10 over {N}: median {sorted(times)[5]:.0f} ms")

t = time.perf_counter()
page = db.execute("SELECT id FROM assets WHERE is_hidden = 0 ORDER BY taken_at DESC, id DESC LIMIT 60").fetchall()
print(f"info  gallery first page (60) over {N}: {(time.perf_counter() - t) * 1000:.1f} ms")

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
