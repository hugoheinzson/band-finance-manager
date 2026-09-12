#!/usr/bin/env bash
# Restore eines Snapshots in den laufenden Band Manager.
#
#   deploy/restore.sh <snapshot.db[.gz]>          lokale Datei
#   deploy/restore.sh remote:latest               neuesten Snapshot aus BANDMANAGER_RCLONE_REMOTE/daily/ holen
#
# Ablauf: Snapshot prüfen → Container stoppen → aktuelle DB als *.pre-restore sichern
#         → Snapshot einspielen (WAL/SHM entfernen) → Container starten → Health + Gig-Zahl vergleichen.
set -euo pipefail

PROJECT_DIR="${BANDMANAGER_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_DIR"
set -a; [ -f .env ] && . ./.env; set +a
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
DATA_DIR="${BANDMANAGER_DATA:-$HOME/band-manager-data}"; DATA_DIR="${DATA_DIR/#\~/$HOME}"
DB="$DATA_DIR/band-manager.db"
PY="uv run --no-sync python scripts/backup.py"
SRC="${1:?Snapshot-Datei oder remote:latest angeben}"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
if [ "$SRC" = "remote:latest" ]; then
    REMOTE="${BANDMANAGER_RCLONE_REMOTE:?BANDMANAGER_RCLONE_REMOTE nicht gesetzt}"
    NAME=$(rclone lsf "$REMOTE/daily/" | sort | tail -n 1)
    [ -n "$NAME" ] || { echo "kein Snapshot im Remote"; exit 1; }
    echo "hole $NAME aus $REMOTE/daily/"
    rclone copyto "$REMOTE/daily/$NAME" "$TMP/$NAME"
    SRC="$TMP/$NAME"
fi

echo "--- prüfe Snapshot"
BANDMANAGER_DB="$DB" $PY verify "$SRC"
if [[ "$SRC" == *.gz ]]; then gunzip -c "$SRC" > "$TMP/restore.db"; else cp "$SRC" "$TMP/restore.db"; fi
EXPECT=$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('select count(*) from gigs').fetchone()[0])" "$TMP/restore.db")

echo "--- stoppe Container"
docker compose stop >/dev/null
STAMP=$(date +%Y%m%d_%H%M%S)
[ -f "$DB" ] && cp -f "$DB" "$DB.pre-restore-$STAMP" && echo "aktuelle DB gesichert als $(basename "$DB").pre-restore-$STAMP"
rm -f "$DB" "$DB-wal" "$DB-shm"
cp "$TMP/restore.db" "$DB"

echo "--- starte Container"
docker compose up -d >/dev/null
for i in $(seq 1 30); do
    if H=$(curl -sf http://127.0.0.1:8019/api/health 2>/dev/null); then
        GOT=$(echo "$H" | python3 -c "import json,sys; print(json.load(sys.stdin)['gigs'])")
        if [ "$GOT" = "$EXPECT" ]; then echo "Restore ok: $GOT Gigs, Container healthy"; exit 0; fi
        echo "Gig-Zahl weicht ab: erwartet $EXPECT, App meldet $GOT"; exit 1
    fi
    sleep 1
done
echo "Container antwortet nicht – docker compose logs prüfen"; exit 1
