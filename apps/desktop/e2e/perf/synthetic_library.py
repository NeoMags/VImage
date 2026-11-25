"""Builds a synthetic library of N assets for the gallery performance run (B12).

Rows only: no source files exist, and the source is paused so the engine never scans or watches it.
Thumbnails are 48 real small WebP files shared between assets (the browser still decodes each <img>).
Usage (from services/sidecar): uv run python <this file> <library_dir> [count]
"""

import random
import sys
from pathlib import Path

from PIL import Image

from ivsidecar import db

library = Path(sys.argv[1])
count = int(sys.argv[2]) if len(sys.argv) > 2 else 100_000
conn = db.open_library(library)
thumbs = library / "thumbs" / "perf"
thumbs.mkdir(parents=True, exist_ok=True)
for i in range(48):
    Image.new("RGB", (256, 192), ((i * 37) % 256, (i * 91) % 256, (i * 53) % 256)).save(thumbs / f"{i}.webp", quality=70)

rng = random.Random(7)
conn.execute(
    "INSERT INTO sources (uid, kind, display_name, root_path, status) "
    "VALUES ('perf', 'local_folder', 'Synthetic', 'C:/synthetic', 'paused')"
)
conn.execute("BEGIN")
assets, locs, ders = [], [], []
for i in range(1, count + 1):
    # ten years, denser recently (like a phone library)
    days = int(3650 * rng.random() ** 1.6)
    y, rem = 2026 - days // 365, days % 365
    month, day = 12 - rem // 31 % 12, 1 + rem % 28
    taken = f"{y}-{month:02d}-{day:02d}T{rng.randrange(24):02d}:{rng.randrange(60):02d}:00"
    kind = "screenshot" if i % 9 == 0 else "image"
    assets.append((i, f"perf{i}", i.to_bytes(32, "big"), kind, 50_000 + rng.randrange(8_000_000), taken))
    locs.append((i, f"k{i}", f"{y}/{month:02d}/IMG_{i:06d}.jpg"))
    ders.append((i, f"thumbs/perf/{i % 48}.webp"))
conn.executemany(
    "INSERT INTO assets (id, uid, sha256, kind, status, mime_type, byte_size, width, height, taken_at, "
    "taken_at_origin, file_modified_at) VALUES (?, ?, ?, ?, 'ready', 'image/jpeg', ?, 4000, 3000, ?, 'source', NULL)",
    assets,
)
conn.executemany(
    "INSERT INTO file_locations (source_id, asset_id, file_key, rel_path, size_bytes, mtime) VALUES (1, ?, ?, ?, 1, '1')",
    locs,
)
conn.executemany(
    "INSERT INTO derivatives (asset_id, kind, rel_path, width, height, byte_size) VALUES (?, 'thumb_256', ?, 256, 192, 1)",
    ders,
)
conn.execute("COMMIT")
conn.execute("PRAGMA optimize")
conn.close()
print(f"{count} assets in {library}")
