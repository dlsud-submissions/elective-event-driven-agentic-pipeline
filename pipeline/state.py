"""Shared state schema, thresholds and enums.

Every key below is a *contract* between agents: the key that one node writes is
the key the next node reads (see docs/02-agentic-pipeline-blueprint.md, section 3).
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, Optional, TypedDict


@dataclass(frozen=True)
class Guardrails:
    """Hard limits. Changing these never requires touching graph logic."""
    max_watch_cycles: int = 3          # observation samples after go-live
    error_rate_threshold: float = 0.05  # 5xx / total requests
    p95_latency_ms_threshold: int = 2000
    min_requests_for_signal: int = 50   # ignore statistically empty samples
    max_rollbacks: int = 1              # rollback is attempted once, then escalate
    command_allowlist: tuple = ("pytest", "docker", "dotnet", "alembic", "kubectl")


Status = Literal[
    "received", "rejected_by_guard", "awaiting_approval", "halted_by_human",
    "migrating", "migration_failed", "deployed", "monitoring",
    "healthy", "rolled_back", "escalated",
]


class PipelineState(TypedDict, total=False):
    # --- inbound webhook (written by ingest_event) --------------------------
    event: dict
    commit_sha: str
    branch: str
    # --- Guard-Agent output --------------------------------------------------
    test_log: str
    test_summary: dict            # {"passed": int, "failed": int, "exit_code": int}
    build_verdict: Literal["pass", "fail"]
    verdict_reason: str
    # --- Infra-Agent output --------------------------------------------------
    image_tag: str                # e.g. "svc:abc1234"  (Guard -> Infra via commit_sha)
    previous_image_tag: str       # rollback target, captured BEFORE deploy
    migration_plan: str           # human-readable SQL/EF diff shown at the gate
    migration_id: str
    approval: Literal["approved", "rejected"]
    approver: str
    deploy_status: Literal["ok", "failed"]
    # --- Watchman-Agent output ----------------------------------------------
    metrics_history: Annotated[list, operator.add]
    watch_cycles: int
    anomaly: bool
    anomaly_reason: str
    rollback_count: int
    # --- control + audit -----------------------------------------------------
    status: str
    audit: Annotated[list, operator.add]   # append-only trail, one entry per node
    error: Optional[str]
