---
license: mit
language:
- en
pretty_name: DistDebug-Bench
size_categories:
- n<1K
tags:
- distributed-systems
- debugging
- root-cause-analysis
- fault-localization
- agents
- software-engineering
configs:
- config_name: instances
  data_files:
  - split: test
    path: data/instances.jsonl
  default: true
- config_name: gold_blocks
  data_files:
  - split: test
    path: data/gold_blocks.jsonl
- config_name: systems
  data_files:
  - split: test
    path: data/systems.jsonl
---

# DistDebug-Bench

DistDebug-Bench evaluates whether LLM agents can find the root cause of
real-world bugs in distributed systems. It contains {{N_INSTANCES}} resolved bugs from
{{N_SYSTEMS}} open-source systems. For each bug, an agent receives the original issue
report, any attached logs and screenshots, and a checkout of the system's
source at the pre-fix snapshot. It must name the responsible files, functions,
and line ranges. Diagnoses are scored against expert-annotated root-cause
blocks.

- Paper: *DistDebug-Bench: Can LLM Agents Diagnose Bugs in Distributed Systems?* (NeurIPS 2026, Evaluations and Datasets Track)
- Code, harness, and evaluator: {{GITHUB_URL}}
- Website and leaderboard: {{WEBSITE_URL}}
- Agent traces behind the paper's Table 1: https://huggingface.co/datasets/OrderLab/distdebug-bench-traces
- Version: {{VERSION}}

## Contents

| Config / path | Rows | Description |
|---|---:|---|
| `instances` (`data/instances.jsonl`) | {{N_INSTANCES}} | One row per bug: issue report, pre-fix snapshot, links, and metadata. |
| `gold_blocks` (`data/gold_blocks.jsonl`) | {{N_GOLD_BLOCKS}} | One row per gold root-cause block, keyed by `instance_id`. |
| `systems` (`data/systems.jsonl`) | {{N_SYSTEMS}} | Source systems with repository URLs and upstream licenses. |
| `artifacts/<instance_id>/` | {{N_IMAGES}} screenshots, {{N_LOGS}} logs | Files attached to the issue reports of {{N_ARTIFACT_BUGS}} bugs. |

`croissant.json` describes all of the above, including the attachments, in
[Croissant](https://mlcommons.org/croissant/) format with Responsible AI
fields.

### What the agent sees

The task input is `title`, `description`, the bug's attachments, and the
system's repository checked out at `branch`. The remaining columns
(`issue_url`, `fix_url`, and the characterization labels) are
for analysis and must not be shown to the agent: the links lead to the fix.
The harness in the GitHub repository prepares workspaces with anonymized paths
and without commits after the snapshot.

### Gold blocks

Each block gives a repository-relative `file`, the enclosing `function`, and a
`start_line`/`end_line` range in the pre-fix snapshot, scored as half-open
`[start_line, end_line)`. Blocks capture the annotated causal implementation
established through issue resolution, not every location where the bug could
be repaired. Score diagnoses with the evaluator in the GitHub repository; it
reproduces the paper's scores and reports file-, function-, line-, and
block-level coverage and precision, plus strict block coverage and mention
coverage.

## DistDebug-Bench Lite

DistDebug-Bench Lite is a {{N_LITE}}-bug subset (`lite == true`), selected for
report and label quality and for lower evaluation cost. Report Lite results
separately from full-benchmark results.

## Loading

```python
from datasets import load_dataset

instances = load_dataset("{{HF_REPO}}", "instances", split="test")
gold = load_dataset("{{HF_REPO}}", "gold_blocks", split="test")
lite = instances.filter(lambda row: row["lite"])
```

With [mlcroissant](https://github.com/mlcommons/croissant), every record set,
including the screenshots and logs, can be loaded from the metadata alone:

```python
import mlcroissant as mlc

ds = mlc.Dataset("https://huggingface.co/datasets/{{HF_REPO}}/resolve/main/croissant.json")
for record in ds.records("artifact_logs"):
    print(record["artifact_logs/instance_id"], record["artifact_logs/file_name"])
```

## Systems and licenses

| System | Language | Issue tracker | Upstream license | Bugs | Lite |
|---|---|---|---|---:|---:|
{{SYSTEMS_TABLE}}

The annotations, metadata, and code in this release are under the MIT license.
Issue titles, descriptions, and attachments are reproduced from public issue
trackers and remain subject to the terms of those trackers and projects. The
dataset references paths and line numbers in the upstream repositories but
does not redistribute their source code; the licenses above are those of the
repositories' current default branches, and historical snapshots may differ.

## Limitations

The benchmark covers resolved bugs in public, open-source distributed systems
and evaluates code-level diagnosis only. Selection favors bugs whose fixes
support precise labeling. Because the issues are public, models may have seen
them during pretraining; the paper reports leakage controls and contamination
probes. See the paper and `croissant.json` for the full discussion.

## Citation

```bibtex
{{CITATION}}
```
