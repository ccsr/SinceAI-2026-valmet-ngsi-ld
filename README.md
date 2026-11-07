# Valmet Industrial Metaverse — NGSI-LD

[![FIWARE](https://img.shields.io/badge/FIWARE-Core-blue)](https://www.fiware.org/)
[![NGSI-LD](https://img.shields.io/badge/NGSI--LD-v1.9.1-red)](https://www.etsi.org/technical-groups/data/)
[![Smart Data Models](https://img.shields.io/badge/Smart%20Data%20Models-Manufacturing-orange)](https://smartdatamodels.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green)](LICENSE)

An open-source NGSI-LD data model and toolchain for industrial asset management in process manufacturing environments (paper mills, chemical plants, utilities). Built for the [Since AI 2026 Hackathon](https://sinceai.ai/hackathon) in partnership with [Valmet](https://www.valmet.com) and [FIWARE Foundation](https://www.fiware.org).

---

## What it does

Converts industrial asset data (from any tabular source) into [NGSI-LD](https://www.etsi.org/deliver/etsi_gs/CIM/001_099/009/01.09.01_60/gs_CIM009v010901p.pdf) entities and serves them through a standards-compliant context broker ([coraine](https://github.com/SEAMWARE/coraine)).

```
Excel / CSV
    │
    ▼
convert.py ──► entities/*.json  (NGSI-LD)
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
    api.py                coraine
    (FastAPI               (NGSI-LD
     convenience           context
     API :8000)            broker :1026)
```

---

## Data model

Six NGSI-LD entity types covering the full asset lifecycle — contributed to [Smart Data Models / Manufacturing](https://smartdatamodels.org/index.php/smart-manufacturing-domain-at-smart-data-models/) as `dataModel.IndustrialAsset`:

| Entity type | Description |
|---|---|
| `IndustrialAsset` | Equipment master (control valves, pumps, motors, transmitters, vibration sensors) |
| `AssetCondition` | AI health score, condition state, recommended next check |
| `TelemetryObservation` | Time-stamped process telemetry with UN/CEFACT unit codes |
| `AssetAlert` | Alarms and events from DCS / CMMS / edge detection |
| `MaintenanceWorkOrder` | Maintenance history (corrective, preventive, predictive) |
| `SceneAnchor` | 3D position + bounding box for industrial metaverse scene placement |

The JSON-LD `@context` is at [`data-model/context.jsonld`](data-model/context.jsonld).  
Schemas for all six types are in [`../dataModel.IndustrialAsset/`](../dataModel.IndustrialAsset/).

---

## Quick start

### Prerequisites

- Docker Desktop
- Python 3.11+

### 1. Prepare your data

Your source data should be an Excel file with these sheets (column names are flexible — edit `convert.py` to match yours):

| Sheet | Maps to |
|---|---|
| `Asset_Master` | `IndustrialAsset` |
| `Current_State` | `AssetCondition` |
| `Telemetry_Sample_3d` | `TelemetryObservation` |
| `Alarms_Events` | `AssetAlert` |
| `Maintenance_History` | `MaintenanceWorkOrder` |
| `Scene_Anchors` | `SceneAnchor` |

### 2. Convert to NGSI-LD

```bash
pip install -r requirements.txt

python3 scripts/convert.py \
  --input your_data.xlsx \
  --output ./entities
```

This writes one JSON file per entity type into `entities/`.

### 3. Start the stack

```bash
docker compose up --build -d
```

Starts two services:
- **valmet-ngsi-ld** (`:8000`) — FastAPI convenience API + serves the `@context`
- **coraine** (`:1026`) — NGSI-LD context broker (in-memory, no DB required)

### 4. Load entities into the broker

```bash
# Load into the 'valmet' tenant
python3 scripts/migrate_tenant.py --tenant valmet
```

### 5. Query

```bash
# All assets
curl -H 'NGSILD-Tenant: valmet' \
  'http://localhost:1026/ngsi-ld/v1/entities?type=IndustrialAsset' | jq

# Single asset
curl -H 'NGSILD-Tenant: valmet' \
  'http://localhost:1026/ngsi-ld/v1/entities/urn:ngsi-ld:IndustrialAsset:ASSET-001' | jq

# Key-values (flat JSON)
curl -H 'NGSILD-Tenant: valmet' \
  'http://localhost:1026/ngsi-ld/v1/entities?type=AssetCondition&options=keyValues' | jq

# Swagger UI (FastAPI)
open http://localhost:8000/docs
```

---

## Project layout

```
valmet-ngsi-ld/
├── data-model/
│   ├── context.jsonld          # JSON-LD @context (all six entity types)
│   └── IndustrialAsset-schema.json
├── scripts/
│   ├── convert.py              # Excel → NGSI-LD converter
│   ├── api.py                  # FastAPI NGSI-LD server
│   ├── load_to_coraine.py      # Bulk loader (default tenant)
│   └── migrate_tenant.py       # Load into named tenant + purge default
├── entities/                   # Output directory (git-ignored)
│   └── .gitkeep
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Scripts reference

### `convert.py`

```
python3 scripts/convert.py --input FILE.xlsx --output ./entities [--telemetry-limit 500]
```

Reads 6 sheets, outputs 6 NGSI-LD JSON array files + `manifest.json`.

### `api.py`

```
python3 scripts/api.py --entities ./entities --port 8000
```

FastAPI server with NGSI-LD standard endpoints (`/ngsi-ld/v1/entities`, `/ngsi-ld/v1/types`) and convenience endpoints (`/api/assets`, `/api/assets/{id}/telemetry`, etc.).

### `migrate_tenant.py`

```
python3 scripts/migrate_tenant.py [--tenant valmet] [--broker http://localhost:1026]
                                   [--load-only | --delete-only]
```

Loads entities from `entities/` into a named coraine tenant. Purges the default tenant after a successful load.

---

## coraine broker

This stack uses [coraine](https://github.com/SEAMWARE/coraine) — a lightweight NGSI-LD context broker in C (ETSI GS CIM 009 v1.9.1, 100% conformance, 967 KiB, starts in 10 ms).

The `docker-compose.yml` runs it with `corDB` (in-memory). Data is lost on container restart — re-run `migrate_tenant.py` to reload. For persistence, add `--dbDir /data` and a named volume.

---

## Smart Data Models contribution

The six entity type schemas are prepared for contribution to the Smart Data Models programme under [`../dataModel.IndustrialAsset/`](../dataModel.IndustrialAsset/). Each type has a `schema.json`, four example files (NGSI v2 key-values, NGSI-LD key-values, NGSI v2 normalized, NGSI-LD normalized), `README.md` and `LICENSE.md`.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).  
Data model schemas: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

---

## Acknowledgements

Built at [Since AI Hackathon 2026](https://sinceai.ai/hackathon), Turku, Finland.  
Challenge partner: [Valmet](https://www.valmet.com).  
Context broker: [coraine](https://github.com/SEAMWARE/coraine) by SEAMWARE.  
Data models: [Smart Data Models](https://smartdatamodels.org/) programme.
