"""Fachlogik des Band Managers – wird von REST-API und MCP-Server gemeinsam genutzt.

Alle Funktionen bekommen eine offene Verbindung (`conn`) und geben plain dicts
zurück, wie in API.md beschrieben. Fehler → `NotFound` / `Invalid`.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from .db import COUNTING_STATUS, FLAGS, GIG_STATUS, ITEM_KINDS


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


# --------------------------------------------------------------------------- #
# Hilfen
# --------------------------------------------------------------------------- #


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _check_flag(v: str, field: str) -> str:
    if v not in FLAGS:
        raise Invalid(f"{field} muss einer von {FLAGS} sein, nicht {v!r}")
    return v


def _check_status(v: str) -> str:
    if v not in GIG_STATUS:
        raise Invalid(f"status muss einer von {GIG_STATUS} sein, nicht {v!r}")
    return v


def _check_date(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError as exc:
        raise Invalid(f"date muss yyyy-mm-dd sein: {v!r}") from exc


def _int(v: Any, field: str) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError) as exc:
        raise Invalid(f"{field} muss eine Zahl sein: {v!r}") from exc


def _touch_gig(conn: sqlite3.Connection, gig_id: int) -> None:
    conn.execute("UPDATE gigs SET updated_at = datetime('now','localtime') WHERE id = ?", (gig_id,))


def _gig_id_of_variant(conn: sqlite3.Connection, variant_id: int) -> int:
    r = conn.execute("SELECT gig_id FROM variants WHERE id = ?", (variant_id,)).fetchone()
    if r is None:
        raise NotFound(f"Variante {variant_id} nicht gefunden")
    return r["gig_id"]


def totals(items: list[dict], fee: int) -> dict:
    musicians = sum(i["amount"] for i in items if i["kind"] == "musician")
    costs = sum(i["amount"] for i in items if i["kind"] == "cost")
    counting = [i for i in items if i["amount"] > 0 and i["paid"] != "na"]
    return {
        "fee": fee,
        "musicians": musicians,
        "costs": costs,
        "rest": fee - musicians - costs,
        "items": len(counting),
        "info_done": sum(1 for i in counting if i["info"] == "done"),
        "invoice_done": sum(1 for i in counting if i["invoice"] == "done"),
        "paid_done": sum(1 for i in counting if i["paid"] == "done"),
    }


# --------------------------------------------------------------------------- #
# Musiker
# --------------------------------------------------------------------------- #

_MUSICIAN_SQL = """
SELECT m.*, (
    SELECT COUNT(DISTINCT v.gig_id) FROM line_items li
    JOIN variants v ON v.id = li.variant_id
    JOIN gigs g ON g.id = v.gig_id
    WHERE li.musician_id = m.id AND g.status IN ('bestaetigt','abgerechnet')
) AS gig_count
FROM musicians m
"""


def list_musicians(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    sql = _MUSICIAN_SQL + ("" if include_inactive else " WHERE m.active = 1") + " ORDER BY m.name COLLATE NOCASE"
    return [_fix_musician(dict(r)) for r in conn.execute(sql)]


def get_musician(conn: sqlite3.Connection, musician_id: int) -> dict:
    r = conn.execute(_MUSICIAN_SQL + " WHERE m.id = ?", (musician_id,)).fetchone()
    if r is None:
        raise NotFound(f"Musiker {musician_id} nicht gefunden")
    return _fix_musician(dict(r))


def find_musician(conn: sqlite3.Connection, name: str) -> dict | None:
    """Namenssuche, tolerant: exakt, sonst Präfix/Teilstring (für MCP-Aufrufe wie „Ben")."""
    name = name.strip()
    if not name:
        return None
    r = conn.execute(_MUSICIAN_SQL + " WHERE m.name = ? COLLATE NOCASE", (name,)).fetchone()
    if r is None:
        rows = conn.execute(_MUSICIAN_SQL + " WHERE m.name LIKE ? COLLATE NOCASE ORDER BY m.name", (f"%{name}%",)).fetchall()
        if len(rows) == 1:
            r = rows[0]
        elif len(rows) > 1:
            raise Invalid(f"Name {name!r} ist nicht eindeutig: {', '.join(x['name'] for x in rows)}")
    return _fix_musician(dict(r)) if r else None


def _fix_musician(m: dict) -> dict:
    m["active"] = bool(m["active"])
    m["is_self"] = bool(m.get("is_self", 0))
    return m


def self_musician_id(conn: sqlite3.Connection) -> int | None:
    r = conn.execute("SELECT id FROM musicians WHERE is_self = 1 LIMIT 1").fetchone()
    return r["id"] if r else None


def _set_self(conn: sqlite3.Connection, musician_id: int, value: bool) -> None:
    """Genau eine Person kann „ich" sein; ihre Posten brauchen keine Häkchen (alle `na`)."""
    if value:
        conn.execute("UPDATE musicians SET is_self = 0 WHERE id != ?", (musician_id,))
        conn.execute("UPDATE line_items SET info='na', invoice='na', paid='na' WHERE musician_id = ?", (musician_id,))
    conn.execute("UPDATE musicians SET is_self = ? WHERE id = ?", (1 if value else 0, musician_id))


_MUSICIAN_FIELDS = ("name", "role", "default_fee", "email", "phone", "iban", "notes", "active")


def create_musician(conn: sqlite3.Connection, data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise Invalid("name fehlt")
    if conn.execute("SELECT 1 FROM musicians WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise Invalid(f"Musiker {name!r} existiert bereits")
    cur = conn.execute(
        "INSERT INTO musicians (name, role, default_fee, email, phone, iban, notes) VALUES (?,?,?,?,?,?,?)",
        (name, (data.get("role") or "").strip(), _int(data.get("default_fee") or 0, "default_fee"),
         (data.get("email") or "").strip(), (data.get("phone") or "").strip(),
         (data.get("iban") or "").replace(" ", "").strip(), data.get("notes") or ""),
    )
    if data.get("is_self"):
        _set_self(conn, cur.lastrowid, True)
    return get_musician(conn, cur.lastrowid)


def update_musician(conn: sqlite3.Connection, musician_id: int, data: dict) -> dict:
    get_musician(conn, musician_id)
    sets, vals = [], []
    for k in _MUSICIAN_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k == "default_fee":
            v = _int(v, k)
        elif k == "active":
            v = 1 if v else 0
        elif k == "name":
            v = (v or "").strip()
            if not v:
                raise Invalid("name darf nicht leer sein")
        elif k == "iban":
            v = (v or "").replace(" ", "").strip()
        else:
            v = v or ""
        sets.append(f"{k} = ?")
        vals.append(v)
    if sets:
        conn.execute(f"UPDATE musicians SET {', '.join(sets)} WHERE id = ?", (*vals, musician_id))
    if "is_self" in data:
        _set_self(conn, musician_id, bool(data["is_self"]))
    return get_musician(conn, musician_id)


def deactivate_musician(conn: sqlite3.Connection, musician_id: int) -> None:
    get_musician(conn, musician_id)
    conn.execute("UPDATE musicians SET active = 0 WHERE id = ?", (musician_id,))


# --------------------------------------------------------------------------- #
# Posten
# --------------------------------------------------------------------------- #

_ITEM_SQL = """
SELECT li.*, m.name AS musician_name
FROM line_items li LEFT JOIN musicians m ON m.id = li.musician_id
"""


def _items_of_variant(conn: sqlite3.Connection, variant_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(_ITEM_SQL + " WHERE li.variant_id = ? ORDER BY li.sort_order, li.id", (variant_id,))]


def get_item(conn: sqlite3.Connection, item_id: int) -> dict:
    r = conn.execute(_ITEM_SQL + " WHERE li.id = ?", (item_id,)).fetchone()
    if r is None:
        raise NotFound(f"Posten {item_id} nicht gefunden")
    return dict(r)


def create_item(conn: sqlite3.Connection, variant_id: int, data: dict) -> dict:
    gig_id = _gig_id_of_variant(conn, variant_id)
    kind = data.get("kind") or "musician"
    if kind not in ITEM_KINDS:
        raise Invalid(f"kind muss einer von {ITEM_KINDS} sein")
    musician_id = data.get("musician_id")
    flags = [_check_flag(data.get("info") or "open", "info"), _check_flag(data.get("invoice") or "open", "invoice"),
             _check_flag(data.get("paid") or "open", "paid")]
    if musician_id is not None:
        musician_id = int(musician_id)
        if get_musician(conn, musician_id)["is_self"]:
            flags = ["na", "na", "na"]  # ich schreibe mir keine Rechnung und informiere mich nicht
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM line_items WHERE variant_id = ?", (variant_id,)).fetchone()[0]
    cur = conn.execute(
        """INSERT INTO line_items (variant_id, kind, role, musician_id, label, amount, info, invoice, paid, note, sort_order)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (variant_id, kind, (data.get("role") or "").strip(), musician_id, (data.get("label") or "").strip(),
         _int(data.get("amount") or 0, "amount"), *flags, data.get("note") or "", max_order + 1),
    )
    _touch_gig(conn, gig_id)
    return get_item(conn, cur.lastrowid)


_ITEM_FIELDS = ("kind", "role", "musician_id", "label", "amount", "info", "invoice", "paid", "note", "sort_order")


def update_item(conn: sqlite3.Connection, item_id: int, data: dict) -> dict:
    item = get_item(conn, item_id)
    sets, vals = [], []
    for k in _ITEM_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k in ("info", "invoice", "paid"):
            v = _check_flag(v, k)
        elif k in ("amount", "sort_order"):
            v = _int(v, k)
        elif k == "kind":
            if v not in ITEM_KINDS:
                raise Invalid(f"kind muss einer von {ITEM_KINDS} sein")
        elif k == "musician_id":
            if v is not None:
                v = int(v)
                get_musician(conn, v)
        else:
            v = (v or "")
        sets.append(f"{k} = ?")
        vals.append(v)
    if sets:
        conn.execute(f"UPDATE line_items SET {', '.join(sets)} WHERE id = ?", (*vals, item_id))
        _touch_gig(conn, _gig_id_of_variant(conn, item["variant_id"]))
    # Eigene Zeile: Häkchen immer `na` – auch wenn jemand versucht, sie zu setzen.
    # Wechsel von „ich" zu jemand anderem: Häkchen wieder auf offen.
    new_mid = data.get("musician_id", item["musician_id"])
    was_self = item["musician_id"] is not None and item["musician_id"] == self_musician_id(conn)
    if new_mid is not None and new_mid == self_musician_id(conn):
        conn.execute("UPDATE line_items SET info='na', invoice='na', paid='na' WHERE id = ?", (item_id,))
    elif was_self and new_mid != item["musician_id"]:
        conn.execute("UPDATE line_items SET info='open', invoice='open', paid='open' WHERE id = ?", (item_id,))
    return get_item(conn, item_id)


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    item = get_item(conn, item_id)
    conn.execute("DELETE FROM line_items WHERE id = ?", (item_id,))
    _touch_gig(conn, _gig_id_of_variant(conn, item["variant_id"]))


def reorder_items(conn: sqlite3.Connection, variant_id: int, item_ids: list[int]) -> list[dict]:
    gig_id = _gig_id_of_variant(conn, variant_id)
    for order, item_id in enumerate(item_ids):
        conn.execute("UPDATE line_items SET sort_order = ? WHERE id = ? AND variant_id = ?", (order, item_id, variant_id))
    _touch_gig(conn, gig_id)
    return _items_of_variant(conn, variant_id)


# --------------------------------------------------------------------------- #
# Varianten
# --------------------------------------------------------------------------- #


def get_variant(conn: sqlite3.Connection, variant_id: int, with_items: bool = True) -> dict:
    r = conn.execute("SELECT * FROM variants WHERE id = ?", (variant_id,)).fetchone()
    if r is None:
        raise NotFound(f"Variante {variant_id} nicht gefunden")
    v = dict(r)
    items = _items_of_variant(conn, variant_id)
    v["totals"] = totals(items, v["fee"])
    if with_items:
        v["items"] = items
    return v


def create_variant(conn: sqlite3.Connection, gig_id: int, name: str, fee: int,
                   copy_from_variant_id: int | None = None, keep_flags: bool = False) -> dict:
    get_gig_row(conn, gig_id)
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM variants WHERE gig_id = ?", (gig_id,)).fetchone()[0]
    cur = conn.execute("INSERT INTO variants (gig_id, name, fee, sort_order) VALUES (?,?,?,?)",
                       (gig_id, (name or "Standard").strip() or "Standard", _int(fee, "fee"), max_order + 1))
    new_id = cur.lastrowid
    if copy_from_variant_id is not None:
        _copy_items(conn, copy_from_variant_id, new_id, keep_flags=keep_flags)
    _touch_gig(conn, gig_id)
    return get_variant(conn, new_id)


def _copy_items(conn: sqlite3.Connection, src_variant_id: int, dst_variant_id: int, keep_flags: bool) -> None:
    for it in _items_of_variant(conn, src_variant_id):
        flags = (it["info"], it["invoice"], it["paid"]) if keep_flags else tuple(
            # „entfällt" bleibt entfällt (z. B. Bandleader ohne Rechnung), erledigt wird wieder offen
            "na" if f == "na" else "open" for f in (it["info"], it["invoice"], it["paid"]))
        conn.execute(
            """INSERT INTO line_items (variant_id, kind, role, musician_id, label, amount, info, invoice, paid, note, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (dst_variant_id, it["kind"], it["role"], it["musician_id"], it["label"], it["amount"],
             *flags, it["note"] if keep_flags else "", it["sort_order"]),
        )


def update_variant(conn: sqlite3.Connection, variant_id: int, data: dict) -> dict:
    gig_id = _gig_id_of_variant(conn, variant_id)
    if "name" in data:
        conn.execute("UPDATE variants SET name = ? WHERE id = ?", ((data["name"] or "").strip() or "Standard", variant_id))
    if "fee" in data:
        conn.execute("UPDATE variants SET fee = ? WHERE id = ?", (_int(data["fee"], "fee"), variant_id))
    _touch_gig(conn, gig_id)
    return get_variant(conn, variant_id)


def delete_variant(conn: sqlite3.Connection, variant_id: int) -> None:
    gig_id = _gig_id_of_variant(conn, variant_id)
    gig = get_gig_row(conn, gig_id)
    count = conn.execute("SELECT COUNT(*) FROM variants WHERE gig_id = ?", (gig_id,)).fetchone()[0]
    if count <= 1:
        raise Invalid("Die letzte Variante eines Gigs kann nicht gelöscht werden")
    if gig["active_variant_id"] == variant_id:
        raise Invalid("Die aktive Variante kann nicht gelöscht werden – erst eine andere aktivieren")
    conn.execute("DELETE FROM variants WHERE id = ?", (variant_id,))
    _touch_gig(conn, gig_id)


# --------------------------------------------------------------------------- #
# Gigs
# --------------------------------------------------------------------------- #


def get_gig_row(conn: sqlite3.Connection, gig_id: int) -> dict:
    r = conn.execute("SELECT * FROM gigs WHERE id = ?", (gig_id,)).fetchone()
    if r is None:
        raise NotFound(f"Gig {gig_id} nicht gefunden")
    return dict(r)


def get_gig(conn: sqlite3.Connection, gig_id: int) -> dict:
    g = get_gig_row(conn, gig_id)
    g["variants"] = [get_variant(conn, r["id"]) for r in
                     conn.execute("SELECT id FROM variants WHERE gig_id = ? ORDER BY sort_order, id", (gig_id,))]
    return g


def _year_of(g: dict) -> int:
    return int((g["date"] or g["created_at"])[:4])


def _list_item(conn: sqlite3.Connection, g: dict) -> dict:
    active = get_variant(conn, g["active_variant_id"], with_items=False) if g["active_variant_id"] else None
    return {
        "id": g["id"], "title": g["title"], "date": g["date"], "venue": g["venue"], "status": g["status"],
        "year": _year_of(g), "active_variant_id": g["active_variant_id"],
        "variant_count": conn.execute("SELECT COUNT(*) FROM variants WHERE gig_id = ?", (g["id"],)).fetchone()[0],
        "totals": active["totals"] if active else totals([], 0),
        "updated_at": g["updated_at"],
    }


def list_gigs(conn: sqlite3.Connection, year: int | None = None, status: str | None = None) -> list[dict]:
    where, vals = [], []
    if status:
        where.append("status = ?")
        vals.append(_check_status(status))
    sql = "SELECT * FROM gigs" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY COALESCE(date, substr(created_at,1,10)) DESC, id DESC"
    rows = [_list_item(conn, dict(r)) for r in conn.execute(sql, vals)]
    if year is not None:
        rows = [r for r in rows if r["year"] == year]
    return rows


def find_gig(conn: sqlite3.Connection, query: str | int) -> dict:
    """Gig per id oder per Titel-Teilstring (+ optional Jahr) finden – für MCP-Aufrufe wie „Hochzeit Miller" oder „Sommerfest 2025"."""
    if isinstance(query, int) or str(query).strip().isdigit():
        return get_gig(conn, int(query))
    q = str(query).strip()
    year = None
    parts = q.split()
    if parts and parts[-1].isdigit() and len(parts[-1]) == 4:
        year = int(parts[-1])
        q = " ".join(parts[:-1])
    rows = [dict(r) for r in conn.execute("SELECT * FROM gigs WHERE title LIKE ? COLLATE NOCASE ORDER BY COALESCE(date, created_at) DESC", (f"%{q}%",))]
    if year is not None:
        rows = [r for r in rows if _year_of(r) == year]
    if not rows:
        raise NotFound(f"Kein Gig passt zu {query!r}")
    if len(rows) > 1:
        names = ", ".join(f"{r['title']} ({r['date'] or _year_of(r)}, id {r['id']})" for r in rows[:8])
        raise Invalid(f"{query!r} ist nicht eindeutig: {names}. Bitte id oder Jahr angeben.")
    return get_gig(conn, rows[0]["id"])


def create_gig(conn: sqlite3.Connection, data: dict) -> dict:
    title = (data.get("title") or "").strip()
    if not title:
        raise Invalid("title fehlt")
    status = _check_status(data.get("status") or "angebot")
    cur = conn.execute(
        "INSERT INTO gigs (title, date, venue, status, notes) VALUES (?,?,?,?,?)",
        (title, _check_date(data.get("date")), (data.get("venue") or "").strip(), status, data.get("notes") or ""),
    )
    gig_id = cur.lastrowid
    fee = _int(data.get("fee") or 0, "fee")
    template_id = data.get("template_gig_id")
    if template_id:
        tpl = get_gig_row(conn, int(template_id))
        variant = create_variant(conn, gig_id, data.get("variant_name") or "Standard", fee,
                                 copy_from_variant_id=tpl["active_variant_id"])
    else:
        variant = create_variant(conn, gig_id, data.get("variant_name") or "Standard", fee)
    conn.execute("UPDATE gigs SET active_variant_id = ? WHERE id = ?", (variant["id"], gig_id))
    return get_gig(conn, gig_id)


_GIG_FIELDS = ("title", "date", "venue", "status", "notes", "active_variant_id")


def update_gig(conn: sqlite3.Connection, gig_id: int, data: dict) -> dict:
    get_gig_row(conn, gig_id)
    sets, vals = [], []
    for k in _GIG_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k == "date":
            v = _check_date(v)
        elif k == "status":
            v = _check_status(v)
        elif k == "active_variant_id":
            v = int(v)
            if _gig_id_of_variant(conn, v) != gig_id:
                raise Invalid("Variante gehört nicht zu diesem Gig")
        elif k == "title":
            v = (v or "").strip()
            if not v:
                raise Invalid("title darf nicht leer sein")
        else:
            v = v or ""
        sets.append(f"{k} = ?")
        vals.append(v)
    if sets:
        conn.execute(f"UPDATE gigs SET {', '.join(sets)} WHERE id = ?", (*vals, gig_id))
        _touch_gig(conn, gig_id)
    return get_gig(conn, gig_id)


def delete_gig(conn: sqlite3.Connection, gig_id: int) -> None:
    get_gig_row(conn, gig_id)
    conn.execute("DELETE FROM gigs WHERE id = ?", (gig_id,))


def duplicate_gig(conn: sqlite3.Connection, gig_id: int, title: str | None = None, date_: str | None = None,
                  status: str = "angebot") -> dict:
    src = get_gig(conn, gig_id)
    cur = conn.execute(
        "INSERT INTO gigs (title, date, venue, status, notes) VALUES (?,?,?,?,?)",
        ((title or f"{src['title']} (Kopie)").strip(), _check_date(date_), src["venue"], _check_status(status), ""),
    )
    new_id = cur.lastrowid
    id_map: dict[int, int] = {}
    for v in src["variants"]:
        nv = create_variant(conn, new_id, v["name"], v["fee"], copy_from_variant_id=v["id"])
        id_map[v["id"]] = nv["id"]
    conn.execute("UPDATE gigs SET active_variant_id = ? WHERE id = ?", (id_map[src["active_variant_id"]], new_id))
    return get_gig(conn, new_id)


# --------------------------------------------------------------------------- #
# Auswertung
# --------------------------------------------------------------------------- #


def open_payments(conn: sqlite3.Connection, gig_id: int | None = None) -> list[dict]:
    sql = """
        SELECT g.id AS gig_id, g.title AS gig_title, g.date AS gig_date, g.status AS gig_status,
               li.id AS item_id, li.kind, li.role, li.label, li.amount, li.info, li.invoice, li.paid,
               li.note, m.name AS musician_name, m.id AS musician_id, m.email, m.phone
        FROM line_items li
        JOIN variants v ON v.id = li.variant_id
        JOIN gigs g ON g.id = v.gig_id AND g.active_variant_id = v.id
        LEFT JOIN musicians m ON m.id = li.musician_id
        WHERE li.paid = 'open' AND li.amount > 0 AND g.status IN ('bestaetigt','abgerechnet')
    """
    vals: list = []
    if gig_id is not None:
        sql += " AND g.id = ?"
        vals.append(gig_id)
    sql += " ORDER BY COALESCE(g.date, '9999') DESC, li.sort_order"
    out = []
    for r in conn.execute(sql, vals):
        d = dict(r)
        d["who"] = d["musician_name"] or d["label"] or d["role"]
        out.append(d)
    return out


def stats(conn: sqlite3.Connection) -> dict:
    self_id = self_musician_id(conn)
    years: dict[int, dict] = {}
    for g in list_gigs(conn):
        if g["status"] not in COUNTING_STATUS:
            continue
        y = years.setdefault(g["year"], {"year": g["year"], "gigs": 0, "fee_total": 0, "rest_total": 0, "open_items": 0, "self_total": 0})
        t = g["totals"]
        y["gigs"] += 1
        y["fee_total"] += t["fee"]
        y["rest_total"] += t["rest"]
        y["open_items"] += t["items"] - t["paid_done"]
        if self_id is not None:
            y["self_total"] += conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM line_items WHERE variant_id = ? AND musician_id = ?",
                (g["active_variant_id"], self_id)).fetchone()[0]
    return {
        "self_musician_id": self_id,
        "years": sorted(years.values(), key=lambda x: x["year"]),
        "open_payments": open_payments(conn),
        "templates": list_gigs(conn, status="vorlage"),
    }


# --------------------------------------------------------------------------- #
# Ereignisse (Erinnerungen etc.)
# --------------------------------------------------------------------------- #


def log_event(conn: sqlite3.Connection, gig_id: int | None, item_id: int | None, kind: str, text: str) -> dict:
    cur = conn.execute("INSERT INTO events (gig_id, item_id, kind, text) VALUES (?,?,?,?)", (gig_id, item_id, kind, text))
    return dict(conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_events(conn: sqlite3.Connection, gig_id: int, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM events WHERE gig_id = ? ORDER BY ts DESC, id DESC LIMIT ?", (gig_id, limit))]
