#!/usr/bin/env python3
"""Validate a built release directory the way the NeurIPS Croissant checker does.

The checker (https://huggingface.co/spaces/JoaquinVanschoren/croissant-checker)
parses the JSON, loads it with mlcroissant, generates the first record of every
record set, and checks for the required RAI fields. This script runs the same
steps before the dataset is public by mapping every file to its local copy;
checksums are still verified. It then generates every record and checks counts,
references, and images.

Usage:
    pip install "mlcroissant[image]"
    python release/validate_croissant.py build/hf
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import mlcroissant as mlc

# Required by the NeurIPS 2026 E&D hosting guidelines.
RAI_FIELDS = [
    "rai:dataLimitations",
    "rai:dataBiases",
    "rai:personalSensitiveInformation",
    "rai:dataUseCases",
    "rai:dataSocialImpact",
    "rai:hasSyntheticData",
    "prov:wasDerivedFrom",
    "prov:wasGeneratedBy",
]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _value(record: dict, record_set: str, field: str):
    v = record[f"{record_set}/{field}"]
    return v.decode() if isinstance(v, bytes) else v


def validate(release_dir: Path) -> list[str]:
    errors: list[str] = []
    metadata = json.loads((release_dir / "croissant.json").read_text())

    missing = [f for f in RAI_FIELDS if f not in metadata]
    if missing:
        errors.append(f"Missing RAI fields: {missing}")

    mapping = {
        obj["@id"]: str(release_dir / obj["name"])
        for obj in metadata["distribution"]
        if obj["@type"] == "cr:FileObject" and obj["encodingFormat"] != "git+https"
    }
    mapping["repo"] = str(release_dir)
    dataset = mlc.Dataset(jsonld=metadata, mapping=mapping)
    for warning in dataset.metadata.ctx.issues.warnings:
        print(f"warning: {warning}")

    records = {}
    for record_set in dataset.metadata.record_sets:
        try:
            records[record_set.uuid] = list(dataset.records(record_set=record_set.uuid))
        except Exception as e:  # noqa: BLE001 - report every failing record set
            errors.append(f"Record set {record_set.uuid!r} failed: {e}")
    if errors:
        return errors

    expected = {
        "instances": len(_jsonl(release_dir / "data/instances.jsonl")),
        "gold_blocks": len(_jsonl(release_dir / "data/gold_blocks.jsonl")),
        "systems": len(_jsonl(release_dir / "data/systems.jsonl")),
        "artifact_images": len(list(release_dir.glob("artifacts/*/*.png"))),
        "artifact_logs": len(list(release_dir.glob("artifacts/*/*.log"))),
    }
    for name, n in expected.items():
        if len(records[name]) != n:
            errors.append(f"{name}: generated {len(records[name])} records, expected {n}")

    instance_ids = {_value(r, "instances", "instance_id") for r in records["instances"]}
    systems = {_value(r, "systems", "system") for r in records["systems"]}
    if bad := {_value(r, "instances", "system") for r in records["instances"]} - systems:
        errors.append(f"instances reference unknown systems: {bad}")
    for name in ("gold_blocks", "artifact_images", "artifact_logs"):
        if bad := {_value(r, name, "instance_id") for r in records[name]} - instance_ids:
            errors.append(f"{name} reference unknown instances: {sorted(bad)[:5]}")
    if bugs_without_blocks := instance_ids - {
        _value(r, "gold_blocks", "instance_id") for r in records["gold_blocks"]
    }:
        errors.append(f"Bugs without gold blocks: {sorted(bugs_without_blocks)}")

    for r in records["artifact_images"]:
        image = r["artifact_images/image"]
        if not getattr(image, "size", None):
            errors.append(f"Image did not decode: {_value(r, 'artifact_images', 'file_name')}")

    counts = ", ".join(f"{k}={len(v)}" for k, v in records.items())
    print(f"Generated records: {counts}")
    lite = sum(bool(r["instances/lite"]) for r in records["instances"])
    print(f"Lite bugs: {lite}")
    return errors


def main() -> None:
    release_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "build/hf").resolve()
    errors = validate(release_dir)
    if errors:
        print("FAILED")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("OK: Croissant metadata loads, all records generate, RAI fields present.")


if __name__ == "__main__":
    main()
