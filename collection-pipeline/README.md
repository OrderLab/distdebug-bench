## Issue labeling pipeline

### Setup
```bash
uv sync   # or: pip install -e .
```
Copy `utils/env.example` to `utils/.env` and set:
- `OUT_PATH` – output directory (e.g. `output`)
- `TEST_SYSTEMS_ROOT_PATH` – parent dir for cloned repos
- `GITHUB_TOKEN` – for GitHub API (optional for some steps)

### Run
1. Put a JSON file named `<project_name>.json` in `output/issues`.
2. Run:
   ```bash
   uv run distdebug-collection --project <project_name>
   # or: python main.py --project <project_name>
   ```

### Outputs
- **Patches:** `output/diffs/<project>/`
- **Prompts (agent input):** `output/prompts/`
- **Buggy blocks CSV:** `output/issues/<project_name>_blocks.csv`

### Utilities
- `utils/extract_issues_json.py` – convert an issue CSV to `<project_name>.json`

### Agent

Uses [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) to run an LLM agent that analyzes prompts and selects root-cause code blocks.

**Prerequisites:** Run the collection pipeline first so `output/prompts/` exists. The project repo must be cloned (e.g. under `TEST_SYSTEMS_ROOT_PATH`).

**Run (from collection-pipeline directory):**
```bash
uv run python -m agent.mini_swe_agent main -i <issue_id> -p <project_name>
# Example:
# uv run python -m agent.mini_swe_agent main -i ZOOKEEPER-2581 -p zookeeper -m openai/gpt-5-mini
```

**Options:**
- `-i` / `--issue-id` – Issue ID (e.g. ZOOKEEPER-2581)
- `-p` / `--project-name` – Project name (e.g. zookeeper)
- `-m` / `--model` – Model name (or set `MSWEA_MODEL_NAME` env var)

**Config:** `agent/default.yaml` (system prompt, step limit, working dir)

**Outputs:**
- `output/agent/<issue_id>_log.json` – Full run log
- `output/agent/<issue_id>-answer.json` – Selected root-cause blocks (JSON)

### Stats visualization

After running `utils/compare_blocks.py` and saving output to `stats`, you can:

- **Generate plots:** Run `python visualize_stats.py` to create bar charts and summary metrics. Requires `matplotlib` (`pip install matplotlib` or `uv pip install -e ".[viz]"`).

   Outputs are saved to `output/stats_viz/`:
   - `precision_by_issue_*.png` – Precision per issue
   - `line_precision_by_issue_*.png` – Line precision per issue
   - `summary_metrics_by_project.png` – Containment, function coverage, precision across projects
   - `containment_*.png` – Pie charts of contained vs missing blocks