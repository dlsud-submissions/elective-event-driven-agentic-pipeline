"""The three specialised agents, expressed as LangGraph node factories.

Each node: reads only the state keys it needs -> calls its bound tools -> asks the
Brain for judgement where useful -> returns a *partial* state update plus one audit
entry. Nodes never call each other; routing lives exclusively in graph.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from langgraph.types import interrupt

from .brain import Brain, parse_pytest_summary
from .state import Guardrails, PipelineState


def _audit(agent: str, action: str, **detail) -> list[dict]:
    return [{"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "agent": agent, "action": action, **detail}]


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


# ---------------------------------------------------------------- ingest ----
def ingest_event(state: PipelineState) -> dict:
    ev = state["event"]
    sha = ev.get("after") or ev.get("commit_sha", "")
    if len(sha) < 7:
        return {"status": "rejected_by_guard", "error": "webhook missing commit hash",
                "audit": _audit("ingest", "invalid_event")}
    return {"commit_sha": sha, "branch": ev.get("ref", "refs/heads/main").split("/")[-1],
            "rollback_count": 0, "watch_cycles": 0, "anomaly": False, "status": "received",
            "audit": _audit("ingest", "webhook_accepted", commit=sha[:7])}


# ----------------------------------------------------------- Guard-Agent ----
def make_guard_node(tools, brain: Brain):
    run_tests = _tool(tools, "run_test_suite")

    def guard_agent(state: PipelineState) -> dict:
        res = run_tests.invoke({"commit_sha": state["commit_sha"]})
        summary = parse_pytest_summary(res["log"], res["exit_code"])
        llm = brain.judge_build(res["log"])
        # Hard evidence wins: LLM may veto, never approve over failing evidence.
        hard_fail = summary["exit_code"] != 0 or summary["failed"] > 0
        verdict = "fail" if (hard_fail or llm.verdict == "fail") else "pass"
        reason = llm.reason if not hard_fail else (
            f"exit_code={summary['exit_code']}, failed={summary['failed']}; {llm.reason}")
        return {"test_log": res["log"], "test_summary": summary, "build_verdict": verdict,
                "verdict_reason": reason,
                "status": "received" if verdict == "pass" else "rejected_by_guard",
                "audit": _audit("guard", f"build_{verdict}", reason=reason)}

    return guard_agent


# ----------------------------------------------------------- Infra-Agent ----
def make_infra_nodes(tools, brain: Brain):
    build = _tool(tools, "build_image")
    preview = _tool(tools, "preview_migration")
    apply_ = _tool(tools, "apply_migration")
    revert = _tool(tools, "revert_migration")
    deploy = _tool(tools, "deploy_container")

    def infra_build(state: PipelineState) -> dict:
        b = build.invoke({"commit_sha": state["commit_sha"]})
        if b["exit_code"] != 0:
            return {"status": "escalated", "error": "image build failed",
                    "audit": _audit("infra", "build_failed", log=b["log"])}
        p = preview.invoke({})
        return {"image_tag": b["image_tag"],
                "previous_image_tag": state.get("previous_image_tag", "svc:stable"),
                "migration_id": "0042_add_sku",
                "migration_plan": brain.summarise_migration(p["sql"]),
                "status": "awaiting_approval",
                "audit": _audit("infra", "image_built_migration_previewed", image=b["image_tag"])}

    def approval_gate(state: PipelineState) -> dict:
        """HUMAN-IN-THE-LOOP. Execution is suspended here and the checkpointer
        persists the full state. Resumed via Command(resume={...})."""
        decision = interrupt({
            "question": "Apply database migration to PRODUCTION?",
            "commit": state["commit_sha"][:7], "image": state["image_tag"],
            "migration_id": state["migration_id"], "plan": state["migration_plan"],
            "expects": {"approval": "approved | rejected", "approver": "<engineer id>"},
        })
        ok = isinstance(decision, dict) and decision.get("approval") == "approved" \
            and bool(decision.get("approver"))
        return {"approval": "approved" if ok else "rejected",
                "approver": (decision or {}).get("approver", "unknown") if isinstance(decision, dict) else "unknown",
                "status": "migrating" if ok else "halted_by_human",
                "audit": _audit("human", "approved" if ok else "rejected",
                                approver=(decision or {}).get("approver") if isinstance(decision, dict) else None)}

    def infra_migrate(state: PipelineState) -> dict:
        assert state.get("approval") == "approved", "migration attempted without approval"
        m = apply_.invoke({})
        if m["exit_code"] != 0:
            revert.invoke({"migration_id": state["migration_id"]})
            return {"status": "migration_failed", "error": m["log"],
                    "audit": _audit("infra", "migration_failed_reverted", log=m["log"])}
        return {"audit": _audit("infra", "migration_applied", id=state["migration_id"])}

    def infra_deploy(state: PipelineState) -> dict:
        d = deploy.invoke({"image_tag": state["image_tag"]})
        if d["exit_code"] != 0:
            revert.invoke({"migration_id": state["migration_id"]})
            return {"deploy_status": "failed", "status": "escalated", "error": d["log"],
                    "audit": _audit("infra", "deploy_failed_migration_reverted", log=d["log"])}
        return {"deploy_status": "ok", "status": "deployed",
                "audit": _audit("infra", "deployed", image=state["image_tag"])}

    return infra_build, approval_gate, infra_migrate, infra_deploy


# -------------------------------------------------------- Watchman-Agent ----
def make_watchman_nodes(tools, guardrails: Guardrails):
    fetch = _tool(tools, "fetch_runtime_metrics")
    rollback = _tool(tools, "rollback_deployment")

    def watchman_agent(state: PipelineState) -> dict:
        cycle = state.get("watch_cycles", 0)
        m = fetch.invoke({"cycle": cycle})
        rate = m["http_5xx"] / m["requests"] if m["requests"] else 0.0
        enough = m["requests"] >= guardrails.min_requests_for_signal
        reasons = []
        if enough and rate > guardrails.error_rate_threshold:
            reasons.append(f"5xx rate {rate:.1%} > {guardrails.error_rate_threshold:.0%}")
        if enough and m["p95_ms"] > guardrails.p95_latency_ms_threshold:
            reasons.append(f"p95 {m['p95_ms']}ms > {guardrails.p95_latency_ms_threshold}ms")
        return {"metrics_history": [{**m, "cycle": cycle, "error_rate": round(rate, 4)}],
                "watch_cycles": cycle + 1, "anomaly": bool(reasons),
                "anomaly_reason": "; ".join(reasons), "status": "monitoring",
                "audit": _audit("watchman", "anomaly" if reasons else "sample_ok",
                                cycle=cycle, error_rate=round(rate, 4))}

    def rollback_node(state: PipelineState) -> dict:
        rollback.invoke({"previous_image_tag": state["previous_image_tag"]})
        return {"rollback_count": state.get("rollback_count", 0) + 1, "status": "rolled_back",
                "audit": _audit("watchman", "rolled_back", to=state["previous_image_tag"],
                                reason=state.get("anomaly_reason"))}

    return watchman_agent, rollback_node
