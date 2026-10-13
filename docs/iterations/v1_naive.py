"""Iteration 1 - first AI draft (kept for the correction log). DO NOT USE."""
from typing import TypedDict
from langgraph.graph import StateGraph, END

class S(TypedDict):
    commit: str
    tests_ok: bool
    healthy: bool

def guard(s):   return {"tests_ok": True}
def infra(s):   return {}
def watch(s):   return {"healthy": False}      # simulated error spike
def rollback(s): return {}

g = StateGraph(S)
for n, f in [("guard", guard), ("infra", infra), ("watchman", watch), ("rollback", rollback)]:
    g.add_node(n, f)
g.set_entry_point("guard")
g.add_conditional_edges("guard", lambda s: "infra" if s["tests_ok"] else END)
g.add_edge("infra", "watchman")
g.add_conditional_edges("watchman", lambda s: END if s["healthy"] else "rollback")
g.add_edge("rollback", "infra")                # <-- "re-deploy previous version"
app = g.compile()                              # <-- no checkpointer, no HITL
print(app.invoke({"commit": "abc123", "tests_ok": True, "healthy": False}))
