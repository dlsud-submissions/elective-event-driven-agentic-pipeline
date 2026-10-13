"""LLM reasoning layer.

`Brain` is what the agents consult for *judgement* (parse a log, summarise risk).
Two implementations:

* HeuristicBrain - deterministic, offline; used by tests and the default demo.
* LLMBrain       - wraps any LangChain chat model (set PIPELINE_LLM, e.g.
                   "anthropic:claude-sonnet-4-5") and uses structured output.

Safety rule enforced in the agents, not here: the LLM may VETO a build, but it can
never approve one over failing hard evidence (non-zero exit code / failed tests).
"""
from __future__ import annotations

import os
import re
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class BuildVerdict(BaseModel):
    verdict: Literal["pass", "fail"]
    reason: str = Field(description="One sentence citing the decisive log evidence")


class Brain(Protocol):
    def judge_build(self, log: str) -> BuildVerdict: ...
    def summarise_migration(self, sql: str) -> str: ...


SUMMARY_RE = re.compile(r"(?:(\d+) failed)?[, ]*(?:(\d+) passed)?")


def parse_pytest_summary(log: str, exit_code: int) -> dict:
    failed = sum(int(n) for n in re.findall(r"(\d+) failed", log))
    passed = sum(int(n) for n in re.findall(r"(\d+) passed", log))
    return {"passed": passed, "failed": failed, "exit_code": exit_code}


class HeuristicBrain:
    def judge_build(self, log: str) -> BuildVerdict:
        s = parse_pytest_summary(log, 0)
        if s["failed"] or "Error" in log and "FAILED" in log:
            return BuildVerdict(verdict="fail", reason=f"{s['failed']} test(s) failed in console output")
        return BuildVerdict(verdict="pass", reason=f"{s['passed']} tests passed, no failures in log")

    def summarise_migration(self, sql: str) -> str:
        risky = any(k in sql.upper() for k in ("DROP ", "TRUNCATE", "DELETE FROM", "ALTER COLUMN"))
        return ("DESTRUCTIVE" if risky else "ADDITIVE") + f" migration:\n{sql}"


class LLMBrain:
    def __init__(self, model_spec: str):
        from langchain.chat_models import init_chat_model  # lazy import
        self.llm = init_chat_model(model_spec, temperature=0)

    def judge_build(self, log: str) -> BuildVerdict:
        chain = self.llm.with_structured_output(BuildVerdict)
        return chain.invoke(
            "You are a CI quality gate. Read the raw console log and decide pass/fail. "
            "Fail on any failed test, collection error, or non-zero exit. "
            f"Log:\n{log[-6000:]}"
        )

    def summarise_migration(self, sql: str) -> str:
        return self.llm.invoke(
            "Summarise the risk of this SQL migration for a production approver in "
            f"3 lines; flag data loss explicitly:\n{sql}"
        ).content


def get_brain() -> Brain:
    spec = os.getenv("PIPELINE_LLM")
    return LLMBrain(spec) if spec else HeuristicBrain()
