"""Band Manager – ein Prozess, drei Aufgaben:

  /api/*   REST für die PWA            (ohne Auth – nur im Tailnet erreichbar)
  /mcp     MCP (streamable HTTP)       (Bearer BANDMANAGER_API_TOKEN – direkt oder über einen Aggregator)
  /        die PWA aus ./web           (statisch, kein Build-Step)

Start: `uvicorn app.main:app --host 127.0.0.1 --port 8019`
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import router as api_router
from .mcp_server import mcp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("band-manager")

WEB_DIR = Path(os.getenv("BANDMANAGER_WEB", Path(__file__).resolve().parent.parent / "web"))

# FastMCP liefert eine eigene Starlette-App mit Route "/mcp" und einem Lifespan
# (Session-Manager). Wir übernehmen ihre Routen in die FastAPI-App, damit alles
# auf EINEM Port läuft und der Aggregator weiterhin ".../mcp" anspricht.
mcp_app = mcp.http_app(path="/mcp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    logger.info("DB: %s · Web: %s", db.DB_PATH, WEB_DIR)
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(title="Band Manager", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(api_router)
app.router.routes.extend(mcp_app.routes)

# ASGI-Middleware der MCP-App (falls FastMCP welche registriert) mitnehmen
for m in getattr(mcp_app, "user_middleware", []):
    app.add_middleware(m.cls, *m.args, **m.kwargs)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Service Worker darf nie lange gecacht werden, sonst bleiben alte App-Shells hängen
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    logger.warning("Web-Verzeichnis %s fehlt – nur API/MCP aktiv", WEB_DIR)
