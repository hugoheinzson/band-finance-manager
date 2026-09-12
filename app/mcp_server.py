"""MCP-Server des Band Managers – wird unter `/mcp` in dieselbe FastAPI-App gehängt.
Direkt nutzbar (Bearer-Token) oder in einem Aggregator mit `namespace="gig"`
gemountet → Tools heißen dann nach außen `gig_*`.

Arbeitsteilung: der Server verwaltet Zahlen und Status. Erinnerungen an
Musiker verschickt das **Chat-Modell** über andere Konnektoren (Mail, WhatsApp …);
`reminder_targets` liefert dafür Empfänger + Textvorschlag, `log_reminder`
protokolliert, dass es passiert ist.
"""

from __future__ import annotations

import logging
from datetime import date

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

from . import service
from .config import API_TOKEN, BAND_NAME, SENDER_NAME
from .db import tx

logger = logging.getLogger("band-manager.mcp")


class BearerAuth(Middleware):
    """`Authorization: Bearer <BANDMANAGER_API_TOKEN>` – zweite Schranke vor dem
    Aggregator (der Port lauscht ohnehin nur auf 127.0.0.1)."""

    async def on_request(self, context, call_next):
        if API_TOKEN:
            header = get_http_headers(include={"authorization"}).get("authorization", "")
            if header.removeprefix("Bearer ").strip() != API_TOKEN:
                logger.warning("MCP-Request ohne gültigen BANDMANAGER_API_TOKEN abgewiesen")
                raise ToolError("Nicht autorisiert")
        return await call_next(context)


mcp = FastMCP(
    "Band Manager",
    instructions=(
        f"Gig-Budgets und Gagen-Auszahlung der Band {BAND_NAME}. Beträge sind immer NETTO in ganzen Euro. "
        "Jeder Posten hat drei Häkchen: info (Musiker über Gage informiert), invoice (Rechnung liegt vor), "
        "paid (überwiesen) – jeweils open/done/na. Gigs lassen sich per id oder Titel (+ Jahr) ansprechen."
    ),
    middleware=[BearerAuth()],
)


def _svc(fn, *args, **kwargs):
    try:
        with tx() as conn:
            return fn(conn, *args, **kwargs)
    except (service.NotFound, service.Invalid) as exc:
        raise ToolError(str(exc)) from exc


def _eur(n: int) -> str:
    return f"{n:,} €".replace(",", ".")


def _flag(v: str) -> str:
    return {"done": "✓", "open": "·", "na": "–"}[v]


def _gig_line(g: dict) -> str:
    t = g["totals"]
    st = {"vorlage": "Vorlage", "angebot": "Angebot", "bestaetigt": "bestätigt", "abgerechnet": "abgerechnet", "abgesagt": "abgesagt"}[g["status"]]
    prog = "" if g["status"] in ("vorlage", "angebot") else f" · bezahlt {t['paid_done']}/{t['items']}"
    return f"[{g['id']}] {g['date'] or '—'} {g['title']} ({st}) · Gage {_eur(t['fee'])} · Rest {_eur(t['rest'])}{prog}"


