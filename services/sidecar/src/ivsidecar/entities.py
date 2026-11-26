"""Things found in a photo's text, and what kind of picture it is (K3). Rule-based, offline.

Entities (links, emails, phone numbers, dates, amounts, commands, error messages) are found line
by line, so each one points at the OCR box it came from. Your edited text is searched the same way;
its lines point at a box when an OCR line has the same text.

The picture type (screenshot / scan / photo) uses the file name, the camera fields, common screen
sizes and how much of the picture is text. A type you set yourself is never changed.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

# ------------------------------------------------------------------------------- entities
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
URL = re.compile(
    r"(?<![\w@/.-])(?:https?://[^\s<>\"'`]+|www\.[^\s<>\"'`]+|"
    r"(?:[A-Za-z0-9-]+\.)+(?:com|org|net|io|dev|ai|app|co|in|uk|de|gov|edu|info|me|tv|ly|gg|xyz)"
    r"(?:/[^\s<>\"'`]*)?)(?![\w@])",
    re.IGNORECASE,
)
_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATES = [
    re.compile(r"\b(?P<y>(?:19|20)\d\d)-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b"),
    re.compile(r"\b(?P<a>\d{1,2})[/.](?P<b>\d{1,2})[/.](?P<y>(?:19|20)\d\d)\b"),
    re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+(?P<y>(?:19|20)\d\d)\b", re.IGNORECASE),
    re.compile(rf"\b{_MON}\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>(?:19|20)\d\d)\b", re.IGNORECASE),
]
_CUR = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY", "RS": "INR", "RS.": "INR"}
_NUM = r"\d{1,3}(?:[,  ]\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
AMOUNTS = [
    re.compile(rf"(?P<cur>[$€£₹¥]|\b(?:USD|EUR|GBP|INR|JPY|Rs\.?))\s?(?P<num>{_NUM})(?!\d)", re.IGNORECASE),
    re.compile(rf"(?<![\w.])(?P<num>{_NUM})\s?(?P<cur>USD|EUR|GBP|INR|JPY|€|₹)(?!\w)", re.IGNORECASE),
]
PHONE = re.compile(
    r"(?<![\w.:/-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,5}\)[\s.-]?)?\d{2,5}(?:[\s.-]\d{2,5}){1,4}(?![\w.:/-])"
)
_CMD_WORDS = (
    "git npm npx pnpm yarn pip pip3 python python3 py node docker kubectl sudo apt apt-get brew curl wget ssh scp "
    "cd ls dir mkdir rm cp mv cat chmod chown winget choco powershell pwsh cmd uv cargo go java dotnet make "
    "gcc ffmpeg exiftool netsh ipconfig ping systemctl"
).split()
COMMAND = re.compile(
    r"^\s*(?:(?:\$|#|>|PS [^>]{0,80}>|[A-Za-z]:\\[^>]{0,120}>)\s*(?P<a>\S.*)|(?P<b>(?:"
    + "|".join(re.escape(w) for w in _CMD_WORDS)
    + r")\s+-{0,2}[\w./:@=-].*))$"
)
ERROR = re.compile(
    r"\b(?:error|exception|traceback|fatal|failed|failure|denied|refused|not found|errno|segmentation fault|"
    r"stack trace|unhandled|invalid|cannot|can't|unable to)\b|\b0x[0-9a-f]{8}\b|"
    r"\b[45]\d\d\s+(?:bad request|unauthorized|forbidden|not found|internal server error|bad gateway|"
    r"service unavailable|gateway timeout)\b",
    re.IGNORECASE,
)


def _date(m: re.Match) -> str | None:
    g = m.groupdict()
    try:
        y = int(g["y"])
        if g.get("mon"):
            return date(y, _MONTHS[g["mon"].lower().rstrip(".")], int(g["d"])).isoformat()
        if g.get("a"):
            a, b = int(g["a"]), int(g["b"])
            # day first unless that cannot be a date (13/05 vs 05/13): the common order outside the US
            d, mo = (b, a) if b > 12 >= a else (a, b)
            return date(y, mo, d).isoformat()
        return date(y, int(g["m"]), int(g["d"])).isoformat()
    except (ValueError, KeyError):
        return None


def _amount(cur: str, num: str) -> str:
    code = _CUR.get(cur.upper(), _CUR.get(cur, cur.upper()))
    value = re.sub(r"[,  ]", "", num)
    return f"{code} {value}"


def extract(line: str) -> list[tuple[str, str, str]]:
    """(type, value as written, normalized value) found in one line of text."""
    out: list[tuple[str, str, str]] = []
    taken: list[tuple[int, int]] = []

    def free(m: re.Match) -> bool:
        return all(m.end() <= s or m.start() >= e for s, e in taken)

    found: list[tuple[int, str, str, str]] = []

    def add(kind: str, m: re.Match, value: str, norm: str) -> None:
        taken.append(m.span())
        found.append((m.start(), kind, value, norm))

    cmd = COMMAND.match(line)
    if cmd:
        c = (cmd.group("a") or cmd.group("b")).strip()
        out.append(("command", c, re.sub(r"\s+", " ", c)))
    for m in EMAIL.finditer(line):
        add("email", m, m.group(0), m.group(0).lower())
    for m in URL.finditer(line):
        if free(m):
            u = m.group(0).rstrip(".,;:!?)]}'\"")
            norm = u if re.match(r"https?://", u, re.I) else f"https://{u}"
            scheme, _, rest = norm.partition("://")
            host, slash, path = rest.partition("/")
            add("url", m, u, f"{scheme.lower()}://{host.lower()}{slash}{path}")
    for rx in DATES:
        for m in rx.finditer(line):
            iso = _date(m)
            if iso and free(m):
                add("date", m, m.group(0), iso)
    for rx in AMOUNTS:
        for m in rx.finditer(line):
            if free(m):
                add("amount", m, m.group(0).strip(), _amount(m.group("cur"), m.group("num")))
    for m in PHONE.finditer(line):
        digits = re.sub(r"\D", "", m.group(0))
        if 8 <= len(digits) <= 15 and free(m) and not re.fullmatch(r"\d{1,2}[.-]\d{1,2}[.-]\d{2,4}", m.group(0)):
            add("phone", m, m.group(0).strip(), ("+" if m.group(0).lstrip().startswith("+") else "") + digits)
    out += [(k, v, n) for _, k, v, n in sorted(found, key=lambda f: f[0])]  # in reading order
    if not cmd and ERROR.search(line):
        out.append(("error_message", line.strip(), line.strip().lower()))
    return out


def refresh_entities(conn: sqlite3.Connection, asset_id: int) -> int:
    """Find the entities in the photo's current text again. Entities you added stay."""
    conn.execute("DELETE FROM entities WHERE asset_id = ? AND origin = 'ai'", (asset_id,))
    cur = conn.execute(
        "SELECT id, origin, full_text FROM extracted_text WHERE asset_id = ? AND is_current = 1", (asset_id,)
    ).fetchone()
    if cur is None:
        return 0
    regions = conn.execute(
        "SELECT r.id, r.text FROM text_regions r JOIN extracted_text e ON e.id = r.extracted_text_id "
        "WHERE e.asset_id = ? AND e.origin = 'ai' ORDER BY e.id DESC, r.line_no",
        (asset_id,),
    ).fetchall()
    if cur[1] == "ai":
        lines = [(rid, t) for rid, t in regions] or [(None, ln) for ln in cur[2].splitlines()]
    else:  # your text: a line points at the OCR box with the same text, if there is one
        by_text = {}
        for rid, t in regions:
            by_text.setdefault(t.strip(), rid)
        lines = [(by_text.get(ln.strip()), ln) for ln in cur[2].splitlines()]
    seen: set[tuple[str, str]] = set()
    rows = []
    for rid, text in lines:
        for kind, value, norm in extract(text):
            if (kind, norm) in seen:
                continue
            seen.add((kind, norm))
            rows.append((asset_id, rid, kind, value[:2000], norm[:2000]))
    conn.executemany(
        "INSERT INTO entities (asset_id, text_region_id, entity_type, value, normalized_value, origin) "
        "VALUES (?, ?, ?, ?, ?, 'ai')",
        rows,
    )
    return len(rows)


