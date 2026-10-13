"""State engine: the LangGraph StateGraph that wires agents together.

    START -> ingest_event -> guard_agent --pass--> infra_build -> approval_gate
                  |                |                                  |
              (invalid)          (fail)                      approved | rejected
                  v                v                                  v      v
                 END              END        infra_migrate --ok--> infra_deploy   END
                                                  | fail                |
                                                 END (reverted)   ok / failed(END)
                                                                       v
                                         +---------------------> watchman_agent
                                         | (healthy, cycles left)       |
                                         +------------------------------+
                                                 healthy & done -> END
                                                 anomaly        -> rollback -> END
Cycles are bounded: the only loop is watchman -> watchman, capped by
Guardrails.max_watch_cycles. Rollback is terminal (never re-enters Infra).
"""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .agents import (ingest_event, make_guard_node, make_infra_nodes,
                     make_watchman_nodes)
from .brain import Brain, get_brain
from .state import Guardrails, PipelineState
from .tools import Runner, SimulatedRunner, make_tools


# ------------------------------------------------------------ routers -------
def route_after_ingest(state: PipelineState) -> str:
    return END if state.get("status") == "rejected_by_guard" else "guard_agent"


def route_after_guard(state: PipelineState) -> str:
    return "infra_build" if state["build_verdict"] == "pass" else END


def route_after_build(state: PipelineState) -> str:
    return END if state.get("status") == "escalated" else "approval_gate"


def route_after_gate(state: PipelineState) -> str:
    return "infra_migrate" if state["approval"] == "approved" else END


def route_after_migrate(state: PipelineState) -> str:
    return END if state.get("status") == "migration_failed" else "infra_deploy"


def route_after_deploy(state: PipelineState) -> str:
    return "watchman_agent" if state["deploy_status"] == "ok" else END


def make_route_after_watch(g: Guardrails):
    def route(state: PipelineState) -> str:
        if state["anomaly"]:
            # bounded: one rollback max, then a human is paged by status=escalated
            return "rollback" if state.get("rollback_count", 0) < g.max_rollbacks else END
        return "watchman_agent" if state["watch_cycles"] < g.max_watch_cycles else "finalize"
    return route


def finalize(state: PipelineState) -> dict:
    return {"status": "healthy",
            "audit": [{"agent": "watchman", "action": "release_healthy",
                       "cycles": state["watch_cycles"]}]}


# ------------------------------------------------------------ builder -------
def build_graph(runner: Runner | None = None, brain: Brain | None = None,
                guardrails: Guardrails | None = None, checkpointer: Any = None):
    runner = runner or SimulatedRunner()
    brain = brain or get_brain()
    guardrails = guardrails or Guardrails()
    tools = make_tools(runner)

    guard_agent = make_guard_node(tools["guard"], brain)
    infra_build, approval_gate, infra_migrate, infra_deploy = make_infra_nodes(tools["infra"], brain)
    watchman_agent, rollback = make_watchman_nodes(tools["watchman"], guardrails)

    g = StateGraph(PipelineState)
    for name, fn in [("ingest_event", ingest_event), ("guard_agent", guard_agent),
                     ("infra_build", infra_build), ("approval_gate", approval_gate),
                     ("infra_migrate", infra_migrate), ("infra_deploy", infra_deploy),
                     ("watchman_agent", watchman_agent), ("rollback", rollback),
                     ("finalize", finalize)]:
        g.add_node(name, fn)

    g.add_edge(START, "ingest_event")
    g.add_conditional_edges("ingest_event", route_after_ingest, ["guard_agent", END])
    g.add_conditional_edges("guard_agent", route_after_guard, ["infra_build", END])
    g.add_conditional_edges("infra_build", route_after_build, ["approval_gate", END])
    g.add_conditional_edges("approval_gate", route_after_gate, ["infra_migrate", END])
    g.add_conditional_edges("infra_migrate", route_after_migrate, ["infra_deploy", END])
    g.add_conditional_edges("infra_deploy", route_after_deploy, ["watchman_agent", END])
    g.add_conditional_edges("watchman_agent", make_route_after_watch(guardrails),
                            ["watchman_agent", "rollback", "finalize", END])
    g.add_edge("rollback", END)
    g.add_edge("finalize", END)

    # A checkpointer is REQUIRED for interrupt(): it persists state while paused.
    return g.compile(checkpointer=checkpointer or MemorySaver())


# ------------------------------------------------------------ driver --------
def run_pipeline(app, event: dict, thread_id: str, decision: dict | None = None) -> dict:
    """Run until completion or the human gate. If paused and `decision` is given,
    resume with it. Returns the final state (+ '__interrupt__' if still paused)."""
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    out = app.invoke({"event": event}, cfg)
    if "__interrupt__" in out and decision is not None:
        out = app.invoke(Command(resume=decision), cfg)
    return out
