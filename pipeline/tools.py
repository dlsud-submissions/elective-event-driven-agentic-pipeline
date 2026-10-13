"""Agent tool definitions.

Tools are plain functions wrapped with @tool and *bound to specific agents*.
No agent receives a tool outside its role (least privilege):

    Guard-Agent    -> run_test_suite
    Infra-Agent    -> build_image, preview_migration, apply_migration,
                      revert_migration, deploy_container
    Watchman-Agent -> fetch_runtime_metrics, rollback_deployment

All side effects go through a `Runner`. `SimulatedRunner` (default) makes the
pipeline fully runnable offline; `ShellRunner` runs real allow-listed commands.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.tools import tool

from .state import Guardrails


class Runner(Protocol):
    def run(self, command: str) -> tuple[int, str]: ...
    def metrics(self, cycle: int) -> dict: ...


@dataclass
class Scenario:
    """Fault-injection switches for the simulated environment."""
    tests_fail: bool = False
    migration_fails: bool = False
    deploy_fails: bool = False
    error_spike: bool = False
    migration_is_destructive: bool = True


@dataclass
class SimulatedRunner:
    scenario: Scenario = field(default_factory=Scenario)
    calls: list = field(default_factory=list)

    def run(self, command: str) -> tuple[int, str]:
        self.calls.append(command)
        s = self.scenario
        if command.startswith("pytest"):
            if s.tests_fail:
                return 1, ("tests/test_orders.py::test_checkout_total FAILED\n"
                           "E   AssertionError: expected 120.0, got 110.0\n"
                           "=== 1 failed, 41 passed in 8.12s ===")
            return 0, "=== 42 passed in 7.90s ==="
        if command.startswith("docker build"):
            return 0, "Successfully built image"
        if command.startswith("alembic upgrade --sql"):
            return 0, "ALTER TABLE orders ADD COLUMN sku VARCHAR(32);"
        if command.startswith("alembic upgrade"):
            if s.migration_fails:
                return 1, "sqlalchemy.exc.ProgrammingError: column \"sku\" already exists"
            return 0, "Running upgrade -> 0042_add_sku"
        if command.startswith("alembic downgrade"):
            return 0, "Running downgrade 0042_add_sku -> 0041"
        if command.startswith("kubectl set image") or command.startswith("kubectl rollout undo"):
            if s.deploy_fails and "undo" not in command:
                return 1, "error: deployment exceeded its progress deadline"
            return 0, "deployment updated"
        return 0, "ok"

    def metrics(self, cycle: int) -> dict:
        if self.scenario.error_spike and cycle >= 1:
            return {"requests": 900, "http_5xx": 117, "p95_ms": 2650}
        return {"requests": 900, "http_5xx": 4, "p95_ms": 310}


@dataclass
class ShellRunner:
    """Real execution. Refuses any binary not on the allowlist."""
    guardrails: Guardrails = field(default_factory=Guardrails)
    metrics_url: str | None = None

    def run(self, command: str) -> tuple[int, str]:
        argv = shlex.split(command)
        if not argv or argv[0] not in self.guardrails.command_allowlist:
            return 126, f"BLOCKED: '{argv[0] if argv else ''}' is not allow-listed"
        p = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        return p.returncode, (p.stdout + p.stderr)

    def metrics(self, cycle: int) -> dict:  # wire to Prometheus/Datadog in production
        raise NotImplementedError("Provide a metrics backend for ShellRunner")


def make_tools(runner: Runner):
    """Return tool groups bound to `runner`, one list per agent."""

    @tool
    def run_test_suite(commit_sha: str) -> dict:
        """Run the unit/integration suite for a commit and return exit code + raw console log."""
        code, out = runner.run(f"pytest -q --maxfail=5 --commit {commit_sha}")
        return {"exit_code": code, "log": out}

    @tool
    def build_image(commit_sha: str) -> dict:
        """Build the container image tagged with the short commit hash."""
        tag = f"svc:{commit_sha[:7]}"
        code, out = runner.run(f"docker build -t {tag} .")
        return {"exit_code": code, "image_tag": tag, "log": out}

    @tool
    def preview_migration() -> dict:
        """Render the pending migration as SQL WITHOUT executing it (shown to the human gate)."""
        code, out = runner.run("alembic upgrade --sql head")
        return {"exit_code": code, "sql": out}

    @tool
    def apply_migration() -> dict:
        """Apply pending database migrations. Only callable after human approval."""
        code, out = runner.run("alembic upgrade head")
        return {"exit_code": code, "log": out}

    @tool
    def revert_migration(migration_id: str) -> dict:
        """Run the DOWN migration for the given migration id."""
        code, out = runner.run(f"alembic downgrade -1  # {migration_id}")
        return {"exit_code": code, "log": out}

    @tool
    def deploy_container(image_tag: str) -> dict:
        """Roll the deployment to `image_tag`."""
        code, out = runner.run(f"kubectl set image deployment/svc svc={image_tag}")
        return {"exit_code": code, "log": out}

    @tool
    def fetch_runtime_metrics(cycle: int) -> dict:
        """Sample live HTTP metrics: request count, 5xx count, p95 latency (ms)."""
        return runner.metrics(cycle)

    @tool
    def rollback_deployment(previous_image_tag: str) -> dict:
        """Restore the previously healthy image."""
        code, out = runner.run(f"kubectl rollout undo deployment/svc  # {previous_image_tag}")
        return {"exit_code": code, "log": out}

    return {
        "guard": [run_test_suite],
        "infra": [build_image, preview_migration, apply_migration, revert_migration, deploy_container],
        "watchman": [fetch_runtime_metrics, rollback_deployment],
    }
