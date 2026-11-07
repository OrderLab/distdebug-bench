#!/usr/bin/env python3
"""Build the Hugging Face release of DistDebug-Bench with Croissant metadata.

Reads ``benchmark/`` and writes a self-contained dataset directory:

    data/instances.jsonl              one row per bug: issue report and metadata
    data/gold_blocks.jsonl            one row per annotated root-cause block
    data/systems.jsonl                one row per source system, with its license
    artifacts/<instance_id>/<file>    logs and screenshots attached to issue reports
    croissant.json                    Croissant metadata, including the RAI fields
    README.md                         dataset card

Usage:
    python release/build_hf_dataset.py --out build/hf
    python release/validate_croissant.py build/hf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

VERSION = "1.1.0"
DEFAULT_HF_REPO = "OrderLab/distdebug-bench"
GITHUB_URL = "https://github.com/OrderLab/distdebug-bench"
WEBSITE_URL = "https://distdebug.com"

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = REPO_ROOT / "benchmark"
CARD_TEMPLATE = Path(__file__).resolve().parent / "dataset_card.md"

# Display name and license of each source system. Licenses are the SPDX
# expressions of the upstream repositories' current default branches.
SYSTEMS = {
    "elasticsearch": ("Elasticsearch", "AGPL-3.0-only OR SSPL-1.0 OR Elastic-2.0"),
    "etcd": ("etcd", "Apache-2.0"),
    "hbase": ("HBase", "Apache-2.0"),
    "hdfs": ("HDFS", "Apache-2.0"),
    "ignite": ("Ignite", "Apache-2.0"),
    "ignite3": ("Ignite 3", "Apache-2.0"),
    "kafka": ("Kafka", "Apache-2.0"),
    "kubernetes": ("Kubernetes", "Apache-2.0"),
    "ray": ("Ray", "Apache-2.0"),
    "tikv": ("TiKV", "Apache-2.0"),
    "zookeeper": ("ZooKeeper", "Apache-2.0"),
}

ARTIFACT_TYPES = {".png": "image/png", ".log": "text/plain"}

# JIRA project keys of the systems whose issues live on Apache JIRA.
JIRA_KEYS = {
    "hbase": "HBASE",
    "hdfs": "HDFS",
    "ignite": "IGNITE",
    "ignite3": "IGNITE",
    "kafka": "KAFKA",
    "zookeeper": "ZOOKEEPER",
}

AUTHORS = [
    ("Yuxuan Jiang", "https://essoz.github.io"),
    ("Chenglin Liu", None),
    ("John Rose", None),
    ("Mason Miller", None),
    ("Ruthesh Thavamani", None),
    ("Juntao Wu", None),
    ("Yinwei Huang", None),
    ("Jiahao Xu", None),
    ("Peng Huang", "https://web.eecs.umich.edu/~ryanph"),
]

CITATION = """@inproceedings{jiang2026distdebugbench,
  title     = {{DistDebug-Bench}: Can {LLM} Agents Diagnose Bugs in Distributed Systems?},
  author    = {Jiang, Yuxuan and Liu, Chenglin and Rose, John and Miller, Mason and Thavamani, Ruthesh and Wu, Juntao and Huang, Yinwei and Xu, Jiahao and Huang, Peng},
  booktitle = {Advances in Neural Information Processing Systems (NeurIPS), Track on Evaluations and Datasets},
  year      = {2026}
}"""


# --------------------------------------------------------------------------- #
# Reading benchmark/
# --------------------------------------------------------------------------- #


def _read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _yes_no(value: str, field: str, bug: str) -> bool:
    if value not in ("Yes", "No"):
        raise ValueError(f"{bug}: {field}={value!r} is not Yes/No")
    return value == "Yes"


def _iso_date(value: str | None) -> str | None:
    """`2023-9-5` -> `2023-09-05`; empty -> None."""
    if not value:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value.strip())
    if not m:
        raise ValueError(f"Unrecognized date {value!r}")
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _issue_url(system: str, number: str, raw: str) -> str:
    """Canonical issue URL. Some JIRA reports store a bare key or the page title instead."""
    if system in JIRA_KEYS:
        url = f"https://issues.apache.org/jira/browse/{JIRA_KEYS[system]}-{number}"
        if raw.startswith("http") and raw != url:
            raise ValueError(f"{system}-{number}: issue link {raw!r} != {url!r}")
        return url
    if not raw.startswith("https://github.com/"):
        raise ValueError(f"{system}-{number}: unexpected issue link {raw!r}")
    return raw


def _fix_url(system: str, number: str, raw: str) -> str | None:
    """Fix URL, or None if unrecorded. ZooKeeper stores GitHub page titles instead."""
    if not raw:
        return None
    if raw.startswith("http"):
        return raw
    if system == "zookeeper":
        if m := re.search(r"Pull Request #(\d+)", raw):
            return f"https://github.com/apache/zookeeper/pull/{m.group(1)}"
        if m := re.search(r"apache/zookeeper@([0-9a-f]{7,40})$", raw):
            return f"https://github.com/apache/zookeeper/commit/{m.group(1)}"
    raise ValueError(f"{system}-{number}: unexpected fix link {raw!r}")


def _bug_dirs() -> list[Path]:
    dirs = [d for d in (BENCHMARK_DIR / "bugs").glob("*/*") if d.is_dir()]
    return sorted(dirs, key=lambda d: (d.parent.name, int(d.name)))


def _artifact_files(bug_dir: Path) -> list[Path]:
    art = bug_dir / "artifact_dir"
    if not art.is_dir():
        return []
    files = sorted(p for p in art.iterdir() if p.is_file())
    for p in files:
        if p.suffix not in ARTIFACT_TYPES:
            raise ValueError(f"Unexpected artifact type: {p}")
    return files


def load_rows() -> tuple[list[dict], list[dict], list[dict], dict[str, list[Path]]]:
    lite = {
        line.strip()
        for line in (BENCHMARK_DIR / "lite.txt").read_text().splitlines()
        if line.strip()
    }
    systems_meta = _read_json(BENCHMARK_DIR / "systems.json")

    instances, blocks, artifacts = [], [], {}
    for bug_dir in _bug_dirs():
        system, number = bug_dir.parent.name, bug_dir.name
        instance_id = f"{system}-{number}"
        # One report has a stray trailing space in a key.
        report = {k.strip(): v for k, v in _read_json(bug_dir / "report.json").items()}
        if str(report["id"]) != number:
            raise ValueError(f"{instance_id}: report id {report['id']!r} != directory name")
        files = _artifact_files(bug_dir)
        if files:
            artifacts[instance_id] = files
        instances.append(
            {
                "instance_id": instance_id,
                "system": system,
                "issue_number": number,
                "lite": instance_id in lite,
                "title": report["title"],
                "description": report["description"],
                "branch": report["branch"],
                "issue_url": _issue_url(system, number, report["issue_link"]),
                "fix_url": _fix_url(system, number, report["fix_link"]),
                "issue_created": _iso_date(report.get("issue_creation_date")),
                "severity": report["severity"],
                # Unify capitalization variants ("Error Handling" / "Error handling").
                "root_cause_type": report["root_cause_type"].strip().capitalize(),
                "bug_complexity": report["bug_complexity"],
                "distributed": _yes_no(report["distributed"], "distributed", instance_id),
                "contains_analysis": _yes_no(
                    report["contains_analysis"], "contains_analysis", instance_id
                ),
                "analysis_removed": report["analysis_removed"] or None,
                "contains_logs": _yes_no(report["contains_logs"], "contains_logs", instance_id),
                "contains_stack_traces": _yes_no(
                    report["contains_stack_traces"], "contains_stack_traces", instance_id
                ),
                "num_artifacts": len(files),
            }
        )
        for i, b in enumerate(_read_json(bug_dir / "blocks.json")):
            blocks.append(
                {
                    "instance_id": instance_id,
                    "block_index": i,
                    "file": b["file"],
                    "function": b.get("function") or None,
                    "start_line": int(b["start_line"]),
                    "end_line": int(b["end_line"]),
                }
            )

    used = sorted({row["system"] for row in instances})
    if set(used) != set(SYSTEMS):
        raise ValueError(f"Systems in bugs/ {used} do not match SYSTEMS {sorted(SYSTEMS)}")
    missing = lite - {row["instance_id"] for row in instances}
    if missing:
        raise ValueError(f"lite.txt lists unknown bugs: {sorted(missing)}")

    trackers: dict[str, set[str]] = {}
    for row in instances:
        host = "Apache JIRA" if "issues.apache.org" in row["issue_url"] else "GitHub Issues"
        trackers.setdefault(row["system"], set()).add(host)
    systems = []
    for system in used:
        name, license_ = SYSTEMS[system]
        meta = systems_meta[system]
        systems.append(
            {
                "system": system,
                "name": name,
                "repository_url": meta["url"],
                "language": meta["language"],
                "license": license_,
                "issue_tracker": " / ".join(sorted(trackers[system])),
                "description": meta["description"],
            }
        )
    return instances, blocks, systems, artifacts


# --------------------------------------------------------------------------- #
# Croissant
# --------------------------------------------------------------------------- #

CONTEXT = {
    "@language": "en",
    "@vocab": "https://schema.org/",
    "citeAs": "cr:citeAs",
    "column": "cr:column",
    "conformsTo": "dct:conformsTo",
    "cr": "http://mlcommons.org/croissant/",
    "rai": "http://mlcommons.org/croissant/RAI/",
    "data": {"@id": "cr:data", "@type": "@json"},
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
    "dct": "http://purl.org/dc/terms/",
    "equivalentProperty": "cr:equivalentProperty",
    "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract",
    "field": "cr:field",
    "fileProperty": "cr:fileProperty",
    "fileObject": "cr:fileObject",
    "fileSet": "cr:fileSet",
    "format": "cr:format",
    "includes": "cr:includes",
    "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath",
    "key": "cr:key",
    "md5": "cr:md5",
    "parentField": "cr:parentField",
    "path": "cr:path",
    "recordSet": "cr:recordSet",
    "references": "cr:references",
    "regex": "cr:regex",
    "repeated": "cr:repeated",
    "replace": "cr:replace",
    "samplingRate": "cr:samplingRate",
    "sc": "https://schema.org/",
    "separator": "cr:separator",
    "source": "cr:source",
    "subField": "cr:subField",
    "transform": "cr:transform",
    "wd": "https://www.wikidata.org/wiki/",
    "prov": "http://www.w3.org/ns/prov#",
}

# (name, dataType, description) per column, in file order.
INSTANCE_FIELDS = [
    ("instance_id", "sc:Text", "Unique bug identifier, `<system>-<issue number>`."),
    ("system", "sc:Text", "Source system; references `systems`."),
    ("issue_number", "sc:Text", "Issue number in the system's tracker."),
    ("lite", "sc:Boolean", "Whether the bug is in DistDebug-Bench Lite (34 bugs)."),
    ("title", "sc:Text", "Issue title. Agent input."),
    ("description", "sc:Text", "Issue description as given to the agent. Prior root-cause analysis is removed where `analysis_removed` says so. Agent input."),
    ("branch", "sc:Text", "Branch, tag, or commit of the pre-fix source snapshot that gold-block line numbers refer to."),
    ("issue_url", "sc:URL", "URL of the original issue report. Not shown to agents."),
    ("fix_url", "sc:URL", "URL of the fixing pull request, commit, or patch, when recorded. Not shown to agents."),
    ("issue_created", "sc:Date", "Creation date of the issue, when recorded."),
    ("severity", "sc:Text", "Severity label (Blocker, Critical, Major)."),
    ("root_cause_type", "sc:Text", "Coarse root-cause category (e.g., Logic, Concurrency, Error handling). Not an exhaustive taxonomy."),
    ("bug_complexity", "sc:Text", "Annotator-assessed complexity."),
    ("distributed", "sc:Boolean", "Whether the failure involves interaction between distributed components."),
    ("contains_analysis", "sc:Boolean", "Whether the original report contained prior root-cause analysis."),
    ("analysis_removed", "sc:Text", "Whether that analysis was removed from `description` (No, Partly Removed, N/A)."),
    ("contains_logs", "sc:Boolean", "Whether the report includes logs, inline or attached."),
    ("contains_stack_traces", "sc:Boolean", "Whether the report includes stack traces."),
    ("num_artifacts", "sc:Integer", "Number of attached files in `artifact_images` and `artifact_logs`."),
]

BLOCK_FIELDS = [
    ("instance_id", "sc:Text", "Bug identifier; references `instances`."),
    ("block_index", "sc:Integer", "Zero-based index of the block within the bug."),
    ("file", "sc:Text", "Repository-relative path of the file containing the block."),
    ("function", "sc:Text", "Enclosing function or method."),
    ("start_line", "sc:Integer", "First line of the block (1-based) in the pre-fix snapshot."),
    ("end_line", "sc:Integer", "End of the block; the evaluator scores ranges as half-open [start_line, end_line)."),
]

SYSTEM_FIELDS = [
    ("system", "sc:Text", "System identifier used in `instance_id`."),
    ("name", "sc:Text", "Display name."),
    ("repository_url", "sc:URL", "Upstream source repository."),
    ("language", "sc:Text", "Primary implementation language."),
    ("license", "sc:Text", "SPDX license expression of the upstream repository's current default branch. Historical snapshots may differ. The dataset references paths and line numbers in these repositories but does not redistribute their source code."),
    ("issue_tracker", "sc:Text", "Tracker the issue reports come from."),
    ("description", "sc:Text", "Short description of the system."),
]

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Cross-table links are stated in field descriptions rather than as Croissant
# `references`: mlcroissant 1.1.1 fails to generate a record set that joins
# another record set which itself has a join, once both are read from the same
# Dataset (as the NeurIPS checker does). validate_croissant.py checks the links.
def _column_fields(record_set: str, file_id: str, fields) -> list[dict]:
    return [
        {
            "@type": "cr:Field",
            "@id": f"{record_set}/{name}",
            "name": name,
            "description": desc,
            "dataType": dtype,
            "source": {"fileObject": {"@id": file_id}, "extract": {"column": name}},
        }
        for name, dtype, desc in fields
    ]


def _artifact_fields(record_set: str, file_set: str, content_name: str, content_type: str, content_desc: str) -> list[dict]:
    src = {"fileSet": {"@id": file_set}}
    return [
        {
            "@type": "cr:Field",
            "@id": f"{record_set}/instance_id",
            "name": "instance_id",
            "description": "Bug the file is attached to; references `instances`.",
            "dataType": "sc:Text",
            "source": {
                **src,
                "extract": {"fileProperty": "fullpath"},
                "transform": {"regex": r"^.*artifacts/([^/]+)/[^/]+$"},
            },
        },
        {
            "@type": "cr:Field",
            "@id": f"{record_set}/file_name",
            "name": "file_name",
            "description": "Original file name.",
            "dataType": "sc:Text",
            "source": {**src, "extract": {"fileProperty": "filename"}},
        },
        {
            "@type": "cr:Field",
            "@id": f"{record_set}/{content_name}",
            "name": content_name,
            "description": content_desc,
            "dataType": content_type,
            "source": {**src, "extract": {"fileProperty": "content"}},
        },
    ]


def build_croissant(
    out: Path, hf_repo: str, revision: str, date_published: str | None, counts: dict[str, int]
) -> dict:
    page_url = f"https://huggingface.co/datasets/{hf_repo}"
    git_url = page_url if revision == "main" else f"{page_url}/tree/refs%2Ftags%2F{revision}"

    def resolve(rel: str) -> str:
        return f"{page_url}/resolve/{revision}/{rel}"

    def data_file(file_id: str, rel: str, desc: str) -> dict:
        return {
            "@type": "cr:FileObject",
            "@id": file_id,
            "name": rel,
            "description": desc,
            "contentUrl": resolve(rel),
            "encodingFormat": "application/jsonlines",
            "sha256": _sha256(out / rel),
        }

    distribution = [
        data_file("instances-jsonl", "data/instances.jsonl", "One row per bug."),
        data_file("gold-blocks-jsonl", "data/gold_blocks.jsonl", "One row per gold root-cause block."),
        data_file("systems-jsonl", "data/systems.jsonl", "One row per source system."),
        {
            "@type": "cr:FileObject",
            "@id": "repo",
            "name": "repo",
            "description": "The Hugging Face git repository of this dataset.",
            "contentUrl": git_url,
            "encodingFormat": "git+https",
            # Git repositories have no file checksum; this placeholder follows
            # Hugging Face's own Croissant files.
            "sha256": "https://github.com/mlcommons/croissant/issues/80",
        },
        {
            "@type": "cr:FileSet",
            "@id": "artifact-image-files",
            "name": "artifact-image-files",
            "description": "Screenshots attached to issue reports.",
            "containedIn": {"@id": "repo"},
            "encodingFormat": "image/png",
            "includes": "artifacts/*/*.png",
        },
        {
            "@type": "cr:FileSet",
            "@id": "artifact-log-files",
            "name": "artifact-log-files",
            "description": "Log files attached to issue reports.",
            "containedIn": {"@id": "repo"},
            "encodingFormat": "text/plain",
            "includes": "artifacts/*/*.log",
        },
    ]

    record_sets = [
        {
            "@type": "cr:RecordSet",
            "@id": "systems",
            "name": "systems",
            "description": f"The {counts['systems']} distributed systems the bugs come from, with upstream licenses.",
            "key": {"@id": "systems/system"},
            "field": _column_fields("systems", "systems-jsonl", SYSTEM_FIELDS),
        },
        {
            "@type": "cr:RecordSet",
            "@id": "instances",
            "name": "instances",
            "description": (
                f"The {counts['instances']} benchmark bugs. An agent receives `title`, `description`, "
                "the attached artifacts, and a checkout of the system at `branch`, and must name the "
                "root-cause code blocks."
            ),
            "key": {"@id": "instances/instance_id"},
            "field": _column_fields("instances", "instances-jsonl", INSTANCE_FIELDS),
        },
        {
            "@type": "cr:RecordSet",
            "@id": "gold_blocks",
            "name": "gold_blocks",
            "description": (
                f"The {counts['gold_blocks']} expert-annotated root-cause blocks used for scoring. "
                "Labels capture the annotated causal implementation in the pre-fix snapshot, not "
                "every location where the bug could be repaired."
            ),
            "key": [{"@id": "gold_blocks/instance_id"}, {"@id": "gold_blocks/block_index"}],
            "field": _column_fields("gold_blocks", "gold-blocks-jsonl", BLOCK_FIELDS),
        },
        {
            "@type": "cr:RecordSet",
            "@id": "artifact_images",
            "name": "artifact_images",
            "description": f"The {counts['images']} screenshots attached to issue reports. Agent input.",
            "field": _artifact_fields(
                "artifact_images", "artifact-image-files", "image", "sc:ImageObject", "The screenshot."
            ),
        },
        {
            "@type": "cr:RecordSet",
            "@id": "artifact_logs",
            "name": "artifact_logs",
            "description": f"The {counts['logs']} log files attached to issue reports. Agent input.",
            "field": _artifact_fields(
                "artifact_logs", "artifact-log-files", "content", "sc:Text", "The log file's text."
            ),
        },
    ]

    team = {
        "@type": "prov:Agent",
        "@id": "distdebug-bench-authors",
        "prov:label": "DistDebug-Bench authors, University of Michigan",
    }

    def activity(wikidata: str, label: str, desc: str) -> dict:
        return {
            "@type": "prov:Activity",
            "prov:type": {"@id": f"https://www.wikidata.org/wiki/{wikidata}"},
            "prov:label": label,
            "sc:description": desc,
            "prov:wasAttributedTo": [team],
        }

    metadata = {
        "@context": CONTEXT,
        "@type": "sc:Dataset",
        "conformsTo": "http://mlcommons.org/croissant/1.0",
        "name": "DistDebug-Bench",
        "description": (
            f"DistDebug-Bench is a benchmark for code-level root-cause diagnosis of real-world bugs in "
            f"distributed systems. It contains {counts['instances']} resolved bugs from "
            f"{counts['systems']} open-source systems. Each bug comes with the original issue report, "
            "any attached logs and screenshots, and expert-annotated root-cause code blocks in the "
            "pre-fix source snapshot. An agent reads the report, explores a checkout of the system, "
            "and names the responsible files, functions, and line ranges; diagnoses are scored by "
            f"their overlap with the gold blocks. DistDebug-Bench Lite is a {counts['lite']}-bug "
            "subset marked by the `lite` column."
        ),
        "url": page_url,
        "sameAs": [GITHUB_URL, WEBSITE_URL],
        "license": "https://spdx.org/licenses/MIT.html",
        "version": VERSION,
        "inLanguage": "en",
        "isLiveDataset": False,
        "keywords": [
            "distributed systems",
            "debugging",
            "root-cause diagnosis",
            "fault localization",
            "LLM agents",
            "software engineering",
            "benchmark",
        ],
        "creator": [
            {"@type": "Person", "name": name, **({"url": url} if url else {})}
            for name, url in AUTHORS
        ],
        "publisher": {"@type": "Organization", "name": "OrderLab, University of Michigan"},
        "citeAs": CITATION,
        "distribution": distribution,
        "recordSet": record_sets,
        "rai:dataLimitations": (
            "DistDebug-Bench covers resolved bugs in public, open-source distributed systems, "
            "and evaluates code-level root-cause diagnosis only. It is not representative of all "
            "software bugs, closed-source or industrial debugging, UI bugs, security "
            "vulnerabilities, or performance tuning. Selection favors bugs that were publicly "
            "reported, fixed, and labelable: fixes over 100 changed source lines are excluded "
            "unless the causal span is clearly separable. Gold blocks encode the annotated causal implementation established through "
            "issue resolution; a valid diagnosis may name other locations where the bug could be "
            "mitigated, and such diagnoses score low. Some root-cause categories contain few bugs, "
            "so per-category results are noisy. The issues are public, so models "
            "may have seen them during pretraining. The benchmark should not be used to claim "
            "general software-engineering ability, or to evaluate patch generation without "
            "additional validation."
        ),
        "rai:dataBiases": (
            "Systems with active public issue trackers and detailed engineering discussions are "
            f"over-represented, and Java systems account for {counts['java']} of the "
            f"{counts['instances']} bugs. Reports are in English. Labels "
            "are human-curated and reflect annotator judgment about the minimal root-cause code. "
            "Reports with logs, stack traces, prior analysis, or clear fix links are easier to "
            "label and may be over-represented relative to real-world debugging workloads."
        ),
        "rai:personalSensitiveInformation": (
            "No personal or sensitive attributes were intentionally collected. The data does not "
            "target gender, age, health, socioeconomic status, political or religious beliefs, "
            "culture, or geography. Because issue reports and logs are copied from public GitHub "
            "and Apache JIRA trackers, they may contain incidental public usernames, host names, "
            "IP addresses, timestamps, and file paths. The data is not intended for profiling or "
            "demographic inference."
        ),
        "rai:dataUseCases": (
            "Validated use: evaluating LLM agents and other tools on code-level root-cause "
            "diagnosis of distributed-system failures, given an issue report, its attachments, "
            "and a checkout of the pre-fix source; and characterizing what evidence issue reports "
            "provide. Not validated for model training, patch-generation evaluation, production "
            "debugging, or security triage."
        ),
        "rai:dataSocialImpact": (
            "Positive impact: more rigorous evaluation of debugging agents and better "
            "understanding of distributed-system failures, supporting software reliability "
            "research. Risks: overclaiming general debugging ability from a specialized benchmark, "
            "overfitting to public instances, training on the data without respecting upstream "
            "terms, and operational reliance on agents that may hallucinate root causes. "
            "Mitigations: versioned releases, provenance links for every bug, per-system license "
            "metadata, and documented scope and limitations."
        ),
        "rai:hasSyntheticData": False,
        "prov:wasDerivedFrom": [
            {
                "@id": "source-github",
                "prov:label": "GitHub issue trackers and source repositories (Elasticsearch, etcd, Kubernetes, Ray, TiKV)",
                "sc:license": "Issue content is subject to GitHub's terms and the respective projects; source code licenses are listed in the `systems` record set.",
                "prov:wasAttributedTo": {"@id": "https://github.com", "prov:label": "GitHub"},
            },
            {
                "@id": "source-apache-jira",
                "prov:label": "Apache JIRA issue reports and Apache source repositories (HBase, HDFS, Ignite, Ignite 3, Kafka, ZooKeeper)",
                "sc:license": "Issue content is subject to Apache Software Foundation terms; source code is under Apache-2.0.",
                "prov:wasAttributedTo": {
                    "@id": "https://www.apache.org/",
                    "prov:label": "Apache Software Foundation",
                },
            },
        ],
        "prov:wasGeneratedBy": [
            activity(
                "Q4929239",
                "Issue and fix collection",
                "Collected resolved issue reports, their attachments, and linked fixing pull "
                "requests or commits from the upstream trackers of eleven distributed systems.",
            ),
            activity(
                "Q1172378",
                "Benchmark instance filtering",
                "Kept closed, high-impact issues with an associated fix that describe a concrete "
                "failure and are diagnosable from issue-side context. Excluded large "
                "refactorings, test-only changes, and fixes over 100 changed source lines unless "
                "the causal span is clearly separable. Later discussion that reveals the root "
                "cause or patch strategy was removed or ignored.",
            ),
            activity(
                "Q109719325",
                "Expert root-cause annotation",
                "Each issue was independently annotated by three annotators, who inspected the "
                "report, the fixing change, and the affected code and marked the minimal pre-fix "
                "code blocks that embody the bug (file, function, line range), excluding tests, "
                "helper refactors, defensive hardening, and incidental cleanup. Labels were then "
                "reconciled into the final gold set; the paper's appendix reports "
                "inter-annotator agreement.",
            ),
            activity(
                "Q3306762",
                "Validation",
                "Annotators audited each accepted issue to confirm it is diagnosable without "
                "post-report discussion and that the report does not reveal the root cause. "
                "Labels and metadata were checked for consistency, including benchmark size, "
                "per-system coverage, block counts, and artifact availability.",
            ),
        ],
    }
    if date_published:
        metadata["datePublished"] = date_published
    return metadata


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build(
    out: Path,
    hf_repo: str = DEFAULT_HF_REPO,
    revision: str = "main",
    date_published: str | None = None,
) -> dict[str, int]:
    instances, blocks, systems, artifacts = load_rows()
    if out.exists():
        shutil.rmtree(out)
    _write_jsonl(out / "data/instances.jsonl", instances)
    _write_jsonl(out / "data/gold_blocks.jsonl", blocks)
    _write_jsonl(out / "data/systems.jsonl", systems)
    for instance_id, files in artifacts.items():
        dest = out / "artifacts" / instance_id
        dest.mkdir(parents=True)
        for f in files:
            shutil.copy2(f, dest / f.name)

    all_files = [f for files in artifacts.values() for f in files]
    counts = {
        "instances": len(instances),
        "lite": sum(row["lite"] for row in instances),
        "gold_blocks": len(blocks),
        "systems": len(systems),
        "images": sum(f.suffix == ".png" for f in all_files),
        "logs": sum(f.suffix == ".log" for f in all_files),
        "artifact_bugs": len(artifacts),
        "java": sum(
            next(s["language"] for s in systems if s["system"] == r["system"]) == "java"
            for r in instances
        ),
    }
    croissant = build_croissant(out, hf_repo, revision, date_published, counts)
    (out / "croissant.json").write_text(json.dumps(croissant, indent=2, ensure_ascii=False) + "\n")

    card = CARD_TEMPLATE.read_text()
    replacements = {
        "HF_REPO": hf_repo,
        "VERSION": VERSION,
        "GITHUB_URL": GITHUB_URL,
        "WEBSITE_URL": WEBSITE_URL,
        "CITATION": CITATION,
        "SYSTEMS_TABLE": "\n".join(
            f"| {s['name']} | {s['language']} | {s['issue_tracker']} | `{s['license']}` | "
            f"{sum(r['system'] == s['system'] for r in instances)} | "
            f"{sum(r['system'] == s['system'] and r['lite'] for r in instances)} |"
            for s in systems
        ),
        **{f"N_{k.upper()}": str(v) for k, v in counts.items()},
    }
    for key, value in replacements.items():
        card = card.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{\w+\}\}", card)
    if leftover:
        raise ValueError(f"Unfilled placeholders in dataset card: {leftover}")
    (out / "README.md").write_text(card)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "build" / "hf")
    ap.add_argument("--hf-repo", default=DEFAULT_HF_REPO, help="Hugging Face dataset id, e.g. OrderLab/distdebug-bench")
    ap.add_argument("--revision", default="main", help="'main' or a release tag that URLs resolve against")
    ap.add_argument("--date-published", help="Release date (YYYY-MM-DD) to record in croissant.json")
    args = ap.parse_args()
    counts = build(args.out, args.hf_repo, args.revision, args.date_published)
    print(f"Wrote {args.out}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
