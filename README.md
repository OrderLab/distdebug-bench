<p align="center">
  <a href="https://distdebug.com">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/logo/logo-dark.svg">
      <img src="assets/logo/logo.svg" alt="DistDebug-Bench" width="520">
    </picture>
  </a>
</p>

<h3 align="center">Can LLM agents diagnose bugs in distributed systems?</h3>

<p align="center">
  <a href="https://distdebug.com/leaderboard/"><img src="https://img.shields.io/badge/leaderboard-live-FF4F2E?style=flat-square" alt="Leaderboard"></a>
  <a href="https://distdebug.com/assets/paper.pdf"><img src="https://img.shields.io/badge/paper-PDF-B31B1B?style=flat-square" alt="Paper"></a>
  <a href="https://huggingface.co/datasets/OrderLab/distdebug-bench"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20dataset-distdebug--bench-FFD21E?style=flat-square" alt="Hugging Face dataset"></a>
  <a href="https://huggingface.co/datasets/OrderLab/distdebug-bench-traces"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20traces-6%2C120%20attempts-FFD21E?style=flat-square" alt="Agent traces"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22C55E?style=flat-square" alt="MIT license"></a>
  <a href="https://github.com/OrderLab/distdebug-bench/stargazers"><img src="https://img.shields.io/github/stars/OrderLab/distdebug-bench?style=flat-square&color=0B1220" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="https://distdebug.com"><b>Website</b></a> ·
  <a href="https://distdebug.com/leaderboard/"><b>Leaderboard</b></a> ·
  <a href="https://distdebug.com/assets/paper.pdf"><b>Paper</b></a> ·
  <a href="https://huggingface.co/datasets/OrderLab/distdebug-bench"><b>Dataset</b></a> ·
  <a href="https://huggingface.co/datasets/OrderLab/distdebug-bench-traces"><b>Traces</b></a> ·
  <a href="https://distdebug.com/submit/"><b>Submit</b></a>
</p>

---

DistDebug-Bench is a benchmark for evaluating whether LLM agents can diagnose
real-world failures in distributed systems. Each task gives the agent a bug report
from the system's issue tracker, the logs and stack traces attached to it, and the
source code as it was before the fix. The agent has to find the code that caused
the failure. We score its diagnosis against human-labeled root-cause code blocks,
rather than by whether a patch passes tests.

Bugs in distributed systems are hard to diagnose. They depend on timing, message
ordering, and partial failures across nodes, are often difficult to reproduce, and
rarely come with a failing test. In our evaluation of 17 agent configurations, the
best agents find the right files 86% of the time but cover at most 73% of the
root-cause code blocks. Results also vary between runs: on 12–30% of bugs, block
coverage differs by more than 20 points across three attempts.

## ✨ Highlights

- **120 real bugs** from the issue trackers of 11 distributed systems, including
  ZooKeeper, Kafka, HDFS, etcd, Kubernetes, Ray, and TiKV. They cover 5 languages
  and 10 failure patterns, such as concurrency races, leader-election faults,
  replication inconsistencies, and resource leaks.
- **Human-labeled root causes.** Each bug comes with the code blocks that caused it,
  so a diagnosis is scored at the file, function, line, and block level.
