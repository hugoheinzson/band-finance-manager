"""REST-API (`/api/*`) für die PWA. Dünne Schicht über `service` – Vertrag in API.md."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from . import config, service
from .db import tx

router = APIRouter(prefix="/api")


class Body(BaseModel, extra="allow"):
    """Freies JSON-Objekt – die Feldprüfung macht `service`, damit REST und MCP identisch validieren."""

    def data(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


def _run(fn, *args, **kwargs):
    try:
        with tx() as conn:
            return fn(conn, *args, **kwargs)
    except service.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except service.Invalid as exc:
        raise HTTPException(400, str(exc)) from exc


# ---- Gigs -----------------------------------------------------------------


@router.get("/gigs")
def list_gigs(year: int | None = None, status: str | None = None):
    return _run(service.list_gigs, year=year, status=status)


@router.post("/gigs", status_code=201)
def create_gig(body: Body):
    return _run(service.create_gig, body.data())


@router.get("/gigs/{gig_id}")
def get_gig(gig_id: int):
    return _run(service.get_gig, gig_id)


@router.patch("/gigs/{gig_id}")
def update_gig(gig_id: int, body: Body):
    return _run(service.update_gig, gig_id, body.data())


@router.delete("/gigs/{gig_id}", status_code=204)
def delete_gig(gig_id: int):
    _run(service.delete_gig, gig_id)
    return Response(status_code=204)


@router.post("/gigs/{gig_id}/duplicate", status_code=201)
def duplicate_gig(gig_id: int, body: Body | None = None):
    d = body.data() if body else {}
    return _run(service.duplicate_gig, gig_id, title=d.get("title"), date_=d.get("date"), status=d.get("status") or "angebot")


@router.get("/gigs/{gig_id}/events")
def gig_events(gig_id: int):
    return _run(service.list_events, gig_id)


# ---- Varianten ------------------------------------------------------------


@router.post("/gigs/{gig_id}/variants", status_code=201)
def create_variant(gig_id: int, body: Body):
    d = body.data()
    if "fee" not in d:
        raise HTTPException(400, "fee fehlt")
    return _run(service.create_variant, gig_id, d.get("name") or "Variante", d["fee"],
                copy_from_variant_id=d.get("copy_from_variant_id"))


@router.patch("/variants/{variant_id}")
def update_variant(variant_id: int, body: Body):
    return _run(service.update_variant, variant_id, body.data())


@router.delete("/variants/{variant_id}", status_code=204)
def delete_variant(variant_id: int):
    _run(service.delete_variant, variant_id)
    return Response(status_code=204)


# ---- Posten ---------------------------------------------------------------


@router.post("/variants/{variant_id}/items", status_code=201)
def create_item(variant_id: int, body: Body):
    return _run(service.create_item, variant_id, body.data())


@router.post("/variants/{variant_id}/items/reorder")
def reorder_items(variant_id: int, body: Body):
    ids = body.data().get("item_ids") or []
    return _run(service.reorder_items, variant_id, [int(i) for i in ids])


@router.patch("/items/{item_id}")
def update_item(item_id: int, body: Body):
    return _run(service.update_item, item_id, body.data())


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    _run(service.delete_item, item_id)
    return Response(status_code=204)


# ---- Musiker --------------------------------------------------------------


@router.get("/musicians")
def list_musicians(all: bool = False):
    return _run(service.list_musicians, include_inactive=all)


@router.post("/musicians", status_code=201)
def create_musician(body: Body):
    return _run(service.create_musician, body.data())


@router.patch("/musicians/{musician_id}")
def update_musician(musician_id: int, body: Body):
    return _run(service.update_musician, musician_id, body.data())


@router.delete("/musicians/{musician_id}", status_code=204)
def deactivate_musician(musician_id: int):
    _run(service.deactivate_musician, musician_id)
    return Response(status_code=204)


# ---- Auswertung / Health --------------------------------------------------


@router.get("/stats")
def stats():
    return _run(service.stats)


@router.get("/config")
def get_config():
    return config.public()


@router.get("/health")
def health():
    with tx() as conn:
        n = conn.execute("SELECT COUNT(*) FROM gigs").fetchone()[0]
    return {"status": "ok", "gigs": n}
