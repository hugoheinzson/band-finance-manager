"""Backup-Werkzeuge für den Band Manager – von deploy/backup.sh aufgerufen, auch einzeln nutzbar.

    uv run python scripts/backup.py snapshot  OUT.db            # konsistente Kopie der laufenden DB (SQLite-Backup-API)
    uv run python scripts/backup.py verify    FILE.db[.gz] [--expect-gigs N]
    uv run python scripts/backup.py export-csv OUTDIR [--public] # gigs.csv, posten.csv, musiker.csv
    uv run python scripts/backup.py notify    "Betreff" "Text"   # Mail über SMTP-Config (BANDMANAGER_MAIL_CONFIG)

`--public` lässt musiker.csv und alle Kontaktdaten/IBAN weg – für eine unverschlüsselte Ablage.
Die DB wird nie als Datei kopiert (WAL-Modus, der Container schreibt), sondern über
`sqlite3.Connection.backup()` gesnapshottet und danach mit `PRAGMA integrity_check` geprüft.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import shutil
import smtplib
import sqlite3
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402

FLAG_TEXT = {"open": "offen", "done": "erledigt", "na": "entfällt"}


def _open_ro(path: Path) -> sqlite3.Connection:
    """Öffnet eine Datei (auch .gz) lesend; .gz wird dafür temporär entpackt."""
    if path.suffix == ".gz":
        tmp = Path(tempfile.mkstemp(suffix=".db")[1])
        with gzip.open(path, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        path = tmp
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _counts(conn: sqlite3.Connection) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("gigs", "variants", "line_items", "musicians", "events")}


def cmd_snapshot(out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    src = db.connect()
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)  # atomar & konsistent, auch bei laufendem Container
        # Der Snapshot erbt den WAL-Modus der Quelle → auf klassisches Journal zurück,
        # damit eine einzelne, in sich geschlossene Datei entsteht (ohne -wal/-shm).
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()
    for suffix in ("-wal", "-shm"):
        Path(str(out) + suffix).unlink(missing_ok=True)
    return cmd_verify(out)


def cmd_verify(path: Path, expect_gigs: int | None = None) -> dict:
    conn = _open_ro(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        counts = _counts(conn)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    finally:
        conn.close()
    ok = integrity == "ok" and (expect_gigs is None or counts["gigs"] == expect_gigs)
    result = {"file": str(path), "integrity": integrity, "schema_version": version, "counts": counts, "ok": ok}
    if expect_gigs is not None:
        result["expected_gigs"] = expect_gigs
    return result


def cmd_export_csv(outdir: Path, public: bool) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    try:
        gigs = conn.execute("""
            SELECT g.id, g.title, g.date, g.venue, g.status, g.notes, v.name AS variant, v.fee,
                   (SELECT COALESCE(SUM(amount),0) FROM line_items WHERE variant_id = v.id AND kind='musician') AS musicians_total,
                   (SELECT COALESCE(SUM(amount),0) FROM line_items WHERE variant_id = v.id AND kind='cost') AS costs_total
            FROM gigs g JOIN variants v ON v.id = g.active_variant_id
            ORDER BY COALESCE(g.date, g.created_at) DESC""").fetchall()
        with open(outdir / "gigs.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["gig_id", "titel", "datum", "ort", "status", "variante", "gage_netto", "summe_musiker", "summe_kosten", "rest", "notizen"])
            for g in gigs:
                w.writerow([g["id"], g["title"], g["date"] or "", g["venue"], g["status"], g["variant"], g["fee"],
                            g["musicians_total"], g["costs_total"], g["fee"] - g["musicians_total"] - g["costs_total"],
                            g["notes"].replace("\n", " ")])

        items = conn.execute("""
            SELECT g.id AS gig_id, g.title, g.date, v.name AS variant, (v.id = g.active_variant_id) AS active,
                   li.kind, li.role, m.name AS musician, li.label, li.amount, li.info, li.invoice, li.paid, li.note
            FROM line_items li JOIN variants v ON v.id = li.variant_id JOIN gigs g ON g.id = v.gig_id
            LEFT JOIN musicians m ON m.id = li.musician_id
            ORDER BY COALESCE(g.date, g.created_at) DESC, v.sort_order, li.sort_order""").fetchall()
        with open(outdir / "posten.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["gig_id", "gig", "datum", "variante", "aktive_variante", "art", "rolle", "wer", "betrag_netto",
                        "info", "rechnung", "bezahlt", "notiz"])
            for it in items:
                w.writerow([it["gig_id"], it["title"], it["date"] or "", it["variant"], "ja" if it["active"] else "nein",
                            "Musiker" if it["kind"] == "musician" else "Kosten", it["role"], it["musician"] or it["label"],
                            it["amount"], FLAG_TEXT[it["info"]], FLAG_TEXT[it["invoice"]], FLAG_TEXT[it["paid"]], it["note"]])

        written = ["gigs.csv", "posten.csv"]
        if not public:
            rows = conn.execute("SELECT * FROM musicians ORDER BY name COLLATE NOCASE").fetchall()
            with open(outdir / "musiker.csv", "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["id", "spitzname", "vorname", "nachname", "rolle", "hauptbesetzung", "standardgage",
                            "email", "telefon", "iban", "notizen", "aktiv"])
                for m in rows:
                    w.writerow([m["id"], m["name"], m["first_name"], m["last_name"], m["role"], "ja" if m["is_core"] else "nein",
                                m["default_fee"], m["email"], m["phone"], m["iban"],
                                m["notes"].replace("\n", " "), "ja" if m["active"] else "nein"])
            written.append("musiker.csv")
    finally:
        conn.close()
    return {"dir": str(outdir), "files": written, "gigs": len(gigs), "items": len(items), "public": public}


def cmd_notify(subject: str, body: str) -> dict:
    cfg_path = Path(os.getenv("BANDMANAGER_MAIL_CONFIG", "")).expanduser()
    if not cfg_path.is_file():
        return {"sent": False, "reason": f"keine Mail-Config ({cfg_path or 'BANDMANAGER_MAIL_CONFIG leer'})"}
    cfg: dict[str, str] = {}
    for line in cfg_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], cfg["MAIL_TO"], subject
    msg.set_content(body)
    with smtplib.SMTP(cfg["SMTP_HOST"], int(cfg.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        s.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
        s.send_message(msg)
    return {"sent": True, "to": cfg["MAIL_TO"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot"); s.add_argument("out", type=Path)
    v = sub.add_parser("verify"); v.add_argument("file", type=Path); v.add_argument("--expect-gigs", type=int)
    e = sub.add_parser("export-csv"); e.add_argument("outdir", type=Path); e.add_argument("--public", action="store_true")
    n = sub.add_parser("notify"); n.add_argument("subject"); n.add_argument("body")
    a = ap.parse_args()

    if a.cmd == "snapshot":
        r = cmd_snapshot(a.out)
    elif a.cmd == "verify":
        r = cmd_verify(a.file, a.expect_gigs)
    elif a.cmd == "export-csv":
        r = cmd_export_csv(a.outdir, a.public)
    else:
        r = cmd_notify(a.subject, a.body)
    print(json.dumps(r, ensure_ascii=False))
    if r.get("ok") is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
