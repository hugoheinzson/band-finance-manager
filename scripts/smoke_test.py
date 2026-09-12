"""End-to-End-Smoke-Test gegen einen laufenden Server (REST + MCP).

    BANDMANAGER_API_TOKEN=… uv run python scripts/smoke_test.py [http://127.0.0.1:8019]

Legt einen Test-Gig an, spielt die Schreibpfade durch und räumt wieder auf.
Bricht mit AssertionError ab, sobald etwas nicht stimmt.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8019"
TOKEN = os.environ.get("BANDMANAGER_API_TOKEN", "")


def rest() -> None:
    c = httpx.Client(base_url=BASE, timeout=10)
    assert c.get("/api/health").json()["status"] == "ok"
    musicians = c.get("/api/musicians").json()
    assert musicians, "keine Musiker – erst importieren oder anlegen"
    m = musicians[0]

    # Gig anlegen (leer), Posten, Häkchen
    g = c.post("/api/gigs", json={"title": "Smoke-Test", "date": "2030-01-01", "fee": 1000, "venue": "Test"}).json()
    gid, vid = g["id"], g["active_variant_id"]
    assert g["status"] == "angebot" and len(g["variants"]) == 1
    it = c.post(f"/api/variants/{vid}/items", json={"kind": "musician", "role": "Bass", "musician_id": m["id"], "amount": 200}).json()
    cost = c.post(f"/api/variants/{vid}/items", json={"kind": "cost", "role": "PA", "label": "Verleih", "amount": 300}).json()
    g = c.get(f"/api/gigs/{gid}").json()
    t = g["variants"][0]["totals"]
    assert (t["musicians"], t["costs"], t["rest"], t["items"]) == (200, 300, 500, 2), t
    it = c.patch(f"/api/items/{it['id']}", json={"paid": "done", "invoice": "done"}).json()
    assert it["paid"] == "done" and it["musician_name"] == m["name"]
    assert c.patch(f"/api/items/{it['id']}", json={"paid": "kaputt"}).status_code == 400
    assert c.patch(f"/api/items/{cost['id']}", json={"paid": "na"}).json()["paid"] == "na"
    t = c.get(f"/api/gigs/{gid}").json()["variants"][0]["totals"]
    assert (t["items"], t["paid_done"]) == (1, 1), t

    # Variante kopieren, aktivieren, löschen
    v2 = c.post(f"/api/gigs/{gid}/variants", json={"name": "Klein", "fee": 700, "copy_from_variant_id": vid}).json()
    assert len(v2["items"]) == 2 and all(i["paid"] in ("open", "na") for i in v2["items"]), "Flags müssen zurückgesetzt sein"
    assert c.delete(f"/api/variants/{vid}").status_code == 400, "aktive Variante darf nicht löschbar sein"
    g = c.patch(f"/api/gigs/{gid}", json={"active_variant_id": v2["id"], "status": "bestaetigt"}).json()
    assert g["active_variant_id"] == v2["id"]
    assert c.delete(f"/api/variants/{vid}").status_code == 204
    lst = [x for x in c.get("/api/gigs?year=2030").json() if x["id"] == gid][0]
    assert lst["totals"]["fee"] == 700 and lst["variant_count"] == 1

    # offene Zahlungen sehen den Posten (bestaetigt, paid=open)
    op = c.get("/api/stats").json()["open_payments"]
    assert any(o["gig_id"] == gid and o["who"] == m["name"] for o in op), "Posten fehlt in open_payments"

    # Duplizieren + aus Vorlage anlegen
    d = c.post(f"/api/gigs/{gid}/duplicate", json={"title": "Smoke-Kopie"}).json()
    assert d["status"] == "angebot" and d["variants"][0]["items"][0]["paid"] in ("open", "na")
    f = c.post("/api/gigs", json={"title": "Smoke-Vorlage", "fee": 900, "template_gig_id": gid}).json()
    assert len(f["variants"][0]["items"]) == 2

    for x in (gid, d["id"], f["id"]):
        assert c.delete(f"/api/gigs/{x}").status_code == 204
    assert c.get(f"/api/gigs/{gid}").status_code == 404
    print("REST ok")


async def mcp() -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    async with Client(StreamableHttpTransport(f"{BASE}/mcp", headers=headers)) as client:
        tools = {t.name for t in await client.list_tools()}
        expected = {"list_gigs", "get_gig", "open_payments", "list_musicians", "stats", "create_gig", "update_gig",
                    "set_line", "remove_line", "mark", "upsert_musician", "reminder_targets", "log_reminder", "gig_history"}
        assert expected <= tools, expected - tools

        r = await client.call_tool("upsert_musician", {"name": "Smoke Tester", "role": "Kazoo", "default_fee": 42, "email": "x@example.org"})
        assert "Smoke Tester" in r.data
        r = await client.call_tool("create_gig", {"title": "Smoke-MCP", "fee": 500, "date_iso": "2030-02-02"})
        assert "Smoke-MCP" in r.data
        r = await client.call_tool("set_line", {"gig": "Smoke-MCP", "role": "Kazoo", "musician": "Smoke Tester"})
        assert "42 €" in r.data and "Rest jetzt 458 €" in r.data, r.data
        r = await client.call_tool("set_line", {"gig": "Smoke-MCP", "role": "PA", "amount": 100, "kind": "cost"})
        assert "Rest jetzt 358 €" in r.data, r.data
        await client.call_tool("update_gig", {"gig": "Smoke-MCP", "status": "bestaetigt"})
        r = await client.call_tool("reminder_targets", {"gig": "Smoke-MCP", "what": "info"})
        assert "Smoke Tester" in r.data and "x@example.org" in r.data
        r = await client.call_tool("log_reminder", {"gig": "Smoke-MCP", "who": "Smoke Tester", "channel": "mail", "mark_informed": True})
        assert "info=done" in r.data
        r = await client.call_tool("open_payments", {"gig": "Smoke-MCP"})
        assert "Smoke Tester" in r.data
        r = await client.call_tool("mark", {"gig": "Smoke-MCP", "who": "alle", "invoice": "done", "paid": "done"})
        r = await client.call_tool("open_payments", {"gig": "Smoke-MCP"})
        assert "Alles ausgezahlt" in r.data, r.data
        r = await client.call_tool("gig_history", {"gig": "Smoke-MCP"})
        assert "erinnert" in r.data
        r = await client.call_tool("remove_line", {"gig": "Smoke-MCP", "role": "PA"})
        assert "entfernt" in r.data
        # Aufräumen über REST
        with httpx.Client(base_url=BASE) as c:
            gid = [g for g in c.get("/api/gigs?year=2030").json() if g["title"] == "Smoke-MCP"][0]["id"]
            c.delete(f"/api/gigs/{gid}")
            mid = [m for m in c.get("/api/musicians?all=true").json() if m["name"] == "Smoke Tester"][0]["id"]
            c.delete(f"/api/musicians/{mid}")  # deaktiviert nur; der nächste Lauf reaktiviert per upsert
    print("MCP ok")

    if TOKEN:
        try:
            async with Client(StreamableHttpTransport(f"{BASE}/mcp", headers={"Authorization": "Bearer falsch"})) as client:
                await client.list_tools()
            raise AssertionError("falsches Token wurde akzeptiert")
        except AssertionError:
            raise
        except Exception:
            print("MCP-Auth ok (falsches Token abgewiesen)")


if __name__ == "__main__":
    rest()
    asyncio.run(mcp())
    print("alles grün")
