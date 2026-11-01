"""Tests for the Hugging Face release build and its Croissant metadata."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "release"))

import build_hf_dataset  # noqa: E402


@pytest.fixture(scope="module")
def release_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("hf")
    build_hf_dataset.build(out, date_published="2026-01-01")
    return out


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_counts(release_dir):
    instances = _jsonl(release_dir / "data/instances.jsonl")
    blocks = _jsonl(release_dir / "data/gold_blocks.jsonl")
    assert len(instances) == 120
    assert sum(r["lite"] for r in instances) == 34
    assert len(_jsonl(release_dir / "data/systems.jsonl")) == 11
    assert {b["instance_id"] for b in blocks} == {r["instance_id"] for r in instances}


def test_lite_matches_list(release_dir):
    lite = (REPO_ROOT / "benchmark/lite.txt").read_text().split()
    instances = _jsonl(release_dir / "data/instances.jsonl")
    assert sorted(r["instance_id"] for r in instances if r["lite"]) == sorted(lite)


def test_gold_blocks_match_benchmark(release_dir):
    """The export must not change labels."""
    exported = {}
    for b in _jsonl(release_dir / "data/gold_blocks.jsonl"):
        exported.setdefault(b["instance_id"], []).append(
            (b["file"], b["function"] or "", b["start_line"], b["end_line"])
        )
    for blocks_json in (REPO_ROOT / "benchmark/bugs").glob("*/*/blocks.json"):
        instance_id = f"{blocks_json.parent.parent.name}-{blocks_json.parent.name}"
        source = [
            (b["file"], b.get("function") or "", int(b["start_line"]), int(b["end_line"]))
            for b in json.loads(blocks_json.read_text())
        ]
        assert exported[instance_id] == source, instance_id


def test_urls_are_urls(release_dir):
    for r in _jsonl(release_dir / "data/instances.jsonl"):
        assert r["issue_url"].startswith("https://"), r["instance_id"]
        assert r["fix_url"] is None or r["fix_url"].startswith("https://"), r["instance_id"]


def test_bug_dirs_hold_only_benchmark_files():
    """Bug directories hold the report, labels, and attachments, nothing system-specific."""
    for bug_dir in (REPO_ROOT / "benchmark/bugs").glob("*/*"):
        extra = {p.name for p in bug_dir.iterdir()} - {"report.json", "blocks.json", "artifact_dir"}
        assert not extra, (bug_dir, extra)


def test_no_annotator_comments():
    """Internal curation notes are not part of the release."""
    for path in (REPO_ROOT / "benchmark/bugs").glob("*/*/*.json"):
        assert '"comment"' not in path.read_text(), path


def test_croissant_checksums(release_dir):
    metadata = json.loads((release_dir / "croissant.json").read_text())
    for obj in metadata["distribution"]:
        if obj["@type"] == "cr:FileObject" and obj["encodingFormat"] != "git+https":
            assert obj["sha256"] == build_hf_dataset._sha256(release_dir / obj["name"])


def test_croissant_validates(release_dir):
    pytest.importorskip("mlcroissant")
    import validate_croissant

    assert validate_croissant.validate(release_dir) == []


def test_build_is_deterministic(release_dir, tmp_path):
    build_hf_dataset.build(tmp_path, date_published="2026-01-01")
    for path in release_dir.rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (tmp_path / path.relative_to(release_dir)).read_bytes(), path
