#!/usr/bin/env python3
"""
Valmet Industrial Metaverse — NGSI-LD API
==========================================
Serves NGSI-LD entities from the converted JSON files.
Implements a subset of the ETSI NGSI-LD API (ETSI GS CIM 009 v1.9.1).

Endpoints:
  GET  /ngsi-ld/v1/entities                    list entities (filter by type, id, q)
  GET  /ngsi-ld/v1/entities/{entityId}         retrieve single entity
  GET  /ngsi-ld/v1/types                       list available entity types
  GET  /api/assets                             convenience: all IndustrialAsset
  GET  /api/assets/{id}                        asset + current condition merged
  GET  /api/assets/{id}/telemetry              telemetry time-series for asset
  GET  /api/assets/{id}/alerts                 alerts for asset
  GET  /api/assets/{id}/workorders             maintenance work orders for asset
  GET  /api/scene/anchors                      3D scene anchors
  GET  /context.jsonld                         serve the JSON-LD context

Usage:
    pip install fastapi uvicorn
    python3 api.py --entities ./entities --port 8000
    # then: curl http://localhost:8000/ngsi-ld/v1/entities?type=IndustrialAsset
"""

import argparse
import json
import os
from pathlib import Path
from typing import Optional, List

try:
    from fastapi import FastAPI, HTTPException, Query, Response
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError:
    raise SystemExit("Install dependencies first:\n  pip install fastapi uvicorn")

# ── boot args ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--entities", default="./entities")
parser.add_argument("--port",     type=int, default=8000)
parser.add_argument("--host",     default="0.0.0.0")
_args, _ = parser.parse_known_args()

ENTITIES_DIR = Path(_args.entities)
CONTEXT_FILE = Path(__file__).parent.parent / "data-model" / "context.jsonld"

NGSI_LD_MIME = "application/ld+json"

# ── load entities into memory ────────────────────────────────────────────────

_store: dict[str, dict] = {}          # id → entity
_by_type: dict[str, list] = {}        # type → [entity, ...]


def _load():
    files = [
        "industrial_assets.json",
        "asset_conditions.json",
        "asset_alerts.json",
        "maintenance_workorders.json",
        "scene_anchors.json",
        "telemetry_observations.json",
    ]
    for fname in files:
        fp = ENTITIES_DIR / fname
        if not fp.exists():
            print(f"  [warn] {fname} not found, skipping")
            continue
        with open(fp, encoding="utf-8") as f:
            entities = json.load(f)
        for e in entities:
            eid = e.get("id")
            etype = e.get("type")
            if eid and etype:
                _store[eid] = e
                _by_type.setdefault(etype, []).append(e)
    print(f"Loaded {len(_store)} entities across {len(_by_type)} types.")


