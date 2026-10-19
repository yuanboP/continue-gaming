"""Private asset selection; standard-library only, shared with feasibility oracle."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def catalog_hash(catalog):
    return hashlib.sha256(canonical(catalog)).hexdigest()


def validate_catalog(catalog):
    if catalog.get("schema_version") != "visual-inspection-assets-v1":
        raise ValueError("Unsupported asset catalog schema")
    items, references = catalog.get("items"), catalog.get("references")
    if not isinstance(items, list) or not isinstance(references, list) or not references:
        raise ValueError("Catalog requires query items and independent normal references")
    ids, hashes = set(), set()
    reference_skus = set()
    for section, rows in [("items", items), ("references", references)]:
        for row in rows:
            for key in ["asset_id", "sku", "image", "sha256"]:
                if not isinstance(row.get(key), str) or not row[key]:
                    raise ValueError("Missing asset field: " + key)
            if row["asset_id"] in ids or row["sha256"] in hashes:
                raise ValueError("Query/reference asset IDs and image hashes must be globally unique")
            ids.add(row["asset_id"])
            hashes.add(row["sha256"])
            if section == "items":
                if row.get("split") not in ["dev", "live"] or row.get("label") not in ["normal", "defect"]:
                    raise ValueError("Query split/label is invalid")
            else:
                reference_skus.add(row["sku"])
    if any(row["sku"] not in reference_skus for row in items):
        raise ValueError("Every query SKU needs a separate normal reference")
    for label in ["normal", "defect"]:
        if sum(row["split"] == "live" and row["label"] == label for row in items) < 12:
            raise ValueError("Live pool requires at least 12 unique images per label")
        if sum(row["split"] == "dev" and row["label"] == label for row in items) < 1:
            raise ValueError("Developer pool must contain both labels")


def create_manifest(seed, mode, catalog, contract):
    validate_catalog(catalog)
    rng = random.Random(seed)
    pools = {label: sorted([x for x in catalog["items"] if x["split"] == mode and x["label"] == label], key=lambda x: x["asset_id"])
             for label in ["normal", "defect"]}
    count = 12 if mode == "live" else min(12, *(len(pool) for pool in pools.values()))
    chosen = rng.sample(pools["normal"], count)+rng.sample(pools["defect"], count)
    rng.shuffle(chosen)
    references = {}
    for row in sorted(catalog["references"], key=lambda x: x["asset_id"]):
        references.setdefault(row["sku"], row)
    output = []
    for index, row in enumerate(chosen):
        lane = contract["lane_keys"][index % 4]
        entry = 0.0 if index == 0 else index*contract["entry_interval_ms"]+rng.uniform(-contract["entry_jitter_ms"], contract["entry_jitter_ms"])
        reference = references[row["sku"]]
        output.append({"carrier_id": "c"+format(rng.getrandbits(64), "016x"),
                       "lane": lane, "entry_ms": round(entry,6), "sku": row["sku"],
                       "asset_id": row["asset_id"], "image_sha256": row["sha256"],
                       "reference_asset_id": reference["asset_id"], "reference_sha256": reference["sha256"],
                       "split": mode, "label": row["label"]})
    horizon = contract["horizon_ms"]
    if mode == "dev" and count < 12:
        final_arrival = max(row["entry_ms"]+contract["distance_to_gate_mm"]/contract["lane_speed_mm_s"][row["lane"]]*1000 for row in output)
        horizon = int(final_arrival+8000)
    return output, horizon