def list_entities(conn: sqlite3.Connection, asset_id: int) -> list[dict]:
    return [
        {"id": i, "type": k, "value": v, "normalized": n, "line_no": ln, "origin": o}
        for i, k, v, n, ln, o in conn.execute(
            "SELECT e.id, e.entity_type, e.value, e.normalized_value, r.line_no, e.origin FROM entities e "
            "LEFT JOIN text_regions r ON r.id = e.text_region_id WHERE e.asset_id = ? ORDER BY e.id",
            (asset_id,),
        )
    ]


# ------------------------------------------------------------------------------- picture type
TYPES = ("screenshot", "scan", "photo")
# Common screen sizes (phones, tablets, laptops, monitors), either orientation.
SCREEN_SIZES = {
    (w, h)
    for w, h in (
        (750, 1334), (828, 1792), (1080, 1920), (1080, 2160), (1080, 2220), (1080, 2280), (1080, 2340),
        (1080, 2400), (1080, 2412), (1170, 2532), (1179, 2556), (1242, 2208), (1242, 2688), (1284, 2778),
        (1290, 2796), (1440, 2560), (1440, 3040), (1440, 3200), (1536, 2048), (1620, 2160), (1640, 2360),
        (1668, 2388), (2048, 2732), (1280, 720), (1280, 800), (1366, 768), (1440, 900), (1536, 864),
        (1600, 900), (1680, 1050), (1920, 1080), (1920, 1200), (2560, 1440), (2560, 1600), (2880, 1800),
        (3024, 1964), (3456, 2234), (3840, 2160), (2256, 1504), (2736, 1824), (2880, 1920),
    )
    for (w, h) in ((w, h), (h, w))
}  # fmt: skip
SHOT_NAME = re.compile(r"screen ?shot|screenshot|screen_shot|screen[ _-]?capture|capture|snip|bildschirmfoto|captura")
SCAN_NAME = re.compile(r"(?<![a-z])(scan|scanned|scn)(?![a-z])|scan_|_scan")


