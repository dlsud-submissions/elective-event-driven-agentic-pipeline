"""python -m pipeline [scenario]   scenarios: happy | tests_fail | rejected | migration_fail | spike"""
import json
import sys

from .graph import build_graph, run_pipeline
from .tools import Scenario, SimulatedRunner

SCENARIOS = {
    "happy": (Scenario(), {"approval": "approved", "approver": "lead.devops"}),
    "tests_fail": (Scenario(tests_fail=True), None),
    "rejected": (Scenario(), {"approval": "rejected", "approver": "lead.devops"}),
    "migration_fail": (Scenario(migration_fails=True), {"approval": "approved", "approver": "lead.devops"}),
    "spike": (Scenario(error_spike=True), {"approval": "approved", "approver": "lead.devops"}),
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "happy"
    scenario, decision = SCENARIOS[name]
    app = build_graph(runner=SimulatedRunner(scenario))
    out = run_pipeline(app, {"after": "9f2c1ab7d3e45f60aa11bb22cc33dd44ee55ff66", "ref": "refs/heads/main"},
                       thread_id=f"demo-{name}", decision=decision)
    print(f"scenario={name}  status={out.get('status')}  verdict={out.get('build_verdict')}")
    for a in out.get("audit", []):
        print("  ", a.get("agent"), "->", a.get("action"))
    if "__interrupt__" in out:
        print("  PAUSED at human gate")
