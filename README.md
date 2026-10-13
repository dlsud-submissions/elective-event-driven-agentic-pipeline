# Event-Driven Agentic Deployment Pipeline

Individual lab for *Applied Generative AI for IT Solution Development* (Week 11): **Orchestrating Event-Driven Agentic Pipelines**.

A git webhook drives three specialised agents through a **LangGraph `StateGraph`**, with a human approval gate before any database migration reaches production:

```text
webhook → Guard-Agent (tests, verdict) → Infra-Agent (build, preview migration)
        → HUMAN GATE → Infra-Agent (migrate, deploy) → Watchman-Agent (monitor, rollback)
```

## Deliverables

| # | Deliverable | Where |
|---|---|---|
| 1 | **Deployment Workflow Blueprint** (manual baseline vs. agentic pipeline, state handoffs) | [`docs/01-manual-baseline.md`](docs/01-manual-baseline.md) and [`docs/02-agentic-pipeline-blueprint.md`](docs/02-agentic-pipeline-blueprint.md) |
| 2 | **Framework Orchestration Code** (tools, state engine, human gate) | [`pipeline/`](pipeline/) |
| 3 | **Prompt Engineering & Correction Log** (system prompt, hallucination analysis, corrective prompt) | [`docs/logs.md`](docs/logs.md) |

Where each required item lives in deliverable 2:

| Requirement | File |
|---|---|
| Agent tools definition (tools bound per agent) | [`pipeline/tools.py`](pipeline/tools.py) (`make_tools`) |
| Agent logic (Guard, Infra, Watchman nodes) | [`pipeline/agents.py`](pipeline/agents.py) |
| State engine (`StateGraph`, conditional routing) | [`pipeline/graph.py`](pipeline/graph.py) (`build_graph`) |
| Human-in-the-loop gate | [`pipeline/agents.py`](pipeline/agents.py) (`approval_gate`, uses `interrupt()`) |
| Shared state and guardrail thresholds | [`pipeline/state.py`](pipeline/state.py) |

## Requirements

- Python 3.10 or newer (verified on 3.12; installs cleanly on 3.14 on Windows)
- No API key and no real infrastructure needed. All side effects go through a simulated runner by default.

## Setup

```bash
git clone <this repo>
cd elective-event-driven-agentic-pipeline
pip install -r requirements.txt
```

## Run the pipeline

```bash
python -m pipeline <scenario>
```

| Scenario | What it simulates | Expected final `status` |
|---|---|---|
| `happy` (default) | Tests pass, human approves, 3 clean monitoring samples | `healthy` |
| `tests_fail` | One failing test; Guard-Agent rejects the build, Infra never runs | `rejected_by_guard` |
| `rejected` | Tests pass, human **rejects** at the gate; no migration runs | `halted_by_human` |
| `migration_fail` | Approved migration errors; the down migration runs and deploy is skipped | `migration_failed` |
| `spike` | Deploy succeeds, then 5xx rate and p95 latency breach thresholds; one rollback | `rolled_back` |

Example (`python -m pipeline happy`):

```text
scenario=happy  status=healthy  verdict=pass
   ingest -> webhook_accepted
   guard -> build_pass
   infra -> image_built_migration_previewed
   human -> approved
   infra -> migration_applied
   infra -> deployed
   watchman -> sample_ok
   watchman -> sample_ok
   watchman -> sample_ok
   watchman -> release_healthy
```

The demo supplies the human decision automatically so the run completes. To see the pause, call `run_pipeline(app, event, thread_id)` without a `decision` argument: it returns a state containing `__interrupt__` with the migration plan. Resume it with `run_pipeline(app, event, thread_id, decision={"approval": "approved", "approver": "<your id>"})`.

## Run the tests

```bash
python -m pytest -q
```

Expected: `9 passed`.

> **Windows note:** use `python -m pytest`, not bare `pytest`. With a user-level install, pip places `pytest.exe` in `...\AppData\Roaming\Python\Python3xx\Scripts`, which is usually not on `PATH`, so `pytest -q` fails with "not recognized". `python -m pytest` avoids the problem without changing `PATH`.

## Optional: live LLM verdicts

By default the Guard-Agent uses a deterministic offline parser. To let a real model judge the console log:

```bash
pip install langchain langchain-anthropic
export PIPELINE_LLM="anthropic:<model-name>"     # PowerShell: $env:PIPELINE_LLM = "anthropic:<model-name>"
export ANTHROPIC_API_KEY="..."                   # PowerShell: $env:ANTHROPIC_API_KEY = "..."
python -m pipeline happy
```

The model can veto a build but can never approve one when the exit code is non-zero or tests failed.

## Safety design (summary)

- Only builds with `build_verdict == "pass"` reach the Infra-Agent.
- The migration runs only after a human approves **and** names an approver; the migrate node re-checks this.
- A failed migration or deploy runs the down migration.
- The only loop is the Watchman sampling loop (capped at 3 cycles). Rollback is terminal and happens at most once.
- `ShellRunner` refuses any binary outside an allowlist. It is not used by the demo; it exists for real execution.

Full tables are in [`docs/02-agentic-pipeline-blueprint.md`](docs/02-agentic-pipeline-blueprint.md).

## Repository layout

| Path | Purpose |
|---|---|
| `docs/01-manual-baseline.md` | Manual process, delays, handoffs, error points |
| `docs/02-agentic-pipeline-blueprint.md` | State diagram, handoff table, guardrail table |
| `docs/logs.md` | Prompt log and correction analysis |
| `docs/iterations/v1_naive.py` | Failing first draft referenced in the log (`GraphRecursionError`); do not use |
| `pipeline/` | LangGraph implementation |
| `tests/test_pipeline.py` | Behaviour tests |
| `requirements.txt` | Dependencies |
