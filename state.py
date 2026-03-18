"""
state.py
--------
Defines the canonical state object that flows through every LangGraph node.
Why: A single shared state prevents nodes from holding private mutable state,
     making the graph deterministic, checkpointable, and replayable.
All fields are Optional so nodes can safely read/write their own slice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def new_trace_id() -> str:
    """Generate a UUID4 trace ID for distributed tracing correlation."""
    return str(uuid.uuid4())


# ── Sub-schemas ───────────────────────────────────────────────────────────────

class RemediationOption(BaseModel):
    """One proposed remediation action from the Reasoning Agent."""
    action:      str   # e.g., "restart_job"
    description: str
    risk_level:  str   # "low" | "medium" | "high"
    estimated_impact: str

class LLMAnalysis(BaseModel):
    """Structured output from the Reasoning Agent (GPT-4o)."""
    diagnosis:          str
    root_cause:         str
    evidence_from_logs: str           # Grounded evidence only
    inference_notes:    str           # Clearly labelled inferences
    uncertainty_notes:  str           # What GPT-4o is unsure about
    confidence_score:   float         # 0.0 – 1.0
    impacted_component: str
    remediation_options: List[RemediationOption]
    safe_for_automation: bool         # LLM's own safety opinion
    prompt_version:      str

class EvaluatorResult(BaseModel):
    """Output from the Evaluation / Critic Agent."""
    evaluator_score:    float         # 0.0 – 1.0
    evaluator_verdict:  str           # "pass" | "fail" | "escalate"
    evaluator_rationale: str
    route_decision:     str           # see ROUTE_DECISIONS in evaluator.py
    grounded_in_logs:   bool
    remediation_aligned: bool
    safety_check_passed: bool

class GuardrailsResult(BaseModel):
    """Output from the Guardrails / Validation Agent."""
    passed:        bool
    violations:    List[str]
    masked_fields: List[str]

class TokenUsage(BaseModel):
    """Tracks LLM token consumption for cost monitoring."""
    input_tokens:  int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

class NodeLatency(BaseModel):
    """Per-node timing for performance observability."""
    node_name:      str
    duration_ms:    float
    started_at:     str
    completed_at:   str

class HumanDecision(BaseModel):
    """Captures the human-in-the-loop approval decision and audit trail."""
    decision:       str           # approve_restart | reject_and_escalate | etc.
    decided_by:     str           # operator email / ID
    decided_at:     str           # ISO timestamp
    notes:          Optional[str] = None
    approved_action: Optional[str] = None


# ── Primary Graph State ───────────────────────────────────────────────────────

class IncidentState(BaseModel):
    """
    The canonical state object passed between every LangGraph node.

    Lifecycle: Created by Monitor → enriched by each node → persisted by Recorder.
    Checkpointing: LangGraph serializes this model at every node boundary,
                   enabling HITL pause and deterministic resume.
    """

    # ── Identity / Correlation ────────────────────────────────────────────────
    trace_id:       str = Field(default_factory=new_trace_id)
    correlation_id: str = Field(default_factory=new_trace_id)
    graph_version:  str = "1.0.0"
    prompt_version: str = "1.0.0"
    model_name:     str = "gpt-4o"
    model_deployment: str = "gpt-4o"

    # ── Scheduler Window ──────────────────────────────────────────────────────
    scheduler_window_start: Optional[str] = None   # ISO timestamp
    scheduler_window_end:   Optional[str] = None

    # ── Databricks Job Metadata ───────────────────────────────────────────────
    job_id:            Optional[str] = None
    run_id:            Optional[str] = None
    job_name:          Optional[str] = None
    task_name:         Optional[str] = None
    cluster_id:        Optional[str] = None
    error_code:        Optional[str] = None
    error_category:    Optional[str] = None     # OOM | AUTH | TIMEOUT | DEP | CODE
    failure_timestamp: Optional[str] = None
    retry_count_job:   int = 0                  # Databricks-side retries

    # ── Log Data ──────────────────────────────────────────────────────────────
    raw_error_log: Optional[str] = None         # Stored redacted
    log_excerpt:   Optional[str] = None         # Trimmed for LLM context

    # ── Agent Outputs ─────────────────────────────────────────────────────────
    llm_analysis:       Optional[LLMAnalysis]     = None
    evaluator_result:   Optional[EvaluatorResult] = None
    guardrails_result:  Optional[GuardrailsResult] = None

    # ── Remediation ───────────────────────────────────────────────────────────
    remediation_options: List[RemediationOption] = []
    approved_action:     Optional[str] = None

    # ── HITL ──────────────────────────────────────────────────────────────────
    human_decision:         Optional[HumanDecision] = None
    awaiting_human_approval: bool = False

    # ── Notification ──────────────────────────────────────────────────────────
    notification_status:   str = "pending"       # pending | sent | failed
    notification_sent_at:  Optional[str] = None

    # ── Execution ─────────────────────────────────────────────────────────────
    execution_status:  str = "pending"            # pending | completed | failed | dry_run
    execution_result:  Optional[str] = None

    # ── Retry / Error Handling ────────────────────────────────────────────────
    retry_count:     int = 0                      # LLM reasoning retries
    last_error:      Optional[str] = None
    dead_lettered:   bool = False

    # ── Observability ─────────────────────────────────────────────────────────
    token_usage:   TokenUsage = Field(default_factory=TokenUsage)
    node_latencies: List[NodeLatency] = []
    incident_status: str = "open"                 # open | resolved | escalated | closed

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at:   str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at:   str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def touch(self) -> None:
        """Update the updated_at timestamp — call after any mutation."""
        self.updated_at = datetime.utcnow().isoformat()

    def add_latency(self, node_name: str, started_at: datetime, completed_at: datetime) -> None:
        """Record per-node latency for observability dashboards."""
        self.node_latencies.append(NodeLatency(
            node_name=node_name,
            duration_ms=round((completed_at - started_at).total_seconds() * 1000, 2),
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
        ))
