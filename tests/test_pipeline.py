import pytest

from pipeline.graph import build_graph, run_pipeline
from pipeline.tools import Scenario, SimulatedRunner

EVENT = {"after": "9f2c1ab7d3e45f60aa11bb22cc33dd44ee55ff66", "ref": "refs/heads/main"}
OK = {"approval": "approved", "approver": "lead.devops"}


def run(scenario, decision, tid):
    r = SimulatedRunner(scenario)
    return r, run_pipeline(build_graph(runner=r), EVENT, tid, decision)


def migrated(r):  # was the production migration executed?
    return any(c == "alembic upgrade head" for c in r.calls)


def test_happy_path_is_healthy():
    r, out = run(Scenario(), OK, "t1")
    assert out["status"] == "healthy" and out["watch_cycles"] == 3


def test_failed_tests_never_reach_infra():
    r, out = run(Scenario(tests_fail=True), None, "t2")
    assert out["status"] == "rejected_by_guard"
    assert not any(c.startswith("docker") for c in r.calls)


def test_pipeline_pauses_at_gate_and_does_not_migrate():
    r, out = run(Scenario(), None, "t3")
    assert "__interrupt__" in out and not migrated(r)


def test_rejection_blocks_migration():
    r, out = run(Scenario(), {"approval": "rejected", "approver": "x"}, "t4")
    assert out["status"] == "halted_by_human" and not migrated(r)


def test_approval_without_approver_is_rejected():
    r, out = run(Scenario(), {"approval": "approved"}, "t5")
    assert out["status"] == "halted_by_human" and not migrated(r)


def test_migration_failure_reverts_and_stops():
    r, out = run(Scenario(migration_fails=True), OK, "t6")
    assert out["status"] == "migration_failed"
    assert any(c.startswith("alembic downgrade") for c in r.calls)
    assert not any(c.startswith("kubectl set image") for c in r.calls)


def test_error_spike_triggers_single_rollback():
    r, out = run(Scenario(error_spike=True), OK, "t7")
    assert out["status"] == "rolled_back" and out["rollback_count"] == 1
    assert sum(c.startswith("kubectl rollout undo") for c in r.calls) == 1


def test_invalid_webhook_rejected():
    out = run_pipeline(build_graph(), {"after": "abc"}, "t8")
    assert out["status"] == "rejected_by_guard"


def test_shell_runner_blocks_non_allowlisted_binary():
    from pipeline.tools import ShellRunner
    code, msg = ShellRunner().run("rm -rf /")
    assert code == 126 and "BLOCKED" in msg
