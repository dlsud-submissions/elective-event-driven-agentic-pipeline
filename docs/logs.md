# Prompt Engineering & Correction Log

All prompts below were issued to the AI assistant while building this repository. Outputs are condensed to what mattered; the code itself lives in [`pipeline/`](../pipeline) and the failed first draft is preserved in [`docs/iterations/v1_naive.py`](iterations/v1_naive.py).

## 1. Prompt and output log

| # | Stage | Prompt (verbatim or near-verbatim) | AI output (summary) | Outcome |
|---|---|---|---|---|
| P1 | Scope | "Read the instructions PDF and the GitHub repo. Create `docs/` for the deployment workflow blueprint md files. Use LangChain StateGraph (Python) for the 2nd deliverable. Create `docs/logs.md` where prompts and prompt outputs are documented in table format." | Read the lab brief and repo (`README.md`, `LICENSE`, `.gitignore` only). Found that the manual-steps list in the brief is missing from the text version. Planned `docs/`, `pipeline/`, `tests/`. | Assumed standard manual release steps and flagged the assumption in `docs/01-manual-baseline.md`. |
| P2 | State logic (system prompt) | See **section 2** (System Prompt). | First-iteration LangGraph draft: 4 nodes, conditional edges, `rollback → infra` edge. | Ran it. **Failed** (see section 3). |
| P3 | Correction | See **section 3** (Corrective Prompt). | Rewrote graph: terminal rollback, bounded watch loop, `interrupt()` gate, checkpointer, allowlisted tools, typed state with audit reducer. | 9/9 tests pass; 5 scenarios run. |
| P4 | Tools | "Bind tools per agent by least privilege. Infra-Agent may run console commands only through an allowlisted runner. Every tool must work offline via a simulated runner with fault injection." | `tools.py`: `@tool` functions in `make_tools(runner)`, `SimulatedRunner` + `Scenario` switches, `ShellRunner` with binary allowlist. | Added test for blocked `rm -rf /` (exit 126). |
| P5 | Guard hardening | "The Guard-Agent parses raw logs with an LLM. Make sure the LLM can never approve a build whose tests actually failed." | `brain.py` + `guard_agent`: deterministic parse of exit code / failed count; LLM verdict may only veto. Offline `HeuristicBrain` for tests. | Rule documented in blueprint section 5. |
| P6 | Human gate | "Add a step where the pipeline pauses for a human engineer before database migrations reach production. Approval must be attributable." | `approval_gate` using `interrupt()`; resume payload `{approval, approver}`; empty approver counts as rejection; `infra_migrate` re-asserts approval. | Tests: pause-before-migrate, rejection, approval without approver. |
| P7 | Verification | "Write tests proving: failed tests never reach infra, no migration before approval, a migration failure triggers the DOWN migration, an error spike causes exactly one rollback." | `tests/test_pipeline.py` (9 tests) using the runner's call log as evidence. | 9 passed. |
| P8 | Defect found while testing | (Self-review of simulated runner) "Does `alembic upgrade --sql` ever match its own branch?" | `startswith("alembic upgrade")` matched before the `--sql` branch, so the preview returned the wrong string. Reordered the branches. | Fixed before the first full run. |
| P9 | Documentation | "Write the blueprint: manual baseline vs. agentic pipeline, mark where data shifts between states." | `01-manual-baseline.md` and `02-agentic-pipeline-blueprint.md` with a Mermaid state diagram, a handoff table, and a guardrail table. | Included in `docs/`. |

## 2. System prompt used to generate the state workflow logic (P2)

```text
You are a senior DevOps engineer and LangGraph expert. Design a LangGraph StateGraph
(Python) for an event-driven production deployment pipeline with three specialised
agents:
  1. Guard-Agent: triggered by a git webhook; runs the test suite, parses raw console
     output and decides pass/fail.
  2. Infra-Agent: runs only if Guard passes; builds the image, applies SQL/EF
     migrations, deploys the container.
  3. Watchman-Agent: after deploy, samples runtime HTTP error rate and triggers an
     automatic rollback if thresholds are breached.
Requirements: typed shared state; one node per agent; conditional edges as decision
gates; pass the commit hash from Guard to Infra; return only valid, runnable code.
```

## 3. Structural hallucination / logic loop and the corrective prompt

### 3.1 What the first iteration generated

`docs/iterations/v1_naive.py` wired the rollback as:

```python
g.add_edge("rollback", "infra")      # "re-deploy previous version"
app = g.compile()                    # no checkpointer, no human gate
```

### 3.2 Observed failure (real run)

```text
langgraph.errors.GraphRecursionError: Recursion limit of 10007 reached without hitting a stop condition.
```

### 3.3 Analysis

| Problem in iteration 1 | Why it is wrong | Consequence |
|---|---|---|
| `rollback → infra → watchman → rollback` cycle | After rollback the Infra-Agent redeploys, the Watchman sees the same fault and rolls back again; no state records that a rollback already happened. | Unbounded loop (hit the recursion limit) and, in production, repeated deploys against a failing service. |
| Rollback target not stored in state | The model assumed "previous version" without capturing it before deploy. | Nothing to roll back to; a hallucinated data dependency. |
| No human gate and no checkpointer | The brief requires a pause before migrations; `interrupt()` also fails without a checkpointer. | Migration would run unattended. |
| Router returned `END` for a failed guard with no recorded reason | No `verdict_reason` or audit trail. | Unexplainable failures. |
| Simulated `healthy=False` baked into the state | Business logic faked inside the state instead of a tool call. | Not testable against real tools. |

### 3.4 Corrective framework prompt (P3)

```text
The previous graph has a cycle: rollback -> infra -> watchman -> rollback with no
termination condition (GraphRecursionError). Rewrite it with these constraints:
1. Rollback is a TERMINAL node and must never route back to the Infra-Agent.
2. Add state fields rollback_count and watch_cycles; the only permitted cycle is
   watchman -> watchman, bounded by max_watch_cycles. Cap rollbacks at 1, then end
   with status "escalated"/"rolled_back" so a human is paged.
3. Capture previous_image_tag in the Infra-Agent BEFORE deploying and store it in
   state; the Watchman rollback must read that key, not recompute it.
4. Insert a human-in-the-loop node using langgraph.types.interrupt() before the
   migration node, compile with a checkpointer, and require approval == "approved"
   plus a non-empty approver to continue.
5. Replace faked state flags with tool calls bound per agent (least privilege).
6. Add an append-only audit list and a verdict_reason on every terminal path.
Return complete runnable code and list every conditional edge with its routing function.
```

### 3.5 Result

| Check | Before | After |
|---|---|---|
| Error spike scenario | `GraphRecursionError` | `status=rolled_back`, exactly one `kubectl rollout undo` (test `test_error_spike_triggers_single_rollback`) |
| Migration without approval | Possible | Impossible (tests: pause, rejection, missing approver) |
| Rollback target | Undefined | `previous_image_tag` captured before deploy |
