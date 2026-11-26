"""Knowledge Cards (K5): a short, editable summary of what a picture with text is about.

Made by rules from the text, its entities and the picture type (no language model yet; that is D5):
the title is the first line that reads like a heading, the summary says what kind of picture it is
and what was found in it. A card is "AI-generated" until you edit it; after that it is yours and
is never regenerated. Cards exist only for photos with text.
"""

from __future__ import annotations

import re
import sqlite3

from .entities import get_type, list_entities
from .library import Invalid, NotFound, _exists, audit

PROMPT_VERSION = "rules-1"
TYPE_NAME = {"screenshot": "Screenshot", "scan": "Scanned document", "photo": "Photo"}
ENTITY_NAME = {
    "url": ("link", "links"),
    "email": ("email address", "email addresses"),
    "phone": ("phone number", "phone numbers"),
    "date": ("date", "dates"),
    "amount": ("amount", "amounts"),
    "command": ("command", "commands"),
    "error_message": ("error message", "error messages"),
}
MD_LABEL = {
    "url": "Link",
    "email": "Email",
    "phone": "Phone",
    "date": "Date",
    "amount": "Amount",
    "command": "Command",
    "error_message": "Error",
}
_JUNK = re.compile(r"^[\W\d_]+$|^\d{1,2}[:.]\d{2}(\s?[AP]M)?$", re.IGNORECASE)  # "12:45", "100%", "•••"


def _title(lines: list[str], fallback: str) -> str:
    """The first line that looks like a heading: 3+ letters, not a clock or a status-bar icon row."""
    for ln in lines[:15]:
        t = ln.strip()
        if len(re.findall(r"[^\W\d_]", t)) >= 3 and not _JUNK.match(t) and len(t) <= 120:
            return t
    return fallback


def _summary(kind: str | None, n_lines: int, entities: list[dict]) -> str:
    what = TYPE_NAME.get(kind or "", "Picture")
    counts: dict[str, int] = {}
    for e in entities:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    found = [f"{n} {ENTITY_NAME[k][0] if n == 1 else ENTITY_NAME[k][1]}" for k, n in counts.items() if k in ENTITY_NAME]
    s = f"{what} with {n_lines} line{'s' if n_lines != 1 else ''} of text"
    if "error_message" in counts:
        s = f"{what} of an error" + s[len(what) :]
    return s + (f"; contains {', '.join(found)}." if found else ".")


def refresh_card(conn: sqlite3.Connection, asset_id: int) -> str | None:
    """Make or update the AI card from the current text. A card you edited is left alone.
    Returns 'made', 'updated', 'removed', 'kept' (yours) or None (no text, no card)."""
    card = conn.execute("SELECT id, status FROM knowledge_cards WHERE asset_id = ?", (asset_id,)).fetchone()
    if card and card[1] == "user_reviewed":
        _link_entities(conn, card[0], asset_id)
        return "kept"
    text = conn.execute(
        "SELECT full_text FROM extracted_text WHERE asset_id = ? AND is_current = 1", (asset_id,)
    ).fetchone()
    lines = [ln for ln in (text[0] if text else "").splitlines() if ln.strip()]
    if not lines:
        if card:
            conn.execute("DELETE FROM knowledge_cards WHERE id = ?", (card[0],))
            _set_fts(conn, asset_id, None)
            return "removed"
        return None
    name = conn.execute(
        "SELECT coalesce(a.title, (SELECT rel_path FROM file_locations WHERE asset_id = a.id ORDER BY id LIMIT 1)) "
        "FROM assets a WHERE a.id = ?",
        (asset_id,),
    ).fetchone()[0]
    fallback = re.split(r"[\\/]", name or f"Photo {asset_id}")[-1]
    ents = list_entities(conn, asset_id)
    kind = get_type(conn, asset_id)
    title, summary = _title(lines, fallback), _summary(kind["value"] if kind else None, len(lines), ents)
    cat = conn.execute(
        "SELECT x.category_id FROM asset_categories x JOIN categories c ON c.id = x.category_id "
        "WHERE x.asset_id = ? AND c.domain = 'source_type' AND x.rejected = 0",
        (asset_id,),
    ).fetchone()
    if card:
        conn.execute(
            "UPDATE knowledge_cards SET title = ?, short_description = ?, category_id = ?, prompt_version = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
            (title, summary, cat[0] if cat else None, PROMPT_VERSION, card[0]),
        )
        cid, out = card[0], "updated"
    else:
        cid = conn.execute(
            "INSERT INTO knowledge_cards (asset_id, title, short_description, category_id, model_name, prompt_version) "
            "VALUES (?, ?, ?, ?, 'rules', ?) RETURNING id",
            (asset_id, title, summary, cat[0] if cat else None, PROMPT_VERSION),
        ).fetchone()[0]
        out = "made"
    _link_entities(conn, cid, asset_id)
    _set_fts(conn, asset_id, f"{title}\n{summary}")
    return out