def detect_type(conn: sqlite3.Connection, asset_id: int) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT a.mime_type, a.width, a.height, m.camera_make, m.camera_model, "
        "(SELECT group_concat(lower(rel_path), ' | ') FROM file_locations WHERE asset_id = a.id) "
        "FROM assets a LEFT JOIN asset_metadata m ON m.asset_id = a.id WHERE a.id = ? AND a.kind = 'image'",
        (asset_id,),
    ).fetchone()
    if row is None:
        return None
    mime, w, h, make, model, names = row
    names = names or ""
    camera = " ".join(x for x in (make, model) if x).lower()
    lines, cover = conn.execute(
        "SELECT count(r.id), coalesce(sum(r.w * r.h), 0) FROM extracted_text e JOIN text_regions r "
        "ON r.extracted_text_id = e.id WHERE e.asset_id = ? AND e.origin = 'ai'",
        (asset_id,),
    ).fetchone()
    if SHOT_NAME.search(names):
        return "screenshot", 0.95
    if SCAN_NAME.search(names) or re.search(r"scan|scansnap|fi-\d|ads-\d|doc", camera):
        return "scan", 0.9
    if camera:
        return "photo", 0.9
    if w and h and (w, h) in SCREEN_SIZES:
        return "screenshot", 0.8
    if mime == "image/png" and lines:
        return "screenshot", 0.7
    if lines >= 3 and cover >= 0.05:
        if w and h and 1.25 <= h / w <= 1.5:
            return "scan", 0.6
        return "screenshot", 0.6
    if mime in ("image/jpeg", "image/heic", "image/heif", "image/avif") or mime.startswith("image/x-"):
        return "photo", 0.5
    return None


def classify(conn: sqlite3.Connection, asset_id: int) -> str | None:
    """Store the picture type as an AI category; a type you chose (or rejected) is left alone."""
    yours = conn.execute(
        "SELECT 1 FROM asset_categories x JOIN categories c ON c.id = x.category_id WHERE x.asset_id = ? "
        "AND c.domain = 'source_type' AND (x.origin = 'user' OR x.rejected = 1)",
        (asset_id,),
    ).fetchone()
    if yours:
        return None
    conn.execute(
        "DELETE FROM asset_categories WHERE asset_id = ? AND origin = 'ai' AND category_id IN "
        "(SELECT id FROM categories WHERE domain = 'source_type')",
        (asset_id,),
    )
    found = detect_type(conn, asset_id)
    if found is None:
        return None
    slug, conf = found
    conn.execute(
        "INSERT INTO asset_categories (asset_id, category_id, origin, confidence) "
        "SELECT ?, id, 'ai', ? FROM categories WHERE domain = 'source_type' AND slug = ?",
        (asset_id, conf, slug),
    )
    return slug


def set_type(conn: sqlite3.Connection, asset_id: int, slug: str | None) -> None:
    """Your choice of picture type (None: let ImageVault decide again)."""
    from .library import Invalid, _exists, audit

    _exists(conn, "assets", asset_id, "photo")
    if slug is not None and slug not in TYPES:
        raise Invalid(f"type must be one of {', '.join(TYPES)}")
    conn.execute(
        "DELETE FROM asset_categories WHERE asset_id = ? AND category_id IN "
        "(SELECT id FROM categories WHERE domain = 'source_type')",
        (asset_id,),
    )
    if slug is None:
        classify(conn, asset_id)
    else:
        conn.execute(
            "INSERT INTO asset_categories (asset_id, category_id, origin) "
            "SELECT ?, id, 'user' FROM categories WHERE domain = 'source_type' AND slug = ?",
            (asset_id, slug),
        )
    audit(conn, "type.set", "asset", asset_id, None, {"type": slug})


def get_type(conn: sqlite3.Connection, asset_id: int) -> dict | None:
    row = conn.execute(
        "SELECT c.slug, x.origin, x.confidence FROM asset_categories x JOIN categories c ON c.id = x.category_id "
        "WHERE x.asset_id = ? AND c.domain = 'source_type' AND x.rejected = 0",
        (asset_id,),
    ).fetchone()
    return {"value": row[0], "origin": row[1], "confidence": row[2]} if row else None
