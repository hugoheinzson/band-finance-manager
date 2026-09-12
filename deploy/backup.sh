#!/usr/bin/env bash
# Tägliches Backup des Band Managers: SQLite-Snapshot + CSV-Export → lokal + rclone (verschlüsselt).
#
# Konfiguration über die .env des Projekts (siehe .env.example, Abschnitt Backup):
#   BANDMANAGER_DATA               Datenverzeichnis (enthält band-manager.db)
#   BANDMANAGER_BACKUP_DIR         lokales Backup-Verzeichnis        (Default: $BANDMANAGER_DATA/backup)
#   BANDMANAGER_RCLONE_REMOTE      rclone-Ziel, z. B. dropbox-crypt:band-manager   (leer = kein Upload)
#   BANDMANAGER_PUBLIC_EXPORT_DIR  lokaler Ordner für CSVs OHNE Kontaktdaten (Default: $BACKUP_DIR/csv-public)
#   BANDMANAGER_PUBLIC_RCLONE_REMOTE  unverschlüsseltes rclone-Ziel für diese CSVs, z. B. dropbox:MeineBand/Band Manager (leer = aus)
#   BANDMANAGER_MAIL_CONFIG        SMTP-Config für Fehler-Mails (leer = nur Log)
#   BANDMANAGER_KEEP_DAILY         lokale/tägliche Snapshots behalten (Default 14)
#   BANDMANAGER_KEEP_MONTHLY_DAYS  monatliche Snapshots im Remote behalten (Default 730 Tage)
#
# Ablauf: Snapshot → integrity_check → CSV (voll + public) → Config-Kopie → lokale Rotation
#         → rclone sync daily/ csv/ config/ → am 1. des Monats Kopie nach monthly/
#         → Snapshot aus dem Remote zurücklesen und erneut prüfen (Round-Trip-Test).
# Jeder Fehler beendet das Skript mit Exit ≠ 0 und schickt (falls konfiguriert) eine Mail.

set -euo pipefail

PROJECT_DIR="${BANDMANAGER_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_DIR"
set -a; [ -f .env ] && . ./.env; set +a
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

DATA_DIR="${BANDMANAGER_DATA:-$HOME/band-manager-data}"; DATA_DIR="${DATA_DIR/#\~/$HOME}"
BACKUP_DIR="${BANDMANAGER_BACKUP_DIR:-$DATA_DIR/backup}"; BACKUP_DIR="${BACKUP_DIR/#\~/$HOME}"
REMOTE="${BANDMANAGER_RCLONE_REMOTE:-}"
PUBLIC_DIR="${BANDMANAGER_PUBLIC_EXPORT_DIR:-$BACKUP_DIR/csv-public}"; PUBLIC_DIR="${PUBLIC_DIR/#\~/$HOME}"
PUBLIC_REMOTE="${BANDMANAGER_PUBLIC_RCLONE_REMOTE:-}"
KEEP_DAILY="${BANDMANAGER_KEEP_DAILY:-14}"
KEEP_MONTHLY_DAYS="${BANDMANAGER_KEEP_MONTHLY_DAYS:-730}"
export BANDMANAGER_DB="$DATA_DIR/band-manager.db"

STAMP=$(date +%Y%m%d_%H%M%S)
LOG="$BACKUP_DIR/backup.log"
PY="uv run --no-sync python scripts/backup.py"
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/csv" "$BACKUP_DIR/config"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
fail() {
    log "FEHLER: $*"
    $PY notify "Band Manager Backup FEHLGESCHLAGEN ($(hostname))" "$(date) — $*

Letzte Logzeilen:
$(tail -n 25 "$LOG")" >/dev/null 2>&1 || log "(Fehler-Mail konnte nicht gesendet werden)"
    exit 1
}
trap 'fail "Abbruch in Zeile $LINENO"' ERR

log "=== Backup Start $STAMP ==="
[ -f "$BANDMANAGER_DB" ] || fail "Datenbank $BANDMANAGER_DB nicht gefunden"