def _link_entities(conn: sqlite3.Connection, card_id: int, asset_id: int) -> None:
    conn.execute("DELETE FROM knowledge_card_entities WHERE card_id = ?", (card_id,))
    conn.execute(
        "INSERT INTO knowledge_card_entities (card_id, entity_id) SELECT ?, id FROM entities WHERE asset_id = ?",
        (card_id, asset_id),
    )


def _set_fts(conn: sqlite3.Connection, asset_id: int, card: str | None) -> None:
    from .handlers import refresh_fts

    if conn.execute("SELECT 1 FROM asset_fts WHERE rowid = ?", (asset_id,)).fetchone() is None:
        refresh_fts(conn, [asset_id])
    conn.execute("UPDATE asset_fts SET card = ? WHERE rowid = ?", (card, asset_id))


def get_card(conn: sqlite3.Connection, asset_id: int) -> dict:
    _exists(conn, "assets", asset_id, "photo")
    row = conn.execute(
        "SELECT title, short_description, status, created_at, updated_at FROM knowledge_cards WHERE asset_id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        return {"card": None}
    return {
        "card": {
            "title": row[0],
            "summary": row[1] or "",
            "edited": row[2] == "user_reviewed",
            "created_at": row[3],
            "updated_at": row[4],
        }
    }


def edit_card(conn: sqlite3.Connection, asset_id: int, title: str | None, summary: str | None) -> None:
    """Your version of the card; from now on it is never regenerated."""
    _exists(conn, "assets", asset_id, "photo")
    row = conn.execute(
        "SELECT id, title, short_description FROM knowledge_cards WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if row is None:
        raise NotFound("this photo has no card (no text was found)")
    new_title = row[1] if title is None else title.strip()
    new_summary = row[2] if summary is None else summary.strip()
    if not new_title:
        raise Invalid("the title cannot be empty")
    conn.execute(
        "UPDATE knowledge_cards SET title = ?, short_description = ?, status = 'user_reviewed', "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
        (new_title, new_summary, row[0]),
    )
    _set_fts(conn, asset_id, f"{new_title}\n{new_summary}")
    audit(conn, "card.edit", "asset", asset_id, {"title": row[1]}, {"title": new_title})


def reset_card(conn: sqlite3.Connection, asset_id: int) -> None:
    """Drop your edits: the card is made from the text again and follows it from now on."""
    _exists(conn, "assets", asset_id, "photo")
    n = conn.execute(
        "UPDATE knowledge_cards SET status = 'ai_generated' WHERE asset_id = ? AND status = 'user_reviewed'",
        (asset_id,),
    ).rowcount
    if not n:
        raise NotFound("this card has not been edited")
    refresh_card(conn, asset_id)
    audit(conn, "card.reset", "asset", asset_id)


def _md_escape(s: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", s)


def markdown(conn: sqlite3.Connection, asset_id: int) -> dict:
    """The card as a Markdown file: title, summary, details, found items and the full text."""
    from .text import get_text

    card = get_card(conn, asset_id)["card"]
    if card is None:
        raise NotFound("this photo has no card (no text was found)")
    t = get_text(conn, asset_id)
    row = conn.execute(
        "SELECT a.sort_date, (SELECT s.root_path || '/' || l.rel_path FROM file_locations l JOIN sources s "
        "ON s.id = l.source_id WHERE l.asset_id = a.id ORDER BY l.state = 'present' DESC, l.id LIMIT 1) "
        "FROM assets a WHERE a.id = ?",
        (asset_id,),
    ).fetchone()
    lines = [f"# {_md_escape(card['title'])}", ""]
    if card["summary"]:
        lines += [_md_escape(card["summary"]), ""]
    lines.append(f"- Type: {TYPE_NAME[t['type']['value']] if t['type'] else 'Unknown'}")
    if row[0]:
        lines.append(f"- Date: {row[0][:10]}")
    if row[1]:
        lines.append(f"- File: `{row[1]}`")
    lines.append(f"- Card: {'edited by you' if card['edited'] else 'AI-generated'}")
    if t["entities"]:
        lines += ["", "## Found in the text", ""]
        for e in t["entities"]:
            label = MD_LABEL.get(e["type"], e["type"])
            value = f"<{e['normalized']}>" if e["type"] == "url" else f"`{e['value']}`"
            lines.append(f"- {label}: {value}")
    lines += ["", "## Text", "", "```text", t["text"].replace("```", "ʼʼʼ"), "```", ""]
    safe = re.sub(r"[^\w\- ]+", "", card["title"]).strip()[:60] or f"photo-{asset_id}"
    return {"file_name": f"{safe}.md", "markdown": "\n".join(lines)}
