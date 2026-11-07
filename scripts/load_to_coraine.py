#!/usr/bin/env python3
"""
load_to_coraine.py
==================
Bulk-loads all Valmet NGSI-LD entity files into a running coraine broker
using the NGSI-LD batch upsert endpoint:

  POST /ngsi-ld/v1/entityOperations/upsert

Usage:
    python3 scripts/load_to_coraine.py
    python3 scripts/load_to_coraine.py --broker http://localhost:1026 --entities ./entities
    python3 scripts/load_to_coraine.py --dry-run       # validate files, don't POST
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

BATCH_SIZE = 25    # smaller batches avoid context-resolution timeouts


# ── helpers ───────────────────────────────────────────────────────────────────

def wait_for_broker(broker: str, retries: int = 20, delay: float = 2.0):
    """Poll the broker until it responds or give up."""
    url = f"{broker}/ngsi-ld/v1/types"
    print(f"Waiting for coraine at {broker} ...", end="", flush=True)
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status < 500:
                    print(" ready.")
                    return
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(delay)
    print()
    sys.exit(f"ERROR: coraine at {broker} did not respond after {retries * delay:.0f}s")


def upsert_batch(broker: str, batch: list, dry_run: bool, retries: int = 3) -> tuple[int, int]:
    """POST one batch to /ngsi-ld/v1/entityOperations/upsert. Returns (ok, err)."""
    if dry_run:
        return len(batch), 0

    url = f"{broker}/ngsi-ld/v1/entityOperations/upsert"
    body = json.dumps(batch).encode()

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/ld+json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status = r.status
                # 201 = all created, 207 = partial (some existed), both are fine
                if status in (201, 204, 207):
                    return len(batch), 0
                else:
                    resp = r.read().decode()
                    print(f"  [warn] batch HTTP {status}: {resp[:200]}")
                    return 0, len(batch)
        except urllib.error.HTTPError as e:
            body_err = e.read().decode()
            print(f"  [error] HTTP {e.code}: {body_err[:300]}")
            return 0, len(batch)
        except Exception as ex:
            if attempt < retries:
                time.sleep(1)
            else:
                print(f"  [error] {ex}")
                return 0, len(batch)
    return 0, len(batch)


CONTEXT_REMAP = {
    # The valmet context URL doesn't exist publicly — remap to the local API
    # so coraine (running inside Docker) can fetch it over the internal network.
    "https://uri.fiware.org/ns/data-models/valmet/context.jsonld":
        "http://valmet-ngsi-ld:8000/context.jsonld",
}


def remap_context(entity: dict) -> dict:
    """Replace non-public context URLs with the locally-served ones."""
    ctx = entity.get("@context")
    if isinstance(ctx, list):
        entity["@context"] = [CONTEXT_REMAP.get(c, c) for c in ctx]
    elif isinstance(ctx, str):
        entity["@context"] = CONTEXT_REMAP.get(ctx, ctx)
    return entity


def load_file(broker: str, filepath: Path, dry_run: bool):
    with open(filepath, encoding="utf-8") as f:
        entities = json.load(f)

    if not isinstance(entities, list):
        entities = [entities]

    entities = [remap_context(e) for e in entities]

    total = len(entities)
    ok = err = 0

    for i in range(0, total, BATCH_SIZE):
        chunk = entities[i: i + BATCH_SIZE]
        c_ok, c_err = upsert_batch(broker, chunk, dry_run)
        ok += c_ok
        err += c_err
        if not dry_run:
            time.sleep(0.1)
        pct = min(100, int((i + len(chunk)) / total * 100))
        print(f"  {pct:3d}%  {i + len(chunk)}/{total}", end="\r", flush=True)

    print(f"  100%  {total}/{total}  ✓ {ok} loaded  ✗ {err} errors        ")
    return ok, err


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Load Valmet entities into coraine")
    parser.add_argument("--broker",   default="http://localhost:1026", help="coraine base URL")
    parser.add_argument("--entities", default="./entities",             help="Entities directory")
    parser.add_argument("--dry-run",  action="store_true",              help="Parse files, skip POST")
    args = parser.parse_args()

    entities_dir = Path(args.entities)
    if not entities_dir.exists():
        sys.exit(f"ERROR: entities directory not found: {entities_dir}")

    if not args.dry_run:
        wait_for_broker(args.broker)

    total_ok = total_err = 0
    for fname in ENTITY_FILES:
        fp = entities_dir / fname
        if not fp.exists():
            print(f"[skip] {fname} not found")
            continue
        print(f"\n→ {fname}")
        ok, err = load_file(args.broker, fp, args.dry_run)
        total_ok += ok
        total_err += err

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Done: {total_ok} entities loaded, {total_err} errors")

    if not args.dry_run:
        print(f"\nQuery the broker:")
        print(f"  curl '{args.broker}/ngsi-ld/v1/entities?type=IndustrialAsset' | jq")
        print(f"  curl '{args.broker}/ngsi-ld/v1/types' | jq")


if __name__ == "__main__":
    main()
