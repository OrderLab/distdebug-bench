# Agent (mini-swe-agent)

To get **step limits**, **completion detection**, and **observability** (timing, step summaries), run tasks through our backend:

```bash
# From repo root
uv run python -m agent.mini_swe_agent_backend
# or from code:
# from agent.mini_swe_agent_backend import run_task
# result = run_task("Your task", max_steps=20)
```

If you run the mini-swe-agent CLI or `hello_world.py` directly (e.g. `uv run mini` or the library’s own entry point), that path does **not** use this backend, so you won’t get:

- A step limit (runs can loop until the model sends an exit, which often never happens with “every response must include one tool call”).
- Detection of `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` as a successful exit.

Use `run_task()` or `python -m agent.mini_swe_agent_backend` so the run stops after `max_steps` or when the completion command is observed.
