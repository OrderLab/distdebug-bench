# DistDebug-Bench

**DistDebug-Bench: Can LLM Agents Diagnose Bugs in Distributed Systems?**\
NeurIPS 2026, Track on Evaluations and Datasets

Yuxuan Jiang, Chenglin Liu, John Rose, Mason Miller, Ruthesh Thavamani,
Juntao Wu, Yinwei Huang, Jiahao Xu, Peng Huang\
University of Michigan

[Website](https://distdebug.com) ·
[Leaderboard](https://distdebug.com/leaderboard/) ·
[Paper](https://distdebug.com/assets/paper.pdf) ·
[Dataset](https://huggingface.co/datasets/OrderLab/distdebug-bench) ·
[Agent traces](https://huggingface.co/datasets/OrderLab/distdebug-bench-traces) ·
[Submit results](https://distdebug.com/submit/)

DistDebug-Bench measures whether coding agents can diagnose real failures in
distributed systems. Given an incident report, its logs or stack traces, and the
repository as it stood before the fix, the agent must name the source code
blocks that caused the failure. Diagnoses are scored against human-labeled
root-cause code, not by whether a patch makes a test pass.

The benchmark contains 120 real bugs from 11 widely used distributed systems,
covering 5 programming languages and 10 failure patterns, such as concurrency
races, leader-election faults, replication inconsistencies, and resource leaks.
Across the 17 agent configurations in the paper, the strongest reaches the right
file 84% of the time but only 68% of the root-cause code blocks. See the
[leaderboard](https://distdebug.com/leaderboard/) for every configuration.

## Quick start: diagnose one incident

You need Linux with [bubblewrap](https://github.com/containers/bubblewrap),
Python 3.10 or later, and an API key for your model provider. This runs
Mini-SWE-Agent with GPT-5.4 on ZooKeeper-2845 once and scores the diagnosis:

```bash
git clone https://github.com/OrderLab/distdebug-bench.git
cd distdebug-bench
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[agent]"
export OPENAI_API_KEY=...        # or the provider key the config needs

python -m distdebug_bench.run --config configs/mini-swe-agent/gpt-5.4.yaml \
    --bug zookeeper-2845 --runs 1
python -m distdebug_bench.score results/mini-swe-agent-gpt-5.4 --attempts 1
```

The attempt is saved under `results/mini-swe-agent-gpt-5.4/zookeeper-2845/`.
`configs/mini-swe-agent/` has a config for each model in the paper.

## Running the full benchmark

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
  sandbox. `distdebug_bench.leakage` flags attempts whose model-visible text
  names the original issue; the paper discards and reruns them.
- **Attempts.** Each bug is attempted until it has three schema-valid
  diagnoses. Scoring takes the first three in run order, averages them within
  the bug, then averages equally over bugs.

Each attempt is saved under `results/<config>/<bug>/run-NN/` as
`trajectory.json`, `diagnosis.json`, `session_stats.json` (API calls, tokens,
cost), and, after scoring, `diagnosis_scores.json`.

## Evaluating your own agent

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

## Submitting results to the leaderboard

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

## Reproducing the paper's results

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

## Reference

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
├── configs/mini-swe-agent/                # The paper's model configurations
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

## License and citation

See `LICENSE`. The benchmark annotations and code are released under the MIT
license. Issue reports and attachments come from public issue trackers and
remain subject to their terms; upstream source code referenced by
`report.json` and `blocks.json` is governed by each project's own license.

```bibtex
@inproceedings{jiang2026distdebugbench,
  title     = {{DistDebug-Bench}: Can {LLM} Agents Diagnose Bugs in Distributed Systems?},
  author    = {Jiang, Yuxuan and Liu, Chenglin and Rose, John and Miller, Mason and Thavamani, Ruthesh and Wu, Juntao and Huang, Yinwei and Xu, Jiahao and Huang, Peng},
  booktitle = {Advances in Neural Information Processing Systems (NeurIPS)},
  year      = {2026}
}
```
