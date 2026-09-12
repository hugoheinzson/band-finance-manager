# Band Manager – API-Vertrag

Basis: `http://127.0.0.1:8019` (Zugriff von außen z. B. über Tailscale/Reverse-Proxy).
Alle `/api/*`-Routen: JSON, keine Auth (nur im privaten Netz erreichbar). `/mcp`: Bearer-Token.
Fehler: `{"detail": "…"}` mit 400/404.

## Enums

- **Flag** (`info`, `invoice`, `paid` je Posten): `"open"` | `"done"` | `"na"`
  – `open` = noch zu tun, `done` = erledigt, `na` = entfällt (z. B. Bandleader selbst, FOH ohne Gage).
- **GigStatus**: `"vorlage"` | `"angebot"` | `"bestaetigt"` | `"abgerechnet"` | `"abgesagt"`
- **ItemKind**: `"musician"` (Gage an eine Person) | `"cost"` (Nebenkosten: PA, Licht, Fotograf, Bandkasse …)

## Objekte

```jsonc
Musician {
  "id": 3, "name": "Ben", "role": "Bass", "default_fee": 180,
  "email": "", "phone": "", "iban": "", "notes": "", "active": true,
  "is_self": false,          // „das bin ich" (Bandleitung) – höchstens eine Person; ihre Posten haben immer info/invoice/paid = "na"
  "gig_count": 19            // nur lesend, Anzahl Gigs mit Posten dieser Person
}

LineItem {
  "id": 41, "variant_id": 7, "kind": "musician",
  "role": "Bass",            // bei kind=cost: Bezeichnung des Postens, z. B. "PA"
  "musician_id": 3,          // null bei cost oder noch unbesetzt
  "musician_name": "Ben",  // abgeleitet, null wenn unbesetzt
  "label": "",               // bei cost: wer/woher, z. B. "Verleih"
  "amount": 180,             // netto, ganze Euro (int)
  "info": "done", "invoice": "open", "paid": "open",
  "note": "600 EUR abgesprochen",
  "sort_order": 3
}

Totals {
  "fee": 2500,               // Gage der Variante
  "musicians": 2205,         // Summe kind=musician
  "costs": 280,              // Summe kind=cost
  "rest": 15,                // fee - musicians - costs (kann negativ sein)
  "items": 9,                // zählbare Posten: amount>0 und paid != "na"
  "info_done": 8, "invoice_done": 4, "paid_done": 5
}

Variant {
  "id": 7, "gig_id": 12, "name": "Volle Besetzung", "fee": 2500, "sort_order": 0,
  "items": [LineItem, …],    // nur im Gig-Detail
  "totals": Totals
}

Gig {                         // Detail
  "id": 12, "title": "Hochzeit Miller", "date": "2026-07-18" | null, "venue": "Hochzeit",
  "status": "bestaetigt", "notes": "…",
  "active_variant_id": 7,
  "created_at": "2026-09-12T15:00:00", "updated_at": "…",
  "variants": [Variant, …]   // sortiert nach sort_order; die aktive enthält die Status-Häkchen
}

GigListItem {                 // Liste
  "id": 12, "title": "Hochzeit Miller", "date": "2026-07-18" | null, "venue": "…", "status": "bestaetigt",
  "year": 2026,               // aus date, sonst aus created_at
  "active_variant_id": 7, "variant_count": 2,
  "totals": Totals            // der aktiven Variante
}
```

## Endpunkte

### Gigs
| Methode | Pfad | Body / Query | Antwort |
|---|---|---|---|
| GET | `/api/gigs` | `?year=2026&status=bestaetigt` (optional) | `[GigListItem]`, neueste zuerst |
| POST | `/api/gigs` | `{title, date?, venue?, status?="angebot", fee, notes?, template_gig_id?}` | `Gig` (201). Mit `template_gig_id`: Posten der aktiven Variante des Template-Gigs kopieren, alle Flags auf `open`, Beträge übernehmen. Ohne Template: Variante „Standard" mit leerer Postenliste. |
| GET | `/api/gigs/{id}` | | `Gig` |
| PATCH | `/api/gigs/{id}` | `{title?, date?, venue?, status?, notes?, active_variant_id?}` | `Gig` |
| DELETE | `/api/gigs/{id}` | | 204 |
| POST | `/api/gigs/{id}/duplicate` | `{title?, date?, status?="angebot"}` | `Gig` (201) – alle Varianten kopiert, Flags auf `open` |

### Varianten (Budget-Szenarien eines Gigs)
| POST | `/api/gigs/{id}/variants` | `{name, fee, copy_from_variant_id?}` | `Variant` (201); mit copy: Posten (Rolle, Musiker, Betrag) kopieren, Flags `open` |
| PATCH | `/api/variants/{id}` | `{name?, fee?}` | `Variant` |
| DELETE | `/api/variants/{id}` | | 204; 400 wenn letzte Variante oder aktive Variante |

### Posten
| POST | `/api/variants/{id}/items` | `{kind, role, musician_id?, label?, amount=0, note?, info?, invoice?, paid?}` | `LineItem` (201), sort_order = ans Ende |
| PATCH | `/api/items/{id}` | beliebige Felder aus LineItem außer id/variant_id | `LineItem` |
| DELETE | `/api/items/{id}` | | 204 |
| POST | `/api/variants/{id}/items/reorder` | `{item_ids: [41, 42, …]}` | `[LineItem]` |

### Musiker
| GET | `/api/musicians` | `?all=true` zeigt auch inaktive | `[Musician]` sortiert nach Name |
| POST | `/api/musicians` | `{name, role?, default_fee?, email?, phone?, iban?, notes?}` | `Musician` (201) |
| PATCH | `/api/musicians/{id}` | Felder | `Musician` |
| DELETE | `/api/musicians/{id}` | | 204 – setzt `active=false` (Historie bleibt) |

### Auswertung
`GET /api/stats` →
```jsonc
{
  "self_musician_id": 1,                                                                         // null, wenn niemand als „das bin ich" markiert ist
  "years": [ { "year": 2025, "gigs": 4, "fee_total": 8750, "self_total": 1150, "rest_total": 20, "open_items": 1 }, … ],  // nur status bestaetigt|abgerechnet; self_total = Summe eigener Posten
  "open_payments": [ { "gig_id": 12, "gig_title": "Hochzeit Miller", "gig_date": "2026-07-18",
                       "item_id": 41, "who": "Ben", "role": "Bass", "amount": 190,
                       "info": "done", "invoice": "open" }, … ],                                  // paid == "open", amount > 0, gig bestaetigt|abgerechnet
  "templates": [ GigListItem, … ]                                                                // status == "vorlage"
}
```

### Sonstiges
- `GET /api/config` → `{"app_name":"Band Manager","band_name":"…","initials":"…"}` (aus Env, fürs Branding im Frontend)
- `GET /api/health` → `{"status":"ok","gigs":19}`
- `GET /` liefert die PWA (`web/index.html`), statische Dateien darunter.