- **No access to the fix.** The agent's workspace contains only the pre-fix snapshot
  (no later commits, branches, tags, or remotes), and its commands run offline in a
  [bubblewrap](https://github.com/containers/bubblewrap) sandbox. A leakage check
  flags attempts that mention the original issue, its fix pull request, or its fix commit.
- **Works with any agent.** We include Mini-SWE-Agent configs for 13 models in
  [`configs/`](configs/mini-swe-agent/), plus Codex CLI and OpenCode harnesses.
  Other agents only need to write a `diagnosis.json`.
- **Reproducible results.** All 6,120 scored attempts from the paper are on Hugging
  Face, and `paper/reproduce.py` rebuilds the paper's main table and figures from them.
- **A Lite subset** of 34 bugs for cheaper runs.

## 🏆 Leaderboard

Top configurations on the full benchmark (v1.1 labels, 120 bugs, Mini-SWE-Agent, mean
of three attempts per bug). **Block coverage** is the primary metric.

| # | Model | Block coverage | File coverage | Tokens / run |
| :-: | :-- | :-: | :-: | --: |
| 1 | GPT-6 Astra | **72.6** <sub>[65.7, 79.3]</sub> | 86.1 | 130K |
| 2 | Claude Opus 5.5 | **71.8** <sub>[65.2, 78.2]</sub> | 84.5 | 149K |
| 3 | GPT-5.5 | **70.0** <sub>[63.1, 76.7]</sub> | 86.2 | 474K |
| 4 | GPT-5.4 | 66.6 <sub>[59.7, 73.4]</sub> | 81.0 | 687K |
| 5 | GPT-5.3-Codex | 63.2 <sub>[55.9, 70.4]</sub> | 79.9 | 417K |

<sub>Brackets are 95% bug-bootstrap confidence intervals.</sub>

The [leaderboard](https://distdebug.com/leaderboard/) lists all 17 configurations.
To add yours, see [Submitting to the leaderboard](#-submitting-to-the-leaderboard).

## 🧩 The task

**Input.** The issue title and description, its attachments (logs, stack traces,
analyses), and the system's source code at the pre-fix snapshot. The agent works in
a sandboxed shell with no network access.

**Output.** A `diagnosis.json` that describes the cause of the failure and lists the
code blocks responsible:

```json
{
  "bug_cause_description": "Leader election retains a stale ZKDatabase ...",
  "buggy_blocks": [
    {
      "file": "src/java/main/org/apache/zookeeper/server/ZooKeeperServer.java",
      "function": "ZooKeeperServer.shutdown",
      "start_line": 555,
      "end_line": 558,
      "bug_contribution": "...",
      "patch": "..."
    }
  ]
}
```

The scorer compares the predicted blocks with the labeled ones and reports
precision and coverage at four levels (see
[metrics](#evaluation-metrics-produced-by-compute_correctness_report)).

### Systems

| System | Language | Bugs | | System | Language | Bugs |
| :-- | :-- | --: | - | :-- | :-- | --: |
| [Ray](https://github.com/ray-project/ray) | Python, C++ | 21 | | [Kafka](https://github.com/apache/kafka) | Java | 8 |
| [Elasticsearch](https://github.com/elastic/elasticsearch) | Java | 19 | | [etcd](https://github.com/etcd-io/etcd) | Go | 8 |
| [ZooKeeper](https://github.com/apache/zookeeper) | Java | 18 | | [Ignite 3](https://github.com/apache/ignite-3) | Java | 7 |
| [TiKV](https://github.com/tikv/tikv) | Rust | 11 | | [HBase](https://github.com/apache/hbase) | Java | 5 |
| [HDFS](https://github.com/apache/hadoop) | Java | 10 | | [Ignite](https://github.com/apache/ignite) | Java | 3 |
| [Kubernetes](https://github.com/kubernetes/kubernetes) | Go | 10 | | | | |

## 🚀 Quick start

You need Linux with [bubblewrap](https://github.com/containers/bubblewrap),
Python 3.10 or later, and an API key for your model provider. The commands below
run Mini-SWE-Agent with GPT-5.4 on one bug, ZooKeeper-2845, and score the result:

```bash
git clone https://github.com/OrderLab/distdebug-bench.git
cd distdebug-bench
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[agent]"
export OPENAI_API_KEY=...        # or the provider key the config needs

python -m distdebug_bench.run --config configs/mini-swe-agent/gpt-5.4.yaml \
    --bug zookeeper-2845 --runs 1
python -m distdebug_bench.score results/mini-swe-agent-gpt-5.4 --attempts 1
```

The attempt is saved under `results/mini-swe-agent-gpt-5.4/zookeeper-2845/`.
`configs/mini-swe-agent/` has a config for each model in the paper.

To load the data without running an agent, `pip install -e .` is enough. It has
no dependencies:

```python
from distdebug_bench import get_bug

bug = get_bug(".", "zookeeper-2845")
print(bug.title)                # Data inconsistency issue due to retain database in leader election
print(bug.ground_truth_blocks)  # the labeled root-cause code blocks
```

## 🧪 Running the full benchmark

The runner reproduces the paper's Mini-SWE-Agent evaluation. One config per
model in the paper's Table 1 is in `configs/mini-swe-agent/`.

```bash
pip install -e ".[agent]"
export OPENAI_API_KEY=...        # or the provider key the config needs

# Run every bug until it has three successful attempts (resumable).
python -m distdebug_bench.run --config configs/mini-swe-agent/gpt-5.4.yaml --parallel 8

# Flag attempts that named the original issue, set them aside, and rerun them.
python -m distdebug_bench.leakage results/mini-swe-agent-gpt-5.4 --quarantine
python -m distdebug_bench.run --config configs/mini-swe-agent/gpt-5.4.yaml --parallel 8

# Score and aggregate as in the paper.
python -m distdebug_bench.score results/mini-swe-agent-gpt-5.4
```

Use `--subset lite`, `--bug zookeeper-2845`, or `--system kafka` to run fewer
bugs, and `--runs 1` for a single attempt per bug. Commands run inside
[bubblewrap](https://github.com/containers/bubblewrap), which must be installed
(Linux only).

The protocol, as in the paper:

- **Agent.** Mini-SWE-Agent 2.2.8 with the prompt in
  `distdebug_bench/mini_swe_agent_prompt.yaml`, a $3 cost limit per attempt, and
  a 60-second limit per command.
- **Input.** The issue title and description, the issue's attachments (copied to
  `.artifacts/` in the workspace), and the system's source at the pre-fix
  snapshot. The agent writes `diagnosis.json` and checks it with
  `.validate_diagnosis.py`.
- **Leakage controls.** Each workspace has a random name and contains only the
  snapshot and its ancestors: no branches, tags, remotes, reflog, or later
  commits, so the fix is unreachable. `chafa` and `img2txt` are disabled in the
  sandbox. `distdebug_bench.leakage` flags attempts in which the agent saw or
  typed the original issue or its fix (see [Leakage check](#leakage-check));
  flagged attempts are discarded and rerun.
- **Attempts.** Each bug is attempted until it has three schema-valid
  diagnoses. Scoring takes the first three in run order, averages them within
  the bug, then averages equally over bugs.

Each attempt is saved under `results/<config>/<bug>/run-NN/` as the agent's
trace (`trajectory.json`, `codex_exec.log`, or `opencode_cli.log`),
`diagnosis.json`, `session_stats.json` (API calls, tokens, cost), and, after
scoring, `diagnosis_scores.json`.

### Harnesses

The config selects the harness. All three give the agent the same task text,
attachments, validator, and instructions, in the same anonymized workspace.

| Harness | Configs | Trace | Tested version |
| --- | --- | --- | --- |
| Mini-SWE-Agent | `configs/mini-swe-agent/` | `trajectory.json` | 2.2.8 |
| Codex CLI | `configs/codex/` | `codex_exec.log` | 0.117.0 |
| OpenCode | `configs/opencode/` | `opencode_cli.log` | 1.4.6 |

```bash
npm install -g @openai/codex@0.117.0   # or set `executable:` in the config
python -m distdebug_bench.run --config configs/codex/gpt-5.2-codex.yaml --bug zookeeper-2845 --runs 1
```

A Codex CLI or OpenCode config names the model, an OpenAI-compatible endpoint
(`base_url`), and the environment variable that holds its key (`api_key_env`).

### Isolation

- **Mini-SWE-Agent.** The model is called from the host. Every command the agent
  runs executes in bubblewrap with its own network namespace, which has only
  loopback. At startup the sandbox tries an outbound connection and refuses to
  run if it succeeds.
- **Codex CLI and OpenCode.** The whole CLI runs in bubblewrap without network
  access. Its only way out is a gateway on the host that forwards model API
  requests (Responses, Chat Completions, model list) to `base_url`. The CLI holds
  a placeholder key, and the gateway adds the real one, so the key never enters
  the sandbox. The gateway serves only the configured model and rejects requests
  that enable hosted tools such as web search, file search, code interpreter, or
  remote MCP. It answers proxy requests
  for any other host with `403`, and direct connections have no route. Every
  decision is logged to `proxy.log` in the run directory.
- **No user configuration.** Each CLI starts from an empty home directory, so no
  user settings, MCP servers, hooks, plugins, skills, or credentials load. Codex
  runs with `web_search = "disabled"` and its `workspace-write` sandbox for the
  commands it runs. OpenCode runs with `webfetch`, `websearch`, and `codesearch`
  disabled and its model declared explicitly.
- **Nothing else is visible.** The sandbox sees the workspace, the temporary
  home, the CLI's install directory, and system directories such as `/usr` and
  `/etc`. The user's home directory and the benchmark's labels are not visible.

### Leakage check

`python -m distdebug_bench.leakage <results>` scans what the agent wrote and
read, meaning its reasoning, commands, and tool output, in all three trace
formats. It flags an attempt when it finds any of these:

- the issue key, such as `KAFKA-13407`, outside file paths;
- the issue or fix-PR number in a reference, such as `#11563`, `issues/13407`,
  `pull/11563`, or `PR 11563`;
- the bare issue or fix-PR number typed in a command or in the model's own text,
  unless it reads as a line number, range, or offset;
- the fix commit hash.

The task prompt is not scanned. Numbers that appear in the task are ignored, and
so are line numbers in source listings. The check writes `leakage.json`, and
`--quarantine` moves flagged runs aside so that the runner replaces them.

## 🛠️ Evaluating your own agent

Any agent or harness can be evaluated. Give it what the runner gives
Mini-SWE-Agent: the issue title and description, the issue's attachments, and
the system's source at the pre-fix snapshot, with no internet access. For each
attempt, have it write `<bug>/run-NN/diagnosis.json` in the benchmark schema
(checked by `distdebug_bench/validate_diagnosis.py`) and a human-readable
`<bug>/run-NN/trajectory.json`. Then run the leakage guard and the scorer on the
results directory, as above.

The [Python API](#python-api) loads a bug and its task prompt, checks out the
pre-fix snapshot so that the fix is unreachable, and scores a single
diagnosis.

## 📤 Submitting to the leaderboard

The [leaderboard](https://distdebug.com/leaderboard/) lists every evaluated
configuration, and the [submission guide](https://distdebug.com/submit/)
summarizes the steps below.

1. Run every bug in the full benchmark (or in Lite) until it has three valid
   attempts. Use `distdebug_bench.run`, or your own agent writing the same layout:
   `<bug>/run-NN/diagnosis.json` (checked by `distdebug_bench/validate_diagnosis.py`)
   and a human-readable `<bug>/run-NN/trajectory.json`.
2. Run `python -m distdebug_bench.leakage <results> --quarantine` and rerun the
   flagged attempts.
3. Copy `submissions/metadata.template.yaml`, fill it in, and package the run:

   ```bash
   python -m distdebug_bench.submit package results/<run> --metadata metadata.yaml
   ```

   This scores the run and writes `submissions/<date>_<agent>__<model>/` (metadata,
   scores, and diagnoses) plus a trajectories archive.
4. Upload the archive anywhere public (Google Drive, Hugging Face, Zenodo, ...),
   set `trajectories_url` in the submission's `metadata.yaml`, and check it:

   ```bash
   python -m distdebug_bench.submit check submissions/<date>_<agent>__<model>
   ```

5. Open a pull request adding the submission directory, or email
   jyuxuan@umich.edu with the subject `[DistDebug-Bench-Submit] <agent> + <model>`
   and links to the submission directory and the trajectories.

Rules:

- Every bug in the chosen subset needs three valid attempts; scores are
  aggregated by `distdebug_bench.score`. Report Lite results separately; the
  [leaderboard](https://distdebug.com/leaderboard/) currently lists full-benchmark runs.
- The agent must not access the original issues, their fixes, or this
  benchmark's repository or website. We rerun the leakage guard and review
  trajectories.
- Describe the system in a paper, technical report, or blog post (`report_url`).
- We rescore every submission from its diagnoses before listing it.

## 🔬 Reproducing the paper's results

The traces behind the paper's Table 1 are on Hugging Face at
[OrderLab/distdebug-bench-traces](https://huggingface.co/datasets/OrderLab/distdebug-bench-traces):
17 configurations, 120 bugs, and the three scored attempts per bug, for 6,120
attempts. `paper/reproduce.py` rescores every diagnosis with
`distdebug_bench.score` and aggregates the scores with the paper's scripts. It
rebuilds Table 1 and Figures 2 and 3 in `paper/out/` and checks them against
`paper/reference/`:

```bash
pip install -e ".[paper]"
hf download OrderLab/distdebug-bench-traces --repo-type dataset --local-dir traces
python paper/reproduce.py traces
```

It prints whether each configuration's scores, token use, and confidence
intervals match the paper. Pass `--use-released-scores` to aggregate the
stored `diagnosis_scores.json` files instead of rescoring.

## 📚 Reference

### Repository structure

```
.
├── benchmark/
│   ├── systems.json
│   ├── partition.json
│   ├── bug_classifications.json
│   ├── bug_difficulty_characterizations.json
│   ├── lite.txt                           # DistDebug-Bench Lite: 34-bug subset
│   └── bugs/<system>/<bug-id>/
│       ├── report.json
│       ├── blocks.json
│       └── artifact_dir/                  # optional supporting logs and traces
│
├── distdebug_bench/                       # Loader, evaluator, and agent runner
├── assets/logo/                           # Logo files (SVG and PNG)
├── configs/mini-swe-agent/                # The paper's model configurations
├── configs/codex/, configs/opencode/      # Codex CLI and OpenCode configurations
├── paper/                                 # Rebuilds the paper's Table 1 and Figures 2-3 from the traces
├── submissions/                           # Leaderboard submissions
├── release/                               # Hugging Face export and Croissant metadata
├── tests/
├── collection-pipeline/                   # Pipeline used to construct the benchmark
└── utils/
```

### Per-bug file layout under `benchmark/bugs/`

Each bug directory at `benchmark/bugs/<system>/<id>/` contains two JSON
files and an optional artifacts directory.

#### `report.json`

Bug metadata and natural-language description.

```json
{
  "id": "2845",
  "system": "zookeeper",
  "branch": "release-3.5.3",
  "title": "Data inconsistency issue due to retain database in leader election",
  "description": "In ZOOKEEPER-2678, the ZKDatabase is retained ...",
  "issue_link": "...",
  "fix_link": "...",
  "severity": "Critical",
  "root_cause_type": "Concurrency",
  "bug_complexity": "Hard",
  "distributed": "Yes",
  "contains_analysis": "Yes",
  "contains_logs": "No",
  "contains_stack_traces": "No",
  "artifact_dir": "artifact_dir"
}
```

`branch` is the upstream branch to check out before running an agent.
`artifact_dir`, when present, is a relative path to supporting logs and
traces.

#### `blocks.json`

The ground-truth root-cause code blocks. A bug may have one or more blocks
spanning multiple files.

```json
[
  {
    "file": "src/java/main/org/apache/zookeeper/server/ZooKeeperServer.java",
    "function": "ZooKeeperServer.shutdown",
    "start_line": 555,
    "end_line": 558
  }
]
```

Line numbers may appear as strings or integers depending on the curator's
tooling; the loader coerces both to `int`.

### Python API

Python 3.10 or later is required. We recommend installing into a
virtual environment so that subsequent `python` invocations resolve to
the project's interpreter:

```bash
git clone https://github.com/OrderLab/distdebug-bench.git
cd distdebug-bench
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

#### Loading a bug from the benchmark

```python
from distdebug_bench import load_benchmark, get_bug

bench = load_benchmark(".")
print(len(bench.systems), "systems,", len(bench.lite_bug_ids), "Lite bugs")

bug = get_bug(".", "zookeeper-2845")
print(bug.title)
print(bug.branch)
print(bug.ground_truth_blocks)
print(bug.task_prompt)

for bug in bench.iter_bugs("all"):  # or "lite"
    print(bug.name, len(bug.ground_truth_blocks))
```

#### Subsets

| Subset | Bugs | Use |
| ------ | ---: | --- |
| `all`  | 120  | The full benchmark, as evaluated in the paper. |
| `lite` | 34   | DistDebug-Bench Lite: a subset selected for report and label quality and lower evaluation cost. Report Lite results separately. |

#### Checking out the buggy revision of the upstream system

```python
from distdebug_bench import ensure_system_repo, temporary_checkout

system = bench.systems[bug.system]
cache = ensure_system_repo(system, "./.cache/repos")
with temporary_checkout(bug, cache, "./.cache/workspaces") as workdir:
    # `workdir` has a random name and holds only `bug.branch` and its
    # ancestors, so later commits (including the fix) are unreachable.
    # The directory is removed automatically on exit.
    ...
```

#### Scoring an agent's predicted root-cause blocks

```python
from distdebug_bench import compute_correctness_report
from distdebug_bench.models import BuggyBlock

predicted = [BuggyBlock(
    file="src/java/main/org/apache/zookeeper/server/ZooKeeperServer.java",
    function="ZooKeeperServer.shutdown",
    start_line=555, end_line=558,
)]
report = compute_correctness_report(bug.ground_truth_blocks, predicted)
# To also score mention coverage as in the paper, pass the raw text of the
# agent's diagnosis.json as a third argument.
print(report)
```

### Evaluation metrics produced by `compute_correctness_report`

This is the scorer behind the paper's results: rescoring every attempt in
the paper reproduces its stored scores and Table 1 exactly. Line ranges are
half-open, `[start_line, end_line)`.

| Metric | Unit | A prediction matches the ground truth when |
| ------ | ---- | ------------------------------------------ |
| `file_precision`, `file_coverage` | distinct files | the paths are equal after normalization, or one contains the other |
| `function_precision`, `function_coverage` | distinct functions | the names agree after canonicalization to lowercase `class.method` (argument lists, Go receivers, and `::`/`#`/`$` delimiters are handled; a bare method matches its qualified name) |
| `line_precision`, `line_coverage` | lines | overlapping line numbers in a matching file |
| `block_precision`, `block_coverage` | blocks | the ranges overlap within 5 lines in a matching file (function names are not used) |
| `strict_block_coverage` | blocks | the ranges overlap without tolerance in a matching file |
| `mention_coverage` | blocks | the ground-truth function appears in the diagnosis text (generic names such as `run` must appear as `class.method`) |

Precision divides by predicted files, functions, lines, or blocks; coverage
divides by ground-truth ones. `block_coverage` is the paper's main metric.

## 🗒️ Changelog

- **v1.1 (2026-11-07).** Fixes the gold labels of the eight bugs reported in
  [#2](https://github.com/OrderLab/distdebug-bench/issues/2) and corrects five more labels.
  - The blocks of etcd-14110, etcd-14325, etcd-14382, etcd-15247, hbase-28830 and
    ray-14883 had been labeled against the fix's base branch. They are now mapped
    onto each bug's pinned revision.
  - tikv-17830's pinned tag already contained the fix. It is re-pinned to the parent
    of the fix on release-7.1 (`b00db656`).
  - tikv-19220 pointed to a commit outside the TiKV repository. It is re-pinned to
    the parent of the fix on master (`6bb8ff37`), and its block now marks the two
    lines that cause the failure.
  - tikv-18498's block is moved onto its pinned v8.5.1 lines.
  - ray-15990, ray-30229, ignite3-21776 and ignite3-22597 name the function that
    contains each labeled block. Their line ranges are unchanged.
  - ignite3-22597 and hbase-28830 gain fix links.
  - The leaderboard and the paper use v1.1. Block coverage rises by 1.4 to 5.4 points
    per configuration.
- **v1.0 (2026-11-01).** Initial release.

## 🤝 Contributing

To suggest a bug for the benchmark, open an issue with the
[Bug Entry template](https://github.com/OrderLab/distdebug-bench/issues/new?template=bug-entry.md).
We also welcome pull requests, including scorer fixes and configs for new agents.

## 📄 License

The benchmark annotations and code are released under the [MIT license](LICENSE).
Issue reports and attachments come from public issue trackers and remain subject
to their terms; upstream source code referenced by `report.json` and `blocks.json`
is governed by each project's own license.

## 📝 Citation

If you use DistDebug-Bench in your research, please cite:

```bibtex
@inproceedings{jiang2026distdebugbench,
  title     = {{DistDebug-Bench}: Can {LLM} Agents Diagnose Bugs in Distributed Systems?},
  author    = {Jiang, Yuxuan and Liu, Chenglin and Rose, John and Miller, Mason and Thavamani, Ruthesh and Wu, Juntao and Huang, Yinwei and Xu, Jiahao and Huang, Peng},
  booktitle = {Advances in Neural Information Processing Systems (NeurIPS)},
  year      = {2026}
}
```

<p align="center">
  <sub>If DistDebug-Bench is useful to you, a ⭐ helps others find it.</sub>
</p>