_load()

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Valmet Industrial Metaverse — NGSI-LD API",
    description=(
        "NGSI-LD API serving synthetic industrial asset data "
        "(control valves, pumps, motors, sensors) from the Valmet × Since AI 2026 builder pack. "
        "Implements ETSI GS CIM 009 v1.9.1 endpoints."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def ld_response(data) -> Response:
    return Response(
        content=json.dumps(data, ensure_ascii=False),
        media_type=NGSI_LD_MIME,
    )


# ── NGSI-LD core endpoints ────────────────────────────────────────────────────

@app.get("/ngsi-ld/v1/entities", tags=["NGSI-LD"])
def list_entities(
    type: Optional[str] = Query(None, description="Filter by entity type, e.g. IndustrialAsset"),
    id: Optional[str]   = Query(None, description="Filter by exact entity ID (comma-separated)"),
    q: Optional[str]    = Query(None, description="Simple property=value filter, e.g. assetClass==CONTROL_VALVE"),
    limit: int          = Query(100, ge=1, le=1000, description="Max results"),
    offset: int         = Query(0,   ge=0,          description="Pagination offset"),
    attrs: Optional[str]= Query(None, description="Comma-separated attribute names to include"),
):
    """
    List entities. Supports type, id, q (simple equality filter), limit, offset, attrs projection.
    """
    if type:
        results = list(_by_type.get(type, []))
    else:
        results = list(_store.values())

    # id filter
    if id:
        ids = {i.strip() for i in id.split(",")}
        results = [e for e in results if e.get("id") in ids]

    # q filter — simple key==value or key!=value
    if q:
        for clause in q.split(";"):
            if "==" in clause:
                key, val = clause.split("==", 1)
                key = key.strip(); val = val.strip()
                results = [
                    e for e in results
                    if _attr_val(e, key) == val
                ]
            elif "!=" in clause:
                key, val = clause.split("!=", 1)
                key = key.strip(); val = val.strip()
                results = [
                    e for e in results
                    if _attr_val(e, key) != val
                ]

    total = len(results)
    page = results[offset: offset + limit]

    # attrs projection
    if attrs:
        keep = set(attrs.split(",")) | {"id", "type", "@context"}
        page = [{k: v for k, v in e.items() if k in keep} for e in page]

    headers = {"NGSILD-Results-Count": str(total)}
    return Response(
        content=json.dumps(page, ensure_ascii=False),
        media_type=NGSI_LD_MIME,
        headers=headers,
    )


def _attr_val(entity, key):
    """Extract string value of a simple Property attribute."""
    attr = entity.get(key)
    if attr is None:
        return None
    if isinstance(attr, dict) and attr.get("type") == "Property":
        return str(attr.get("value", ""))
    return str(attr)


@app.get("/ngsi-ld/v1/entities/{entity_id:path}", tags=["NGSI-LD"])
def get_entity(entity_id: str):
    """Retrieve a single entity by its full URN ID."""
    entity = _store.get(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail={"type": "https://uri.etsi.org/ngsi-ld/errors/ResourceNotFound", "title": "Entity not found", "detail": entity_id})
    return ld_response(entity)


@app.get("/ngsi-ld/v1/types", tags=["NGSI-LD"])
def list_types():
    """List all available entity types."""
    return ld_response([
        {"id": f"urn:ngsi-ld:EntityTypeInfo:{t}", "type": "EntityTypeInfo", "typeName": t, "entityCount": len(v)}
        for t, v in _by_type.items()
    ])


# ── convenience API endpoints ─────────────────────────────────────────────────

@app.get("/api/assets", tags=["Assets"])
def api_assets(
    assetClass: Optional[str] = Query(None, description="CONTROL_VALVE | PROCESS_PUMP | ELECTRIC_MOTOR | PRESSURE_TRANSMITTER | VIBRATION_SENSOR"),
    areaId:     Optional[str] = Query(None, description="AREA-01 … AREA-05"),
    criticality:Optional[str] = Query(None, description="A | B | C"),
):
    """Return all industrial assets with optional filters."""
    assets = list(_by_type.get("IndustrialAsset", []))
    if assetClass:
        assets = [a for a in assets if _attr_val(a, "assetClass") == assetClass]
    if areaId:
        assets = [a for a in assets if _attr_val(a, "areaId") == areaId]
    if criticality:
        assets = [a for a in assets if _attr_val(a, "criticality") == criticality]
    return ld_response(assets)


@app.get("/api/assets/{asset_id}", tags=["Assets"])
def api_asset_detail(asset_id: str):
    """Return a single asset merged with its current condition."""
    urn = f"urn:ngsi-ld:IndustrialAsset:{asset_id}"
    asset = _store.get(urn)
    if not asset:
        raise HTTPException(404, f"Asset {asset_id} not found")
    # merge condition
    cond_urn = f"urn:ngsi-ld:AssetCondition:{asset_id}"
    cond = _store.get(cond_urn)
    merged = dict(asset)
    if cond:
        for k, v in cond.items():
            if k not in ("id", "type", "@context", "refAsset"):
                merged[k] = v
    return ld_response(merged)


@app.get("/api/assets/{asset_id}/telemetry", tags=["Telemetry"])
def api_telemetry(
    asset_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Return telemetry observations for an asset, newest first."""
    urn = f"urn:ngsi-ld:IndustrialAsset:{asset_id}"
    obs = [e for e in _by_type.get("TelemetryObservation", [])
           if e.get("refAsset", {}).get("object") == urn]
    obs.sort(key=lambda e: e.get("observedAt", ""), reverse=True)
    return ld_response({
        "assetId": asset_id,
        "totalCount": len(obs),
        "observations": obs[offset: offset + limit],
    })


@app.get("/api/assets/{asset_id}/alerts", tags=["Alerts"])
def api_alerts(
    asset_id: str,
    status: Optional[str] = Query(None, description="open | acknowledged | closed"),
    severity: Optional[str] = Query(None, description="low | medium | high"),
):
    """Return alerts for an asset."""
    urn = f"urn:ngsi-ld:IndustrialAsset:{asset_id}"
    alerts = [e for e in _by_type.get("AssetAlert", [])
              if e.get("refAsset", {}).get("object") == urn]
    if status:
        alerts = [a for a in alerts if _attr_val(a, "eventStatus") == status]
    if severity:
        alerts = [a for a in alerts if _attr_val(a, "severity") == severity]
    alerts.sort(key=lambda e: e.get("observedAt", ""), reverse=True)
    return ld_response(alerts)


@app.get("/api/assets/{asset_id}/workorders", tags=["Maintenance"])
def api_workorders(asset_id: str):
    """Return maintenance work orders for an asset, newest first."""
    urn = f"urn:ngsi-ld:IndustrialAsset:{asset_id}"
    wos = [e for e in _by_type.get("MaintenanceWorkOrder", [])
           if e.get("refAsset", {}).get("object") == urn]
    wos.sort(key=lambda e: _attr_val(e, "openedAt") or "", reverse=True)
    return ld_response(wos)


@app.get("/api/scene/anchors", tags=["Scene"])
def api_scene_anchors(areaId: Optional[str] = Query(None)):
    """Return 3D scene anchors (optionally filtered by area)."""
    anchors = list(_by_type.get("SceneAnchor", []))
    if areaId:
        anchors = [a for a in anchors if _attr_val(a, "areaId") == areaId]
    return ld_response(anchors)


@app.get("/context.jsonld", tags=["Context"])
def serve_context():
    """Serve the JSON-LD @context file."""
    if CONTEXT_FILE.exists():
        with open(CONTEXT_FILE, encoding="utf-8") as f:
            return Response(content=f.read(), media_type="application/ld+json")
    return ld_response({"error": "context file not found"})


@app.get("/", tags=["Info"])
def root():
    return {
        "title": "Valmet Industrial Metaverse — NGSI-LD API",
        "version": "1.0.0",
        "spec": "ETSI GS CIM 009 v1.9.1 (subset)",
        "docs": "/docs",
        "entities": {t: len(v) for t, v in _by_type.items()},
        "endpoints": {
            "list_entities":  "GET /ngsi-ld/v1/entities?type=IndustrialAsset",
            "get_entity":     "GET /ngsi-ld/v1/entities/urn:ngsi-ld:IndustrialAsset:ASSET-001",
            "types":          "GET /ngsi-ld/v1/types",
            "assets":         "GET /api/assets",
            "asset_detail":   "GET /api/assets/ASSET-001",
            "telemetry":      "GET /api/assets/ASSET-001/telemetry",
            "alerts":         "GET /api/assets/ASSET-001/alerts",
            "workorders":     "GET /api/assets/ASSET-001/workorders",
            "scene_anchors":  "GET /api/scene/anchors",
            "context":        "GET /context.jsonld",
        }
    }


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\nStarting NGSI-LD API on http://{_args.host}:{_args.port}")
    print(f"Swagger UI: http://localhost:{_args.port}/docs")
    print(f"Entities dir: {ENTITIES_DIR.resolve()}\n")
    uvicorn.run(app, host=_args.host, port=_args.port)
