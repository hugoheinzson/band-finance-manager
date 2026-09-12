"""Füllt eine leere Datenbank mit fiktiven Beispieldaten – zum Ausprobieren und für Screenshots.

    BANDMANAGER_DB=/tmp/demo.db uv run python scripts/demo_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db, service  # noqa: E402

PEOPLE = [("Me", "Keys", 300), ("Anna", "Gesang", 320), ("Ben", "Bass", 180), ("Chris", "Gitarre", 200),
          ("Dave", "Drums", 180), ("Eve", "Sax", 180), ("Finn", "FOH", 150), ("Gina", "Trompete", 250), ("Hugo", "Gesang", 300)]

# (Titel, Datum, Ort, Status, Gage, [(Rolle, Person, Betrag, info, invoice, paid, Notiz)], [(Kosten, Wer, Betrag)])
GIGS = [
    ("Sommerfest Riverside", "2025-07-05", "Stadtfest", "abgerechnet", 2200,
     [("Keys", "Me", 300, "na", "na", "na", ""), ("Gesang", "Anna", 320, "done", "done", "done", ""),
      ("Bass", "Ben", 200, "done", "done", "done", ""), ("Gitarre", "Chris", 200, "done", "done", "done", ""),
      ("Drums", "Dave", 200, "done", "done", "done", ""), ("Sax", "Eve", 200, "done", "done", "done", ""),
      ("Trompete", "Gina", 300, "done", "done", "done", ""), ("Gesang", "Hugo", 300, "done", "done", "done", "")], []),
    ("Weinfest Hillside", "2025-09-20", "Weinfest", "abgerechnet", 2000,
     [("Keys", "Me", 350, "na", "na", "na", ""), ("Gesang", "Anna", 320, "done", "done", "done", ""),
      ("Bass", "Ben", 180, "done", "done", "done", ""), ("Gitarre", "Chris", 180, "done", "done", "done", ""),
      ("Drums", "Dave", 180, "done", "done", "done", ""), ("Sax", "Eve", 180, "done", "done", "done", ""),
      ("Trompete", "Gina", 250, "done", "done", "done", ""), ("Gesang", "Hugo", 320, "done", "done", "done", "")], []),
    ("Hochzeit Miller", "2026-07-18", "Hochzeit / Privat", "bestaetigt", 2500,
     [("Keys", "Me", 300, "na", "na", "na", ""), ("Gesang", "Anna", 280, "done", "open", "open", "600 EUR gesamt abgesprochen"),
      ("Bass", "Ben", 190, "done", "open", "open", ""), ("Gitarre", "Chris", 300, "done", "done", "done", ""),
      ("Drums", "Dave", 190, "done", "open", "open", ""), ("Sax", "Eve", 190, "done", "open", "open", ""),
      ("FOH", "Finn", 200, "done", "done", "done", ""), ("Trompete", "Gina", 275, "done", "done", "done", ""),
      ("Gesang", "Hugo", 280, "done", "done", "done", "")],
     [("Licht", "Verleih", 230), ("Pult", "", 50)]),
    ("Firmenfeier Nordwerk", "2026-08-01", "Privatfeier", "angebot", 1100,
     [("Keys", "Me", 200, "open", "open", "open", ""), ("Gesang", "Anna", 200, "open", "open", "open", ""),
      ("Bass", "Ben", 150, "open", "open", "open", ""), ("Gitarre", "Chris", 150, "open", "open", "open", ""),
      ("Drums", "Dave", 150, "open", "open", "open", ""), ("Sax", "Eve", 150, "open", "open", "open", "")], []),
]


def main() -> None:
    db.init_db()
    with db.tx() as conn:
        if conn.execute("SELECT COUNT(*) FROM gigs").fetchone()[0]:
            sys.exit("Datenbank ist nicht leer – Demo-Daten nur in eine frische DB laden.")
        people = {n: service.create_musician(conn, {"name": n, "role": r, "default_fee": f})["id"] for n, r, f in PEOPLE}
        for title, date_, venue, status, fee, lines, costs in GIGS:
            g = service.create_gig(conn, {"title": title, "date": date_, "venue": venue, "status": status, "fee": fee,
                                          "variant_name": "Volle Besetzung"})
            vid = g["active_variant_id"]
            for role, who, amount, info, inv, paid, note in lines:
                service.create_item(conn, vid, {"kind": "musician", "role": role, "musician_id": people[who], "amount": amount,
                                                "info": info, "invoice": inv, "paid": paid, "note": note})
            for role, label, amount in costs:
                service.create_item(conn, vid, {"kind": "cost", "role": role, "label": label, "amount": amount})
            if status == "angebot":
                service.create_variant(conn, g["id"], "Mit Bläsern", 1600, copy_from_variant_id=vid)
        # Vorlage: kleine Besetzung
        t = service.create_gig(conn, {"title": "Kleine Besetzung", "status": "vorlage", "fee": 1100, "variant_name": "Standard"})
        for role, who, amount in (("Keys", "Me", 200), ("Gesang", "Anna", 250), ("Bass", "Ben", 150), ("Gitarre", "Chris", 200),
                                  ("Drums", "Dave", 150), ("Sax", "Eve", 150)):
            service.create_item(conn, t["active_variant_id"], {"kind": "musician", "role": role, "musician_id": people[who], "amount": amount})
    print(f"Demo-Daten geladen: {len(GIGS) + 1} Gigs, {len(PEOPLE)} Personen → {db.DB_PATH}")


if __name__ == "__main__":
    main()
