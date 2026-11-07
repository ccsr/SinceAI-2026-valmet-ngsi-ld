#!/usr/bin/env python3
"""
migrate_tenant.py
=================
Moves Valmet NGSI-LD entities into a named tenant on coraine.

Steps:
  1. Load all entities from JSON files into the target tenant (upsert)
  2. Delete every entity from the default tenant

Usage:
    python3 scripts/migrate_tenant.py
    python3 scripts/migrate_tenant.py --tenant valmet --broker http://localhost:1026
    python3 scripts/migrate_tenant.py --load-only   # skip the delete step
    python3 scripts/migrate_tenant.py --delete-only # only purge default tenant
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── config ────────────────────────────────────────────────────────────────────

ENTITY_FILES = [
    "industrial_assets.json",
    "asset_conditions.json",
    "scene_anchors.json",
    "asset_alerts.json",
    "maintenance_workorders.json",
    "telemetry_observations.json",
]

BATCH_SIZE = 25

CONTEXT_REMAP = {
    "https://uri.fiware.org/ns/data-models/valmet/context.jsonld":
        "http://valmet-ngsi-ld:8000/context.jsonld",
}

ENTITY_TYPES = [
    "IndustrialAsset",
    "AssetCondition",
    "SceneAnchor",
    "AssetAlert",
    "MaintenanceWorkOrder",
    "TelemetryObservation",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def headers(tenant: str | None = None) -> dict:
    h = {"Content-Type": "application/ld+json"}
    if tenant:
        h["NGSILD-Tenant"] = tenant
    return h


def request(method: str, url: str, body: bytes | None = None,
            tenant: str | None = None, timeout: int = 60):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers(tenant))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as ex:
        return 0, str(ex)


def remap_context(entity: dict) -> dict:
    ctx = entity.get("@context")
    if isinstance(ctx, list):
        entity["@context"] = [CONTEXT_REMAP.get(c, c) for c in ctx]
    elif isinstance(ctx, str):
        entity["@context"] = CONTEXT_REMAP.get(ctx, ctx)
    return entity


def wait_for_broker(broker: str):
    print(f"Checking broker at {broker} ...", end="", flush=True)
    for _ in range(20):
        status, _ = request("GET", f"{broker}/ngsi-ld/v1/types", timeout=3)
        if 0 < status < 500:
            print(" ready.")
            return
        print(".", end="", flush=True)
        time.sleep(2)
    sys.exit(f"\nERROR: broker not reachable at {broker}")


# ── step 1: load into tenant ──────────────────────────────────────────────────

def load_to_tenant(broker: str, entities_dir: Path, tenant: str):
    print(f"\n── Step 1: Load entities → tenant '{tenant}' ──────────────────")
    url = f"{broker}/ngsi-ld/v1/entityOperations/upsert"
    total_ok = total_err = 0

    for fname in ENTITY_FILES:
        fp = entities_dir / fname
        if not fp.exists():
            print(f"  [skip] {fname}")
            continue
        with open(fp, encoding="utf-8") as f:
            entities = json.load(f)
        entities = [remap_context(e) for e in entities]
        ok = err = 0
        for i in range(0, len(entities), BATCH_SIZE):
            chunk = entities[i: i + BATCH_SIZE]
            for attempt in range(3):
                status, body = request("POST", url,
                                       body=json.dumps(chunk).encode(),
                                       tenant=tenant)
                if status in (201, 204, 207):
                    ok += len(chunk)
                    break
                elif attempt < 2:
                    time.sleep(1)
                else:
                    print(f"\n  [error] {fname} batch HTTP {status}: {body[:200]}")
                    err += len(chunk)
            time.sleep(0.1)
        print(f"  {fname:<40}  ✓ {ok}  ✗ {err}")
        total_ok += ok
        total_err += err

    print(f"\n  Total loaded: {total_ok} entities, {total_err} errors")
    return total_err == 0


# ── step 2: purge default tenant ─────────────────────────────────────────────

def purge_default_tenant(broker: str):
    print(f"\n── Step 2: Delete entities from default tenant ────────────────")
    total_deleted = 0

    for etype in ENTITY_TYPES:
        deleted = 0
        page = 0
        while True:
            # Fetch a page of IDs (default tenant = no NGSILD-Tenant header)
            url = (f"{broker}/ngsi-ld/v1/entities"
                   f"?type={etype}&limit=100&offset={page * 100}&attrs=id")
            status, body = request("GET", url, tenant=None)
            if status == 400:
                # Type unknown in this tenant — nothing to delete
                break
            if status != 200:
                print(f"  [warn] GET {etype} → HTTP {status}: {body[:100]}")
                break
            entities = json.loads(body)
            if not entities:
                break

            ids = [e["id"] for e in entities]

            # Batch delete
            del_url = f"{broker}/ngsi-ld/v1/entityOperations/delete"
            status, body = request("POST", del_url,
                                   body=json.dumps(ids).encode(),
                                   tenant=None)
            if status in (200, 204, 207):
                deleted += len(ids)
            else:
                print(f"  [error] batch delete HTTP {status}: {body[:200]}")

            if len(ids) < 100:
                break
            page += 1
            time.sleep(0.05)

        status_str = f"deleted {deleted}" if deleted else "already empty"
        print(f"  {etype:<35}  {status_str}")
        total_deleted += deleted

    print(f"\n  Total deleted from default tenant: {total_deleted}")


# ── verify ────────────────────────────────────────────────────────────────────

def verify(broker: str, tenant: str):
    print(f"\n── Verify: entity counts in tenant '{tenant}' ─────────────────")
    status, body = request("GET", f"{broker}/ngsi-ld/v1/types", tenant=tenant)
    if status != 200:
        print(f"  [error] /types → HTTP {status}: {body[:200]}")
        return
    types = json.loads(body)
    for t in types:
        if isinstance(t, dict):
            name = t.get('typeName') or t.get('type') or t.get('id', '?')
            count = t.get('entityCount', '?')
        else:
            # coraine may return plain strings or URIs
            name = str(t).split('/')[-1].split(':')[-1]
            count = '?'
        print(f"  {name:<35}  {count} entities")
    print(f"\n  Query example:")
    print(f"  curl -H 'NGSILD-Tenant: {tenant}' \\")
    print(f"    'http://localhost:1026/ngsi-ld/v1/entities?type=IndustrialAsset' | jq")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Move Valmet data to a coraine tenant")
    parser.add_argument("--broker",      default="http://localhost:1026")
    parser.add_argument("--entities",    default="./entities")
    parser.add_argument("--tenant",      default="valmet")
    parser.add_argument("--load-only",   action="store_true",
                        help="Load into tenant but skip deleting from default")
    parser.add_argument("--delete-only", action="store_true",
                        help="Only purge the default tenant (no load)")
    args = parser.parse_args()

    entities_dir = Path(args.entities)

    wait_for_broker(args.broker)

    if not args.delete_only:
        ok = load_to_tenant(args.broker, entities_dir, args.tenant)
        if not ok:
            print("\nWARNING: some entities failed to load — skipping default-tenant purge.")
            print("Re-run with --load-only to retry, or --delete-only to purge manually.")
            sys.exit(1)

    if not args.load_only:
        purge_default_tenant(args.broker)

    verify(args.broker, args.tenant)


if __name__ == "__main__":
    main()
