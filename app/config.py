"""Band-spezifische Einstellungen – ausschließlich über Umgebungsvariablen, damit
der Code selbst keine persönlichen Daten enthält (siehe .env.example)."""

from __future__ import annotations

import os

APP_NAME = os.getenv("BANDMANAGER_APP_NAME", "Band Manager")
BAND_NAME = os.getenv("BANDMANAGER_BAND_NAME", "Meine Band")
# Name, mit dem Erinnerungs-Texte unterschrieben werden (der Bandleader / Kassenwart)
SENDER_NAME = os.getenv("BANDMANAGER_SENDER_NAME", "")
API_TOKEN = os.getenv("BANDMANAGER_API_TOKEN", "")


def initials(name: str) -> str:
    parts = [p for p in name.replace("-", " ").split() if p and p[0].isalnum()]
    stop = {"the", "die", "der", "das", "and", "und", "&"}
    core = [p for p in parts if p.lower() not in stop] or parts
    return "".join(p[0] for p in core[:2]).upper() or "GK"


def public() -> dict:
    """Was das Frontend wissen darf."""
    return {"app_name": APP_NAME, "band_name": BAND_NAME, "initials": initials(BAND_NAME)}