# 1. Snapshot + Prüfung
SNAP="$BACKUP_DIR/daily/band-manager_${STAMP}.db"
RESULT=$($PY snapshot "$SNAP") || fail "Snapshot fehlgeschlagen: $RESULT"
GIGS=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['counts']['gigs'])")
gzip -f "$SNAP"; SNAP="$SNAP.gz"
log "Snapshot ok: $(basename "$SNAP") · $GIGS Gigs · $(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['counts'])")"

# 2. CSV-Exporte (voll = mit Kontaktdaten/IBAN → nur verschlüsselt; public = ohne)
$PY export-csv "$BACKUP_DIR/csv" >/dev/null || fail "CSV-Export fehlgeschlagen"
log "CSV exportiert nach $BACKUP_DIR/csv"
mkdir -p "$PUBLIC_DIR"
$PY export-csv "$PUBLIC_DIR" --public >/dev/null || fail "Public-CSV-Export fehlgeschlagen"
printf 'Automatischer Export des Band Managers vom %s.\nOhne Kontaktdaten/IBAN. Vollständige Sicherung liegt verschlüsselt im rclone-Remote.\n' "$(date '+%d.%m.%Y %H:%M')" > "$PUBLIC_DIR/README.txt"
log "Public-CSV nach $PUBLIC_DIR"
if [ -n "$PUBLIC_REMOTE" ]; then
    command -v rclone >/dev/null || fail "rclone nicht gefunden"
    rclone sync "$PUBLIC_DIR/" "$PUBLIC_REMOTE/" --log-file="$LOG" --log-level NOTICE
    log "Public-CSV nach $PUBLIC_REMOTE"
fi

# 3. Konfiguration, die ein Restore auf frischem System braucht
cp -f .env "$BACKUP_DIR/config/env" 2>/dev/null || true
cp -f import-config.json "$BACKUP_DIR/config/import-config.json" 2>/dev/null || true
chmod 600 "$BACKUP_DIR"/config/* 2>/dev/null || true

# 4. Lokale Rotation
ls -1t "$BACKUP_DIR"/daily/*.db.gz | tail -n +"$((KEEP_DAILY + 1))" | xargs -r rm -f
log "Lokal: $(ls -1 "$BACKUP_DIR"/daily/*.db.gz | wc -l) Snapshots (max. $KEEP_DAILY)"

# 5. Upload
if [ -n "$REMOTE" ]; then
    command -v rclone >/dev/null || fail "rclone nicht gefunden"
    rclone sync "$BACKUP_DIR/daily/"  "$REMOTE/daily/"  --log-file="$LOG" --log-level NOTICE
    rclone sync "$BACKUP_DIR/csv/"    "$REMOTE/csv/"    --log-file="$LOG" --log-level NOTICE
    rclone sync "$BACKUP_DIR/config/" "$REMOTE/config/" --log-file="$LOG" --log-level NOTICE
    if [ "$(date +%d)" = "01" ] || [ "${BANDMANAGER_FORCE_MONTHLY:-0}" = "1" ]; then
        rclone copy "$SNAP" "$REMOTE/monthly/" --log-file="$LOG" --log-level NOTICE
        rclone delete "$REMOTE/monthly/" --min-age "${KEEP_MONTHLY_DAYS}d" --log-file="$LOG" --log-level NOTICE || true
        log "Monats-Snapshot nach $REMOTE/monthly/"
    fi
    log "Upload nach $REMOTE fertig"

    # 6. Round-Trip: genau diese Datei aus dem Remote zurücklesen und prüfen
    TMP=$(mktemp -d)
    rclone copyto "$REMOTE/daily/$(basename "$SNAP")" "$TMP/roundtrip.db.gz" --log-file="$LOG" --log-level NOTICE
    CHECK=$($PY verify "$TMP/roundtrip.db.gz" --expect-gigs "$GIGS") || { rm -rf "$TMP"; fail "Round-Trip-Prüfung fehlgeschlagen: $CHECK"; }
    rm -rf "$TMP"
    log "Round-Trip ok: Remote-Kopie integer, $GIGS Gigs"
fi

log "=== Backup fertig ==="
