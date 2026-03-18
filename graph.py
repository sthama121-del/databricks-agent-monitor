"""
graph.py
--------
LangGraph multi-agent orchestration graph definition.
This is the control plane — it wires all agents together with typed edges,
conditional routing, HITL interrupt support, and memory-based checkpointing.

Design: Explicit edge graph (not supervisor pattern) for maximum determinism.
Why explicit edges: supervisor patterns add an extra LLM hop per routing decision.
                    For financial-grade systems, deterministic routing is safer.
"""

from __future__ import annotations

from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from nodes.approval  import approval_node
from nodes.evaluator import evaluator_node
from nodes.executor  import executor_node
from nodes.guardrails import guardrails_node
from nodes.monitor   import monitor_node
from nodes.notifier  import notifier_node
from nodes.reasoner  import reasoning_node
from nodes.recorder  import recorder_node
from state import IncidentState


# ── Routing functions ──────────────────────────────────────────────────────────
# These are the branching conditions. They read state and return an edge label.
# Why functions: keeps routing logic visible, testable, and separate from node code.

def route_after_monitor(state: IncidentState) -> Literal["reasoning_node", END]:
    """If no failure found or incident already closed, end the graph."""
    if state.incident_status == "closed" or not state.job_id:
        return END
    return "reasoning_node"


def route_after_reasoner(state: IncidentState) -> Literal["evaluator_node", "recorder_node"]:
    """If reasoning exhausted all retries, skip to recorder (dead-letter)."""
    if state.dead_lettered:
        return "recorder_node"
    return "evaluator_node"


def route_after_evaluator(
    state: IncidentState,
) -> Literal["guardrails_node", "reasoning_node", "recorder_node"]:
    """
    Route based on evaluator's route_decision.
    retry_reasoning: increment retry count and send back to reasoner.
    escalate/reject: skip to recorder (no notification, no execution).
    proceed: continue to guardrails → notifier.
    """
    if not state.evaluator_result:
        return "recorder_node"

    route = state.evaluator_result.route_decision

    if route == "retry_reasoning_with_fallback_prompt":
        state.retry_count += 1
        return "reasoning_node"
    if route in ("reject_reasoning_and_escalate",):
        state.incident_status = "escalated"
        return "recorder_node"
    if route == "require_manual_review":
        # Still notify but flag as manual review required
        return "guardrails_node"
    return "guardrails_node"   # proceed_to_notify


def route_after_guardrails(
    state: IncidentState,
) -> Literal["notifier_node", "recorder_node"]:
    """If guardrails found violations, skip notification and go to recorder."""
    if state.guardrails_result and not state.guardrails_result.passed:
        return "recorder_node"
    return "notifier_node"


def route_after_approval(
    state: IncidentState,
) -> Literal["executor_node", "recorder_node"]:
    """
    After human decision: if approve action → execute; otherwise → record and close.
    'close_no_action' and escalations skip execution.
    """
    if not state.human_decision:
        return "recorder_node"
    if state.approved_action in ("close_no_action", "manual_fix", "create_incident_ticket"):
        return "recorder_node"
    if "approve" in state.human_decision.decision:
        return "executor_node"
    return "recorder_node"


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Construct and compile the LangGraph StateGraph.

    Node order:
    START → monitor → reasoner → evaluator → guardrails → notifier
         → approval (HITL interrupt) → executor → recorder → END

    Conditional edges implement the routing logic above.
    MemorySaver enables checkpointing for HITL pause/resume.
    """
    # ── State graph with Pydantic model ───────────────────────────────────────
    graph = StateGraph(IncidentState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("monitor_node",    monitor_node)
    graph.add_node("reasoning_node",  reasoning_node)
    graph.add_node("evaluator_node",  evaluator_node)
    graph.add_node("guardrails_node", guardrails_node)
    graph.add_node("notifier_node",   notifier_node)
    graph.add_node("approval_node",   approval_node)   # HITL interrupt lives here
    graph.add_node("executor_node",   executor_node)
    graph.add_node("recorder_node",   recorder_node)

    # ── Entry edge ────────────────────────────────────────────────────────────
    graph.add_edge(START, "monitor_node")

    # ── Conditional edges (routing logic) ─────────────────────────────────────
    graph.add_conditional_edges(
        "monitor_node",
        route_after_monitor,
        {"reasoning_node": "reasoning_node", END: END},
    )
    graph.add_conditional_edges(
        "reasoning_node",
        route_after_reasoner,
        {"evaluator_node": "evaluator_node", "recorder_node": "recorder_node"},
    )
    graph.add_conditional_edges(
        "evaluator_node",
        route_after_evaluator,
        {
            "guardrails_node": "guardrails_node",
            "reasoning_node":  "reasoning_node",
            "recorder_node":   "recorder_node",
        },
    )
    graph.add_conditional_edges(
        "guardrails_node",
        route_after_guardrails,
        {"notifier_node": "notifier_node", "recorder_node": "recorder_node"},
    )

    # ── Linear edges (no branching needed) ────────────────────────────────────
    graph.add_edge("notifier_node", "approval_node")   # Notify → pause for human

    graph.add_conditional_edges(
        "approval_node",
        route_after_approval,
        {"executor_node": "executor_node", "recorder_node": "recorder_node"},
    )

    graph.add_edge("executor_node", "recorder_node")
    graph.add_edge("recorder_node", END)

    return graph


def compile_graph():
    """
    Compile graph with MemorySaver checkpointer.
    MemorySaver stores state in-process (local dev).
    Production: swap for SqliteSaver or PostgresSaver for durable persistence.
    """
    graph = build_graph()
    memory = MemorySaver()
    return graph.compile(checkpointer=memory, interrupt_before=["approval_node"])
    # interrupt_before="approval_node" tells LangGraph to pause BEFORE running the
    # approval node, giving the human time to inject their decision via update_state.
