# Backup & restore

`deploy/backup.sh` runs daily (systemd user timer, see `*.service` / `*.timer` in this
folder) and produces, per run:

| What | Where | Contains personal data? |
|---|---|---|
| `daily/band-manager_<stamp>.db.gz` – consistent SQLite snapshot (backup API, `PRAGMA integrity_check`) | local `BANDMANAGER_BACKUP_DIR` + rclone remote | yes → remote must be an encrypted (`crypt`) remote |
| `csv/gigs.csv`, `csv/posten.csv`, `csv/musiker.csv` – flat exports, `;`-separated, UTF‑8 with BOM (opens in Excel/Numbers) | same | yes (musiker.csv has contacts/IBAN) |
| `gigs.csv`, `posten.csv` **without** contacts | `BANDMANAGER_PUBLIC_EXPORT_DIR` (local) and, if set, `BANDMANAGER_PUBLIC_RCLONE_REMOTE` (a plain, unencrypted cloud folder) | no |
| `config/env`, `config/import-config.json` | remote only | yes (API token, names) |
| `monthly/…` – copy of the 1st-of-month snapshot, pruned after `BANDMANAGER_KEEP_MONTHLY_DAYS` | remote | yes |

After uploading, the script downloads the snapshot it just uploaded and verifies it again
(integrity + gig count) – a failed round trip fails the run and, if `BANDMANAGER_MAIL_CONFIG`
is set, sends a mail.

## Install the timer

```bash
cp deploy/band-manager-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now band-manager-backup.timer
systemctl --user start band-manager-backup.service      # first run now
tail -f ~/band-manager-data/backup/backup.log
```

`Persistent=true` matters on a machine that is switched off at night: a missed 09:10 run
fires on the next boot. Adjust `OnCalendar` to a time the machine is normally on.

## Restore

```bash
deploy/restore.sh ~/band-manager-data/backup/daily/band-manager_20260912_091000.db.gz
deploy/restore.sh remote:latest        # newest snapshot from the rclone remote
```

The script verifies the snapshot, stops the container, keeps the current database as
`band-manager.db.pre-restore-<stamp>`, swaps the file, restarts and compares the gig count
reported by `/api/health` with the snapshot.

## Verify a backup from another machine

You need `rclone` and the two remote definitions (`[dropbox]` + `[dropbox-crypt]` in the
example below) from the server's `~/.config/rclone/rclone.conf`. The crypt remote's
password is stored obscured there; `rclone reveal <obscured>` prints it, or copy the
section verbatim – both work.

```bash
# list what is there (file names are encrypted on Dropbox, rclone decrypts them for you)
rclone lsl dropbox-crypt:band-manager/daily/

# fetch the newest snapshot and check it without the app
rclone copyto "dropbox-crypt:band-manager/daily/$(rclone lsf dropbox-crypt:band-manager/daily/ | sort | tail -1)" ./latest.db.gz
gunzip -k latest.db.gz
python3 -c "import sqlite3; c=sqlite3.connect('latest.db'); print(c.execute('pragma integrity_check').fetchone()[0], c.execute('select count(*) from gigs').fetchone()[0], 'gigs')"
# or: sqlite3 latest.db 'pragma integrity_check; select count(*) from gigs;'

# the CSVs are plain text once decrypted
rclone cat dropbox-crypt:band-manager/csv/gigs.csv | head
```

Without rclone, the unencrypted `gigs.csv` / `posten.csv` in the public export folder
(Dropbox app on the phone) show at a glance whether yesterday's export happened and what it
contains – they carry no contact data, so a look is harmless.
