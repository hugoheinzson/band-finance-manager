"""Einmal-Import einer Gig-Tabelle (ein Blatt pro Gig, .xlsx) in den Band Manager.

    uv run python scripts/import_excel.py ~/gigs.xlsx [--reset]

Ein Blatt = ein Gig. Zwei Budget-Blöcke auf einem Blatt (2026) = zwei Varianten.
Häkchen werden 1:1 übernommen: `x` → done, `-` → na, leer → open, `!` → open + Notiz.
Für `self_name` (den Bandleader) werden alle drei Häkchen auf `na` gesetzt (keine
Rechnung an sich selbst). Nicht namentlich genannte Rollen („drums", „Sax") bleiben
unbesetzt – außer `year_defaults` macht es für ein Jahr eindeutig.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db, service  # noqa: E402

# Band-spezifisches Mapping (Namen, Rollen, Kostenposten) liegt in import-config.json
# (gitignored) – Vorlage: import-config.example.json. Alles case-insensitiv.
ROOT = Path(__file__).resolve().parent.parent
ROLES: dict[str, str] = {}
NAMES: dict[str, tuple[str, str]] = {}
COSTS: dict[str, str] = {}
YEAR_DEFAULTS: dict[tuple[int, str], str] = {}
SHEET_FIX: dict[str, str] = {}
TEMPLATE_TITLES: set[str] = set()
SELF_NAME = ""


def load_config(path: Path) -> None:
    global SELF_NAME
    cfg = json.loads(path.read_text(encoding="utf-8"))
    ROLES.update({k.lower(): v for k, v in cfg.get("roles", {}).items()})
    NAMES.update({k.lower(): (v[0], v[1] if len(v) > 1 else "") for k, v in cfg.get("names", {}).items()})
    COSTS.update({k.lower(): v for k, v in cfg.get("costs", {}).items()})
    for key, name in cfg.get("year_defaults", {}).items():
        year, role = key.split("|", 1)
        YEAR_DEFAULTS[(int(year), role)] = name
    SHEET_FIX.update(cfg.get("sheet_fix", {}))
    TEMPLATE_TITLES.update(cfg.get("template_titles", []))
    SELF_NAME = cfg.get("self_name", "")


def parse_sheet_name(name: str) -> tuple[int | None, str | None, str, str]:
    """'2023 16.09. Weinf' → (2023, '2023-09-16', 'Weinfest', status)."""
    year = None
    m = re.match(r"^(\d{4})\s+", name)
    rest = name
    if m:
        year = int(m.group(1))
        rest = name[m.end():]
    date = None
    dm = re.search(r"(\d{1,2})\.(\d{1,2})\.?", rest)
    if dm and year:
        date = f"{year}-{int(dm.group(2)):02d}-{int(dm.group(1)):02d}"
        rest = (rest[:dm.start()] + rest[dm.end():]).strip()
    title = re.sub(r"\s+", " ", rest).strip()
    for short, full in SHEET_FIX.items():
        if title.endswith(short):
            title = title[: -len(short)] + full
    status = "abgerechnet"
    if title.lower().startswith("beispiel"):
        title, status = title[len("Beispiel"):].strip(), "vorlage"
    elif title.lower().startswith("angebot"):
        title, status = title[len("Angebot"):].strip(), "abgesagt"
    elif title in TEMPLATE_TITLES:
        status = "vorlage"
    elif year and year >= 2026:
        status = "bestaetigt"
    return year, date, title, status


def flag(v) -> tuple[str, str]:
    s = (str(v).strip().lower() if v is not None else "")
    if s == "x":
        return "done", ""
    if s == "-":
        return "na", ""
    if s == "!":
        return "open", "!"
    return "open", ""


def classify(label: str, year: int | None) -> dict:
    """Zeilenbezeichnung → kind/role/musician/label/note."""
    raw = label.strip()
    low = raw.lower().rstrip(":")
    if low in COSTS:
        return {"kind": "cost", "role": COSTS[low], "musician": None, "label": "", "note": ""}
    tokens = [t for t in re.split(r"[\s()/]+", low) if t]
    roles = [ROLES[t] for t in tokens if t in ROLES]
    names = [NAMES[t] for t in tokens if t in NAMES]
    note = ""
    if len(names) > 1:
        note = raw  # z. B. „Anna/Ben" – erste Person eintragen, Original als Notiz
    musician = names[0][0] if names else None
    role = roles[0] if roles else (names[0][1] or names[0][0] if names else raw)
    if musician is None and (year, role) in YEAR_DEFAULTS:
        musician = YEAR_DEFAULTS[(year, role)]
    return {"kind": "musician", "role": role, "musician": musician, "label": "", "note": note}


def find_blocks(ws) -> list[tuple[int, int]]:
    """Blöcke = Zeilen, in denen B numerisch ist und A leer oder 'Gage …' → Startzeile; Ende = 'Übrig' oder leere A-Spalte."""
    blocks = []
    rows = list(ws.iter_rows(min_row=1, max_row=ws.max_row))
    starts = [r[0].row for r in rows if isinstance(r[1].value, (int, float)) and (r[0].value is None or str(r[0].value).lower().startswith("gage"))]
    for i, start in enumerate(starts):
        limit = starts[i + 1] - 1 if i + 1 < len(starts) else ws.max_row
        end = start
        for row in range(start + 1, limit + 1):
            a = ws.cell(row=row, column=1).value
            c = ws.cell(row=row, column=3).value
            if a is None and c is None:
                break
            end = row
        blocks.append((start, end))
    return blocks


def import_sheet(conn, ws, musicians: dict[str, int], stats: dict) -> None:
    year, date, title, status = parse_sheet_name(ws.title)
    blocks = find_blocks(ws)
    if not blocks:
        print(f"  ! {ws.title}: kein Budget-Block gefunden, übersprungen")
        return
    gig_notes = []
    # lose Notizen: Zellen in B/C unterhalb des ersten Blocks mit Text
    for row in range(blocks[0][1] + 1, ws.max_row + 1):
        for col in (2, 3):
            v = ws.cell(row=row, column=col).value
            if isinstance(v, str) and v.strip() and not any(b[0] <= row <= b[1] for b in blocks):
                gig_notes.append(v.strip())

    fee0 = int(ws.cell(row=blocks[0][0], column=2).value or 0)
    gig = service.create_gig(conn, {"title": title, "date": date, "status": status, "fee": fee0,
                                    "notes": "\n".join(gig_notes), "variant_name": f"Budget {fee0}"})
    if date is None and year:
        conn.execute("UPDATE gigs SET created_at = ? WHERE id = ?", (f"{year}-06-01 12:00:00", gig["id"]))
    variant_ids = [gig["active_variant_id"]]
    for start, _ in blocks[1:]:
        fee = int(ws.cell(row=start, column=2).value or 0)
        variant_ids.append(service.create_variant(conn, gig["id"], f"Budget {fee}", fee)["id"])

    for (start, end), variant_id in zip(blocks, variant_ids):
        # Zeile 'Spesen' kann in B eine Bezeichnung tragen („Licht Verleih" 230)
        for row in range(start + 1, end + 1):
            a, b, c = (ws.cell(row=row, column=k).value for k in (1, 2, 3))
            e, f, g = (ws.cell(row=row, column=k).value for k in (5, 6, 7))
            extra = " ".join(str(ws.cell(row=row, column=k).value).strip() for k in (8, 9) if ws.cell(row=row, column=k).value)
            if a is None and isinstance(c, str):
                # Sonderzeilen wie C14='Fotograf' D14='150 (Cash)' oder C14='Pult 50 EUR'
                d = ws.cell(row=row, column=4).value
                text = f"{c} {d or ''}".strip()
                m = re.search(r"(\d+)", text)
                amount = int(m.group(1)) if m else 0
                name = re.split(r"\s|\d", c.strip())[0]
                note = re.sub(r"^\s*\d+\s*", "", text.replace(c.strip(), "", 1)).strip(" ()") if d else re.sub(r"\d+\s*EUR", "", text).replace(name, "").strip()
                service.create_item(conn, variant_id, {"kind": "cost", "role": name.title(), "amount": amount, "note": note,
                                                       "info": "na", "invoice": "na", "paid": "na"})
                continue
            if a is None:
                continue
            label = str(a).strip()
            if label.lower().rstrip(":") in ("übrig", "-", ""):
                continue
            amount = int(c) if isinstance(c, (int, float)) else 0
            info = classify(label, year)
            if info["kind"] == "cost" and info["role"] == "Spesen":
                if amount == 0 and not isinstance(b, str):
                    continue
                # „Spesen | Licht Verleih | 230" → Kostenposten Licht, label Verleih
                if isinstance(b, str):
                    parts = b.split()
                    info["role"] = parts[0].title()
                    info["label"] = " ".join(parts[1:])
            if info["kind"] == "cost" and amount == 0:
                continue
            fi, ni = flag(e)
            fv, nv = flag(f)
            fp, np_ = flag(g)
            notes = " ".join(x for x in (info["note"], extra, "Rechnung: !" if nv else "") if x)
            if SELF_NAME and info["musician"] == SELF_NAME:
                fi = fv = fp = "na"
            if info["kind"] == "cost" and (e, f, g) == (None, None, None):
                fi = fv = fp = "na"
            musician_id = musicians.get(info["musician"]) if info["musician"] else None
            service.create_item(conn, variant_id, {"kind": info["kind"], "role": info["role"], "musician_id": musician_id,
                                                   "label": info["label"], "amount": amount, "info": fi, "invoice": fv,
                                                   "paid": fp, "note": notes})
            stats["items"] += 1
            if info["kind"] == "musician" and musician_id is None and amount > 0:
                stats["unassigned"] += 1
    stats["gigs"] += 1
    print(f"  ✓ {ws.title!r:40} → [{gig['id']}] {title} · {date or year} · {status} · {len(blocks)} Variante(n)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--reset", action="store_true", help="vorher alle Daten löschen")
    ap.add_argument("--config", type=Path, default=ROOT / "import-config.json",
                    help="Mapping-Datei (Default: import-config.json neben pyproject.toml)")
    args = ap.parse_args()
    if not args.config.exists():
        sys.exit(f"{args.config} fehlt – import-config.example.json kopieren und anpassen.")
    load_config(args.config)

    db.init_db()
    with db.tx() as conn:
        if args.reset:
            for t in ("events", "line_items", "variants", "gigs", "musicians"):
                conn.execute(f"DELETE FROM {t}")
            print("Datenbank geleert.")
        elif conn.execute("SELECT COUNT(*) FROM gigs").fetchone()[0]:
            sys.exit("Datenbank ist nicht leer – mit --reset neu importieren.")

        musicians: dict[str, int] = {}
        for _, (name, role) in NAMES.items():
            if name not in musicians:
                musicians[name] = service.create_musician(conn, {"name": name, "role": role, "is_self": name == SELF_NAME})["id"]
        stats = {"gigs": 0, "items": 0, "unassigned": 0}
        wb = openpyxl.load_workbook(args.xlsx, data_only=True)
        for ws in wb.worksheets:
            import_sheet(conn, ws, musicians, stats)

        # Standardgage = letzter Betrag, den die Person bekommen hat
        for name, mid in musicians.items():
            r = conn.execute("""SELECT li.amount FROM line_items li JOIN variants v ON v.id = li.variant_id
                                JOIN gigs g ON g.id = v.gig_id AND g.active_variant_id = v.id
                                WHERE li.musician_id = ? AND li.amount > 0
                                ORDER BY COALESCE(g.date, g.created_at) DESC LIMIT 1""", (mid,)).fetchone()
            if r:
                conn.execute("UPDATE musicians SET default_fee = ? WHERE id = ?", (r["amount"], mid))

    print(f"\nFertig: {stats['gigs']} Gigs, {stats['items']} Posten, {len(musicians)} Musiker. "
          f"{stats['unassigned']} bezahlte Posten ohne zugeordnete Person (in der App per Auswahl nachtragen).")


if __name__ == "__main__":
    main()
