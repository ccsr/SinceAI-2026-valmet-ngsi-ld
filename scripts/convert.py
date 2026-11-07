#!/usr/bin/env python3
"""
Valmet Builder Pack → NGSI-LD Converter
========================================
Reads valmet_builder_pack.xlsx and outputs NGSI-LD JSON entities for:
  - IndustrialAsset      (Asset_Master sheet)
  - AssetCondition       (Current_State sheet)
  - TelemetryObservation (Telemetry_Sample_3d sheet)
  - AssetAlert           (Alarms_Events sheet)
  - MaintenanceWorkOrder (Maintenance_History sheet)
  - SceneAnchor          (Scene_Anchors sheet)

Usage:
    python3 convert.py --input valmet_builder_pack.xlsx --output ./entities
    python3 convert.py --input valmet_builder_pack.xlsx --output ./entities --telemetry-limit 100
"""

import argparse
import json
import os
from datetime import datetime, date
from pathlib import Path
import openpyxl

CONTEXT_URL = "https://uri.fiware.org/ns/data-models/valmet/context.jsonld"
CORE_CONTEXT = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.9.jsonld"

CONTEXT = [CORE_CONTEXT, CONTEXT_URL]


# ── helpers ──────────────────────────────────────────────────────────────────

def iso(val):
    """Convert datetime / date to ISO-8601 string, or pass through strings."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%dT00:00:00Z")
    return str(val)


def prop(value, observed_at=None, unit_code=None):
    """Build an NGSI-LD Property node."""
    if value is None:
        return None
    node = {"type": "Property", "value": value}
    if observed_at:
        node["observedAt"] = observed_at
    if unit_code:
        node["unitCode"] = unit_code
    return node


def rel(object_id):
    """Build an NGSI-LD Relationship node."""
    if not object_id:
        return None
    return {"type": "Relationship", "object": object_id}


def asset_urn(asset_id):
    return f"urn:ngsi-ld:IndustrialAsset:{asset_id}"


def criticality_code(raw):
    """Extract criticality letter from 'A - production critical' → 'A'."""
    if raw and isinstance(raw, str):
        return raw.split(" - ")[0].strip()
    return raw


def read_sheet(wb, name):
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
    return [dict(zip(headers, row)) for row in rows[1:]]


def save(entities, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entities, f, indent=2, ensure_ascii=False)
    print(f"  ✓ {len(entities):>5} entities → {path.name}")


# ── converters ───────────────────────────────────────────────────────────────

def convert_assets(rows):
    entities = []
    for r in rows:
        asset_id = r.get("asset_id")
        if not asset_id:
            continue
        anchor_id = r.get("scene_anchor_id")
        e = {
            "id": asset_urn(asset_id),
            "type": "IndustrialAsset",
            "tag":                prop(r.get("tag")),
            "assetClass":         prop(r.get("asset_class")),
            "areaId":             prop(r.get("area_id")),
            "areaName":           prop(r.get("area_name")),
            "processContext":     prop(r.get("process_context")),
            "criticality":        prop(criticality_code(r.get("criticality"))),
            "installationDate":   prop(iso(r.get("installation_date"))),
            "lastServiceDate":    prop(iso(r.get("last_service_date"))),
            "serviceIntervalDays":prop(r.get("service_interval_days")),
            "connectivityProfile":prop(r.get("connectivity_profile")),
            "qrId":               prop(r.get("qr_id")),
            "nfcId":              prop(r.get("nfc_id")),
            "dataClassification": prop(r.get("data_classification")),
            "refSceneAnchor":     rel(f"urn:ngsi-ld:SceneAnchor:{anchor_id}") if anchor_id else None,
            "refCondition":       rel(f"urn:ngsi-ld:AssetCondition:{asset_id}"),
            "@context":           CONTEXT,
        }
        # Remove None values
        e = {k: v for k, v in e.items() if v is not None}
        entities.append(e)
    return entities


def convert_conditions(rows):
    entities = []
    for r in rows:
        asset_id = r.get("asset_id")
        if not asset_id:
            continue
        obs_at = iso(r.get("last_updated"))
        e = {
            "id": f"urn:ngsi-ld:AssetCondition:{asset_id}",
            "type": "AssetCondition",
            "refAsset":             rel(asset_urn(asset_id)),
            "healthScore":          prop(r.get("health_score"), observed_at=obs_at),
            "conditionState":       prop(r.get("condition_state"), observed_at=obs_at),
            "issueCode":            prop(r.get("issue_code"), observed_at=obs_at),
            "issueSummary":         prop(r.get("issue_summary"), observed_at=obs_at),
            "urgency":              prop(r.get("urgency"), observed_at=obs_at),
            "recommendedNextCheck": prop(r.get("recommended_next_check"), observed_at=obs_at),
            "modelConfidence":      prop(r.get("model_confidence"), observed_at=obs_at),
            "knowledgeDocId":       prop(r.get("knowledge_doc_id")),
            "observedAt":           obs_at,
            "@context":             CONTEXT,
        }
        e = {k: v for k, v in e.items() if v is not None}
        entities.append(e)
    return entities


def convert_telemetry(rows, limit=None):
    entities = []
    for i, r in enumerate(rows):
        if limit and i >= limit:
            break
        asset_id = r.get("asset_id")
        ts_raw = r.get("timestamp")
        if not asset_id or not ts_raw:
            continue
        obs_at = iso(ts_raw)
        seq = str(i).zfill(6)
        e = {
            "id": f"urn:ngsi-ld:TelemetryObservation:{asset_id}-{seq}",
            "type": "TelemetryObservation",
            "refAsset":          rel(asset_urn(asset_id)),
            "observedAt":        obs_at,
            "assetClass":        prop(r.get("asset_class")),
            "areaId":            prop(r.get("area_id")),
        }
        # Conditional telemetry fields — only include if non-null
        field_map = {
            "processPressureBar":  ("process_pressure_bar", "BAR"),
            "processFlowPct":      ("process_flow_pct",     "P1"),
            "positionSetpointPct": ("position_setpoint_pct","P1"),
            "positionActualPct":   ("position_actual_pct",  "P1"),
            "pneumaticSupplyBar":  ("pneumatic_supply_bar", "BAR"),
            "stictionIndex":       ("stiction_index",       None),
            "loadForOpeningPct":   ("load_for_opening_pct", "P1"),
            "vibrationMmS":        ("vibration_mm_s",       "MMT"),
            "bearingTempC":        ("bearing_temp_c",       "CEL"),
            "motorCurrentA":       ("motor_current_a",      "AMP"),
            "speedRpm":            ("speed_rpm",            "RPM"),
            "signalQualityPct":    ("signal_quality_pct",   "P1"),
            "sensorBias":          ("sensor_bias",          None),
        }
        for ngsi_key, (xlsx_key, unit) in field_map.items():
            val = r.get(xlsx_key)
            if val is not None:
                e[ngsi_key] = prop(val, observed_at=obs_at, unit_code=unit)

        e["@context"] = CONTEXT
        entities.append(e)
    return entities


def convert_alerts(rows):
    entities = []
    for r in rows:
        event_id = r.get("event_id")
        asset_id = r.get("asset_id")
        if not event_id or not asset_id:
            continue
        obs_at = iso(r.get("timestamp"))
        e = {
            "id": f"urn:ngsi-ld:AssetAlert:{event_id}",
            "type": "AssetAlert",
            "refAsset":        rel(asset_urn(asset_id)),
            "observedAt":      obs_at,
            "areaName":        prop(r.get("area_name")),
            "eventType":       prop(r.get("event_type"), observed_at=obs_at),
            "severity":        prop(r.get("severity")),
            "eventCode":       prop(r.get("event_code")),
            "eventDescription":prop(r.get("description")),
            "eventSource":     prop(r.get("source")),
            "eventStatus":     prop(r.get("status")),
            "@context":        CONTEXT,
        }
        e = {k: v for k, v in e.items() if v is not None}
        entities.append(e)
    return entities


def convert_workorders(rows):
    entities = []
    for r in rows:
        wo_id = r.get("work_order_id")
        asset_id = r.get("asset_id")
        if not wo_id or not asset_id:
            continue
        e = {
            "id": f"urn:ngsi-ld:MaintenanceWorkOrder:{wo_id}",
            "type": "MaintenanceWorkOrder",
            "refAsset":        rel(asset_urn(asset_id)),
            "openedAt":        prop(iso(r.get("opened_at"))),
            "areaName":        prop(r.get("area_name")),
            "action":          prop(r.get("action")),
            "maintenanceType": prop(r.get("maintenance_type")),
            "priority":        prop(r.get("priority")),
            "result":          prop(r.get("result")),
            "durationH":       prop(r.get("duration_h"), unit_code="HUR"),
            "downtimeH":       prop(r.get("downtime_h"), unit_code="HUR"),
            "role":            prop(r.get("role")),
            "knowledgeDocId":  prop(r.get("knowledge_doc_id")),
            "@context":        CONTEXT,
        }
        e = {k: v for k, v in e.items() if v is not None}
        entities.append(e)
    return entities


def convert_anchors(rows):
    entities = []
    for r in rows:
        anchor_id = r.get("scene_anchor_id")
        asset_id = r.get("asset_id")
        if not anchor_id or not asset_id:
            continue
        e = {
            "id": f"urn:ngsi-ld:SceneAnchor:{anchor_id}",
            "type": "SceneAnchor",
            "refAsset":          rel(asset_urn(asset_id)),
            "areaId":            prop(r.get("area_id")),
            "xLocalM":           prop(r.get("x_local_m"), unit_code="MTR"),
            "yLocalM":           prop(r.get("y_local_m"), unit_code="MTR"),
            "zLocalM":           prop(r.get("z_local_m"), unit_code="MTR"),
            "bboxXM":            prop(r.get("bbox_x_m"),  unit_code="MTR"),
            "bboxYM":            prop(r.get("bbox_y_m"),  unit_code="MTR"),
            "bboxZM":            prop(r.get("bbox_z_m"),  unit_code="MTR"),
            "scenePath":         prop(r.get("scene_path")),
            "mappingStatus":     prop(r.get("mapping_status")),
            "identityMethods":   prop(r.get("identity_methods")),
            "mappingConfidence": prop(r.get("mapping_confidence")),
            "@context":          CONTEXT,
        }
        # Add GeoJSON location from x/y/z
        x = r.get("x_local_m")
        z = r.get("z_local_m")
        if x is not None and z is not None:
            e["location"] = {
                "type": "GeoProperty",
                "value": {
                    "type": "Point",
                    "coordinates": [float(x), float(r.get("y_local_m", 0)), float(z)]
                }
            }
        e = {k: v for k, v in e.items() if v is not None}
        entities.append(e)
    return entities


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Convert Valmet Builder Pack xlsx to NGSI-LD JSON")
    parser.add_argument("--input",  default="valmet_builder_pack.xlsx", help="Path to input .xlsx file")
    parser.add_argument("--output", default="./entities",               help="Output directory for NGSI-LD JSON files")
    parser.add_argument("--telemetry-limit", type=int, default=500,     help="Max telemetry rows to convert (default 500; 0=all)")
    args = parser.parse_args()

    out = Path(args.output)
    tlimit = args.telemetry_limit if args.telemetry_limit > 0 else None

    print(f"\nLoading {args.input}...")
    wb = openpyxl.load_workbook(args.input)

    conversions = [
        ("Asset_Master",        convert_assets,     out / "industrial_assets.json"),
        ("Current_State",       convert_conditions, out / "asset_conditions.json"),
        ("Alarms_Events",       convert_alerts,     out / "asset_alerts.json"),
        ("Maintenance_History", convert_workorders, out / "maintenance_workorders.json"),
        ("Scene_Anchors",       convert_anchors,    out / "scene_anchors.json"),
    ]

    for sheet_name, converter, outpath in conversions:
        rows = read_sheet(wb, sheet_name)
        entities = converter(rows)
        save(entities, outpath)

    # Telemetry last (potentially large)
    trows = read_sheet(wb, "Telemetry_Sample_3d")
    tentities = convert_telemetry(trows, limit=tlimit)
    save(tentities, out / "telemetry_observations.json")

    # Write a summary manifest
    manifest = {
        "generated": iso(datetime.utcnow()),
        "source": os.path.basename(args.input),
        "context": CONTEXT,
        "entity_types": [
            {"type": "IndustrialAsset",      "file": "industrial_assets.json",      "count": 0},
            {"type": "AssetCondition",       "file": "asset_conditions.json",       "count": 0},
            {"type": "AssetAlert",           "file": "asset_alerts.json",           "count": 0},
            {"type": "MaintenanceWorkOrder", "file": "maintenance_workorders.json", "count": 0},
            {"type": "SceneAnchor",          "file": "scene_anchors.json",          "count": 0},
            {"type": "TelemetryObservation", "file": "telemetry_observations.json","count": 0},
        ]
    }
    # Update counts from written files
    for item in manifest["entity_types"]:
        fp = out / item["file"]
        if fp.exists():
            with open(fp) as f:
                item["count"] = len(json.load(f))
    save([manifest], out / "manifest.json")
    print(f"\nDone. Entities written to: {out.resolve()}")


if __name__ == "__main__":
    main()