def _gig_detail(g: dict) -> str:
    active = next(v for v in g["variants"] if v["id"] == g["active_variant_id"])
    t = active["totals"]
    lines = [
        f"**{g['title']}** (id {g['id']}) — {g['status']} · {g['date'] or 'ohne Datum'}" + (f" · {g['venue']}" if g["venue"] else ""),
        f"Variante „{active['name']}“: Gage {_eur(t['fee'])} · Musiker {_eur(t['musicians'])} · Kosten {_eur(t['costs'])} · **Rest {_eur(t['rest'])}**",
        f"Status: informiert {t['info_done']}/{t['items']} · Rechnung {t['invoice_done']}/{t['items']} · bezahlt {t['paid_done']}/{t['items']}",
        "",
        "| # | Rolle | Wer | Betrag | Info | Rechn. | Bezahlt | Notiz |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for it in active["items"]:
        who = it["musician_name"] or it["label"] or ("—" if it["kind"] == "musician" else "")
        lines.append(f"| {it['id']} | {it['role']} | {who} | {_eur(it['amount'])} | {_flag(it['info'])} | {_flag(it['invoice'])} | {_flag(it['paid'])} | {it['note']} |")
    if len(g["variants"]) > 1:
        others = ", ".join(f"„{v['name']}“ {_eur(v['fee'])} (id {v['id']})" for v in g["variants"] if v["id"] != active["id"])
        lines += ["", f"Weitere Varianten: {others}"]
    if g["notes"]:
        lines += ["", f"Notizen: {g['notes']}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Lesen
# --------------------------------------------------------------------------- #


@mcp.tool
def list_gigs(year: int | None = None, status: str = "") -> str:
    """Alle Gigs im Überblick (neueste zuerst) mit Gage, Rest und Auszahlungsstand.

    Args:
        year: Nur dieses Jahr.
        status: Nur dieser Status – vorlage | angebot | bestaetigt | abgerechnet | abgesagt. Leer = alle.
    """
    rows = _svc(service.list_gigs, year=year, status=status or None)
    if not rows:
        return "Keine Gigs gefunden."
    return "\n".join(_gig_line(g) for g in rows)


@mcp.tool
def get_gig(gig: str) -> str:
    """Ein Gig im Detail: Budget, jede Zeile mit Betrag und den drei Häkchen (Info / Rechnung / Bezahlt).

    Args:
        gig: Gig-id oder Titel, optional mit Jahr – z. B. "12", "Hochzeit Miller", "Sommerfest 2025".
    """
    return _gig_detail(_svc(service.find_gig, gig))


@mcp.tool
def open_payments(gig: str = "") -> str:
    """Wem muss noch Geld überwiesen werden? Alle offenen Auszahlungen (paid=open) über bestätigte/abgerechnete Gigs.

    Args:
        gig: Optional auf einen Gig einschränken (id oder Titel).
    """
    gig_id = _svc(service.find_gig, gig)["id"] if gig.strip() else None
    rows = _svc(service.open_payments, gig_id=gig_id)
    if not rows:
        return "Alles ausgezahlt – keine offenen Überweisungen."
    total = sum(r["amount"] for r in rows)
    lines = [f"**{len(rows)} offene Auszahlungen, zusammen {_eur(total)}**", ""]
    for r in rows:
        state = "Rechnung liegt vor" if r["invoice"] == "done" else ("Rechnung fehlt noch" if r["invoice"] == "open" else "ohne Rechnung")
        lines.append(f"- {r['who']} ({r['role']}) · {_eur(r['amount'])} · {r['gig_title']} {r['gig_date'] or ''} · {state} · Posten-id {r['item_id']}")
    return "\n".join(lines)


@mcp.tool
def list_musicians(include_inactive: bool = False) -> str:
    """Musiker & Crew mit Rolle, Standardgage, Kontaktdaten und Anzahl gespielter Gigs.

    Args:
        include_inactive: Auch deaktivierte Personen zeigen.
    """
    rows = _svc(service.list_musicians, include_inactive=include_inactive)
    if not rows:
        return "Noch keine Musiker angelegt."
    out = []
    for m in rows:
        contact = " · ".join(x for x in (m["email"], m["phone"]) if x)
        out.append(f"[{m['id']}] {m['name']} — {m['role'] or '?'} · Standard {_eur(m['default_fee'])} · {m['gig_count']} Gigs"
                   + (" · DAS BIN ICH (Bandleitung, keine Häkchen)" if m["is_self"] else "")
                   + (f" · {contact}" if contact else "") + ("" if m["active"] else " · INAKTIV"))
    return "\n".join(out)


@mcp.tool
def stats() -> str:
    """Auswertung: Gesamtgage, Gigs, Bandkassen-Rest und offene Posten pro Jahr."""
    s = _svc(service.stats)
    if not s["years"]:
        return "Noch keine gespielten Gigs."
    lines = ["| Jahr | Gigs | Gage gesamt | Mein Anteil | Rest (Bandkasse) | offene Posten |", "|---|---|---|---|---|---|"]
    for y in s["years"]:
        lines.append(f"| {y['year']} | {y['gigs']} | {_eur(y['fee_total'])} | {_eur(y['self_total'])} | {_eur(y['rest_total'])} | {y['open_items']} |")
    if s["self_musician_id"] is None:
        lines.append("(„Mein Anteil“ ist leer, weil noch niemand als „das bin ich“ markiert ist – upsert_musician(name, is_self=true).)")
    lines.append("")
    lines.append(f"Offene Auszahlungen insgesamt: {len(s['open_payments'])} (Details: open_payments).")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Schreiben
# --------------------------------------------------------------------------- #


@mcp.tool
def create_gig(title: str, fee: int, date_iso: str = "", venue: str = "", status: str = "angebot",
               template: str = "", notes: str = "") -> str:
    """Neuen Gig anlegen – auf Wunsch aus einer Vorlage (Besetzung + Beträge werden kopiert, Häkchen auf offen).

    Args:
        title: Name des Gigs, z. B. "Weinfest Hillside".
        fee: Gage netto in Euro (was der Veranstalter zahlt).
        date_iso: Datum yyyy-mm-dd, leer wenn noch unbekannt.
        venue: Ort/Art, z. B. "Stadtfest", "Hochzeit".
        status: angebot (Default) | bestaetigt | vorlage.
        template: Vorlage per id oder Titel, z. B. "Kleine Besetzung" – oder ein früherer Gig ("Sommerfest 2025"),
            dessen Besetzung übernommen werden soll. Leer = leerer Gig.
        notes: Freitext.
    """
    template_id = _svc(service.find_gig, template)["id"] if template.strip() else None
    g = _svc(service.create_gig, {"title": title, "fee": fee, "date": date_iso or None, "venue": venue,
                                  "status": status, "notes": notes, "template_gig_id": template_id})
    return "Gig angelegt.\n\n" + _gig_detail(g)


@mcp.tool
def update_gig(gig: str, title: str = "", date_iso: str = "", venue: str = "", status: str = "",
               fee: int | None = None, notes: str = "") -> str:
    """Gig-Kopfdaten ändern: Titel, Datum, Ort, Status oder die Gage der aktiven Variante. Leere Felder bleiben unverändert.

    Args:
        gig: Gig-id oder Titel (+ Jahr).
        title: Neuer Titel.
        date_iso: Neues Datum yyyy-mm-dd.
        venue: Neuer Ort.
        status: vorlage | angebot | bestaetigt | abgerechnet | abgesagt.
        fee: Neue Gage netto (aktive Variante).
        notes: Notizen (ersetzt den bisherigen Text).
    """
    g = _svc(service.find_gig, gig)
    patch = {k: v for k, v in {"title": title, "date": date_iso, "venue": venue, "status": status, "notes": notes}.items() if v}
    if patch:
        g = _svc(service.update_gig, g["id"], patch)
    if fee is not None:
        _svc(service.update_variant, g["active_variant_id"], {"fee": fee})
        g = _svc(service.get_gig, g["id"])
    return _gig_detail(g)


@mcp.tool
def set_line(gig: str, role: str, amount: int | None = None, musician: str = "", kind: str = "musician",
             note: str = "") -> str:
    """Einen Posten der aktiven Variante setzen: existiert die Rolle schon, wird sie aktualisiert, sonst neu angelegt.
    So lässt sich eine Planung Zeile für Zeile aufbauen ("Bass Ben 190", "PA 700 als Kosten").

    Args:
        gig: Gig-id oder Titel (+ Jahr).
        role: Rolle bzw. Kostenposten, z. B. "Bass", "Sängerin", "FOH", "PA", "Licht".
        amount: Betrag netto in Euro. None = unverändert (bei neuem Posten: Standardgage des Musikers, sonst 0).
        musician: Name der Person (muss unter list_musicians existieren). Leer = unverändert / unbesetzt.
        kind: musician (Default) oder cost für Nebenkosten.
        note: Notiz zur Zeile.
    """
    g = _svc(service.find_gig, gig)
    active = next(v for v in g["variants"] if v["id"] == g["active_variant_id"])
    m = _svc(service.find_musician, musician) if musician.strip() else None
    if musician.strip() and m is None:
        raise ToolError(f"Musiker {musician!r} nicht gefunden – erst mit upsert_musician anlegen.")
    existing = next((it for it in active["items"] if it["role"].lower() == role.strip().lower()), None)
    patch: dict = {}
    if amount is not None:
        patch["amount"] = amount
    if m is not None:
        patch["musician_id"] = m["id"]
    if note:
        patch["note"] = note
    if existing:
        item = _svc(service.update_item, existing["id"], patch)
        verb = "aktualisiert"
    else:
        patch.setdefault("amount", m["default_fee"] if m else 0)
        patch.update({"kind": kind, "role": role.strip()})
        if kind == "cost" and m is None and musician:
            patch["label"] = musician
        item = _svc(service.create_item, active["id"], patch)
        verb = "angelegt"
    g = _svc(service.get_gig, g["id"])
    t = next(v for v in g["variants"] if v["id"] == g["active_variant_id"])["totals"]
    who = item["musician_name"] or item["label"] or "unbesetzt"
    return f"Posten {verb}: {item['role']} · {who} · {_eur(item['amount'])} (id {item['id']}). Rest jetzt {_eur(t['rest'])}."


@mcp.tool
def remove_line(gig: str, role: str) -> str:
    """Einen Posten aus der aktiven Variante entfernen.

    Args:
        gig: Gig-id oder Titel (+ Jahr).
        role: Rolle/Posten, der entfernt werden soll.
    """
    g = _svc(service.find_gig, gig)
    active = next(v for v in g["variants"] if v["id"] == g["active_variant_id"])
    existing = next((it for it in active["items"] if it["role"].lower() == role.strip().lower()), None)
    if not existing:
        raise ToolError(f"Kein Posten {role!r} in {g['title']}.")
    _svc(service.delete_item, existing["id"])
    return f"Posten {existing['role']} entfernt."


@mcp.tool
def mark(gig: str, who: str, info: str = "", invoice: str = "", paid: str = "") -> str:
    """Häkchen an einem Posten setzen: Musiker informiert, Rechnung erhalten, Geld überwiesen.

    Args:
        gig: Gig-id oder Titel (+ Jahr).
        who: Musikername ODER Rolle ODER Posten-id – z. B. "Ben", "Bass", "41". "alle" = jeder zählbare Posten.
        info: open | done | na – Musiker über die Gage informiert?
        invoice: open | done | na – Rechnung liegt vor?
        paid: open | done | na – überwiesen?
    """
    g = _svc(service.find_gig, gig)
    active = next(v for v in g["variants"] if v["id"] == g["active_variant_id"])
    patch = {k: v for k, v in {"info": info, "invoice": invoice, "paid": paid}.items() if v}
    if not patch:
        raise ToolError("Mindestens eines von info/invoice/paid angeben.")
    q = who.strip().lower()
    if q == "alle":
        targets = [it for it in active["items"] if it["amount"] > 0 and it["paid"] != "na"]
    elif q.isdigit():
        targets = [it for it in active["items"] if it["id"] == int(q)]
    else:
        targets = [it for it in active["items"] if (it["musician_name"] or "").lower() == q or it["role"].lower() == q
                   or it["label"].lower() == q]
        if not targets:
            targets = [it for it in active["items"] if q in (it["musician_name"] or "").lower() or q in it["role"].lower()]
    if not targets:
        raise ToolError(f"Kein Posten passt zu {who!r} in {g['title']}.")
    for it in targets:
        _svc(service.update_item, it["id"], patch)
        _svc(service.log_event, g["id"], it["id"], "status",
             f"{it['musician_name'] or it['role']}: " + ", ".join(f"{k}={v}" for k, v in patch.items()))
    names = ", ".join(it["musician_name"] or it["label"] or it["role"] for it in targets)
    return f"Gesetzt für {names}: " + ", ".join(f"{k}={v}" for k, v in patch.items()) + "\n\n" + _gig_detail(_svc(service.get_gig, g["id"]))


@mcp.tool
def upsert_musician(name: str, role: str = "", default_fee: int | None = None, email: str = "", phone: str = "",
                    iban: str = "", notes: str = "", is_self: bool | None = None) -> str:
    """Musiker anlegen oder Stammdaten ergänzen (Rolle, Standardgage, E-Mail, Telefon, IBAN). Leere Felder bleiben unverändert.

    Args:
        name: Vorname/Spitzname, wie er in den Gigs steht, z. B. "Ben".
        role: Instrument/Funktion, z. B. "Bass", "Gesang", "FOH".
        default_fee: Übliche Gage netto – wird bei neuen Posten vorgeschlagen.
        email: E-Mail für Erinnerungen.
        phone: Telefon/WhatsApp (international, z. B. +49151…).
        iban: Für die spätere Überweisungs-Automatik.
        notes: Freitext.
        is_self: true = diese Person ist die Bandleitung selbst: ihre Posten haben keine Häkchen
            (kein Informieren, keine Rechnung, keine Überweisung) und zählen als „Mein Anteil" in stats.
    """
    patch = {k: v for k, v in {"role": role, "email": email, "phone": phone, "iban": iban, "notes": notes}.items() if v}
    if default_fee is not None:
        patch["default_fee"] = default_fee
    if is_self is not None:
        patch["is_self"] = is_self
    m = _svc(service.find_musician, name)
    if m:
        patch["active"] = True  # ein früher deaktivierter Musiker wird durch Upsert wieder aktiv
        m = _svc(service.update_musician, m["id"], patch)
        return f"Aktualisiert: [{m['id']}] {m['name']} — {m['role']} · Standard {_eur(m['default_fee'])}"
    m = _svc(service.create_musician, {"name": name, **patch})
    return f"Angelegt: [{m['id']}] {m['name']} — {m['role']} · Standard {_eur(m['default_fee'])}"


# --------------------------------------------------------------------------- #
# Erinnerungen (Versand macht das Chat-Modell über Mail/WhatsApp-Konnektoren)
# --------------------------------------------------------------------------- #


@mcp.tool
def reminder_targets(gig: str, what: str = "invoice") -> str:
    """Wer muss für einen Gig noch erinnert werden – mit Kontaktdaten und Textvorschlag.
    Den Versand übernimmst DU über den passenden Konnektor (Mail, WhatsApp); danach `log_reminder` aufrufen.

    Args:
        gig: Gig-id oder Titel (+ Jahr).
        what: invoice = Rechnung fehlt noch (Default) · info = noch nicht über Gage informiert.
    """
    if what not in ("invoice", "info"):
        raise ToolError("what muss invoice oder info sein")
    g = _svc(service.find_gig, gig)
    active = next(v for v in g["variants"] if v["id"] == g["active_variant_id"])
    targets = [it for it in active["items"] if it["kind"] == "musician" and it["amount"] > 0 and it[what] == "open" and it["musician_id"]]
    if not targets:
        return f"Niemand zu erinnern – bei {g['title']} ist {what} überall erledigt oder entfällt."
    musicians = {m["id"]: m for m in _svc(service.list_musicians, include_inactive=True)}
    when = g["date"] and date.fromisoformat(g["date"]).strftime("%d.%m.%Y") or "—"
    sign = f" {SENDER_NAME}" if SENDER_NAME else ""
    out = [f"**{g['title']}** ({when}) – {len(targets)} Personen, {what}:", ""]
    for it in targets:
        m = musicians[it["musician_id"]]
        contact = " · ".join(f"{k}: {v}" for k, v in (("Mail", m["email"]), ("Tel", m["phone"])) if v) or "KEINE Kontaktdaten hinterlegt"
        if what == "invoice":
            text = (f"Hi {m['name']}, für {g['title']} am {when} fehlt mir noch deine Rechnung über {_eur(it['amount'])} netto "
                    f"({it['role']}). Schickst du sie mir, dann überweise ich direkt. Danke!{sign}")
        else:
            text = (f"Hi {m['name']}, kurz zur Info für {g['title']} am {when}: deine Gage ist {_eur(it['amount'])} netto "
                    f"({it['role']}). Rechnung bitte nach dem Gig an mich. Danke!{sign}")
        out.append(f"- **{m['name']}** ({it['role']}, {_eur(it['amount'])}) · {contact} · Posten-id {it['id']}\n  Vorschlag: „{text}“")
    return "\n".join(out)


@mcp.tool
def log_reminder(gig: str, who: str, channel: str, mark_informed: bool = False) -> str:
    """Protokolliert, dass eine Erinnerung verschickt wurde (nach dem Versand über einen anderen Konnektor).

    Args:
        gig: Gig-id oder Titel (+ Jahr).
        who: Musikername oder Posten-id.
        channel: z. B. "mail", "whatsapp", "telefon".
        mark_informed: Zusätzlich info=done setzen (wenn es die Gagen-Info war).
    """
    g = _svc(service.find_gig, gig)
    active = next(v for v in g["variants"] if v["id"] == g["active_variant_id"])
    q = who.strip().lower()
    it = next((i for i in active["items"] if str(i["id"]) == q or (i["musician_name"] or "").lower() == q), None)
    if it is None:
        raise ToolError(f"Kein Posten passt zu {who!r} in {g['title']}.")
    _svc(service.log_event, g["id"], it["id"], "reminder", f"{it['musician_name'] or it['role']} per {channel} erinnert")
    if mark_informed:
        _svc(service.update_item, it["id"], {"info": "done"})
    return f"Protokolliert: {it['musician_name'] or it['role']} per {channel} erinnert" + (" · info=done" if mark_informed else "")


@mcp.tool
def gig_history(gig: str) -> str:
    """Protokoll eines Gigs: wann welches Häkchen gesetzt, wer wann erinnert wurde.

    Args:
        gig: Gig-id oder Titel (+ Jahr).
    """
    g = _svc(service.find_gig, gig)
    rows = _svc(service.list_events, g["id"])
    if not rows:
        return f"Noch kein Protokoll für {g['title']}."
    return "\n".join(f"{r['ts']} · {r['kind']} · {r['text']}" for r in rows)
