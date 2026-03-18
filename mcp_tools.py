"""
mcp_tools.py
------------
FastMCP-based tool layer for Azure Databricks integration.
Why FastMCP: provides a clean tool-call interface that LangGraph agents use,
             making it easy to swap mock → real Databricks SDK without changing node code.
Production note: Replace SIMULATE_MODE logic with real databricks-sdk calls.
"""

from __future__ import annotations

import os
import random
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import MAX_LOG_LINES, SECRET_PATTERNS, get_settings
from logging_config import get_logger

logger = get_logger(__name__)

# ── Simulation toggle ─────────────────────────────────────────────────────────
# True = use synthetic mock data for local dev/testing.
# False = call real Databricks SDK (requires valid credentials).
SIMULATE_MODE = os.getenv("DATABRICKS_SIMULATE", "true").lower() == "true"


# ── Sample synthetic failures (mirrors real Databricks error patterns) ─────────
_MOCK_FAILED_RUNS = [
    {
        "job_id": "JOB-1001",
        "run_id": "RUN-9901",
        "job_name": "ETL_Daily_Positions",
        "task_name": "compute_risk_metrics",
        "cluster_id": "CLU-AAA-001",
        "error_code": "OUT_OF_MEMORY",
        "error_category": "OOM",
        "failure_timestamp": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
        "log_file": "failed_job_log_oom.txt",
        "retry_count_job": 1,
    },
    {
        "job_id": "JOB-1002",
        "run_id": "RUN-9902",
        "job_name": "Auth_Token_Refresh",
        "task_name": "refresh_oauth_tokens",
        "cluster_id": "CLU-BBB-002",
        "error_code": "AUTH_FAILURE",
        "error_category": "AUTH",
        "failure_timestamp": (datetime.utcnow() - timedelta(minutes=60)).isoformat(),
        "log_file": "failed_job_log_auth.txt",
        "retry_count_job": 0,
    },
    {
        "job_id": "JOB-1003",
        "run_id": "RUN-9903",
        "job_name": "Market_Data_Ingest",
        "task_name": "stream_ingest_equities",
        "cluster_id": "CLU-CCC-003",
        "error_code": "JOB_TIMEOUT",
        "error_category": "TIMEOUT",
        "failure_timestamp": (datetime.utcnow() - timedelta(minutes=15)).isoformat(),
        "log_file": "failed_job_log_timeout.txt",
        "retry_count_job": 2,
    },
]


def _redact_log(raw_log: str) -> str:
    """
    Remove secrets from log text before storing or sending to LLM.
    Why: Prevents token/credential leakage into LangSmith traces or email.
    """
    pattern = re.compile(
        r"(" + "|".join(re.escape(p) for p in SECRET_PATTERNS) + r")"
        r"[=:\s]+[^\s,;\"']+",
        re.IGNORECASE,
    )
    return pattern.sub(r"\1=***REDACTED***", raw_log)


def _trim_log(log_text: str, max_lines: int = MAX_LOG_LINES) -> str:
    """Keep only the last N lines to control LLM token cost."""
    lines = log_text.strip().splitlines()
    return "\n".join(lines[-max_lines:])


def _load_sample_log(filename: str) -> str:
    """Load a sample log file from sample_data/ for simulation mode."""
    path = os.path.join(os.path.dirname(__file__), "sample_data", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"[MOCK LOG] Sample log file not found: {filename}"


# ── Tool Functions (called by Monitor Agent) ──────────────────────────────────

def get_failed_job_runs(window_minutes: int = 90) -> List[Dict[str, Any]]:
    """
    Fetch failed Databricks job runs within the polling window.
    Production: Replace with databricks_sdk.jobs.runs.list(active_only=False, ...).
    Returns a list of normalized job run metadata dicts.
    """
    if SIMULATE_MODE:
        logger.info("databricks.get_failed_runs", mode="simulate", window_minutes=window_minutes)
        # Return 0-2 mock failures to keep local dev manageable
        sample_count = random.randint(0, min(2, len(_MOCK_FAILED_RUNS)))
        return random.sample(_MOCK_FAILED_RUNS, sample_count)

    # ── Real SDK path ──
    settings = get_settings()
    try:
        from databricks.sdk import WorkspaceClient  # type: ignore
        client = WorkspaceClient(
            host=settings.databricks_host,
            token=settings.databricks_token,
        )
        since = datetime.utcnow() - timedelta(minutes=window_minutes)
        runs = []
        for run in client.jobs.list_runs(completed_only=True):
            if run.state and run.state.result_state and \
               run.state.result_state.value == "FAILED" and \
               run.end_time and run.end_time > int(since.timestamp() * 1000):
                runs.append({
                    "job_id":            str(run.job_id),
                    "run_id":            str(run.run_id),
                    "job_name":          run.run_name or "unknown",
                    "task_name":         "unknown",
                    "cluster_id":        str(run.cluster_instance.cluster_id) if run.cluster_instance else "",
                    "error_code":        run.state.state_message or "UNKNOWN",
                    "error_category":    _classify_error(run.state.state_message or ""),
                    "failure_timestamp": datetime.utcfromtimestamp(run.end_time / 1000).isoformat(),
                    "retry_count_job":   0,
                    "log_file":          None,
                })
        return runs
    except Exception as exc:
        logger.error("databricks.get_failed_runs.error", error=str(exc))
        return []


def get_job_logs(job_id: str, run_id: str, log_file: Optional[str] = None) -> str:
    """
    Retrieve and redact the last N lines of job logs.
    Production: Use databricks_sdk.jobs.runs.get_output(run_id=run_id).
    """
    if SIMULATE_MODE and log_file:
        raw = _load_sample_log(log_file)
        redacted = _redact_log(raw)
        return _trim_log(redacted)

    settings = get_settings()
    try:
        from databricks.sdk import WorkspaceClient  # type: ignore
        client = WorkspaceClient(
            host=settings.databricks_host,
            token=settings.databricks_token,
        )
        output = client.jobs.get_run_output(run_id=int(run_id))
        raw = output.logs or "No logs available"
        return _trim_log(_redact_log(raw))
    except Exception as exc:
        logger.error("databricks.get_job_logs.error", run_id=run_id, error=str(exc))
        return f"[LOG RETRIEVAL FAILED] {exc}"


def restart_databricks_job(job_id: str, dry_run: bool = True) -> Dict[str, Any]:
    """
    Trigger a job restart.  dry_run=True logs intent only (safe default).
    Why dry_run: prevents accidental execution in local/staging environments.
    """
    if dry_run:
        logger.info("databricks.restart_job.dry_run", job_id=job_id)
        return {"status": "dry_run", "job_id": job_id, "action": "restart_job"}

    settings = get_settings()
    try:
        from databricks.sdk import WorkspaceClient  # type: ignore
        client = WorkspaceClient(
            host=settings.databricks_host,
            token=settings.databricks_token,
        )
        run = client.jobs.run_now(job_id=int(job_id))
        return {"status": "triggered", "job_id": job_id, "new_run_id": str(run.run_id)}
    except Exception as exc:
        logger.error("databricks.restart_job.error", job_id=job_id, error=str(exc))
        return {"status": "error", "job_id": job_id, "error": str(exc)}


def _classify_error(message: str) -> str:
    """
    Heuristically classify Databricks error messages into standard categories.
    Why: Gives the Reasoning Agent a structured category to anchor analysis.
    """
    msg = message.lower()
    if any(k in msg for k in ["memory", "oom", "heap", "gc overhead"]):
        return "OOM"
    if any(k in msg for k in ["auth", "token", "403", "401", "credential"]):
        return "AUTH"
    if any(k in msg for k in ["timeout", "timed out", "deadline"]):
        return "TIMEOUT"
    if any(k in msg for k in ["dependency", "import", "module not found", "package"]):
        return "DEP"
    if any(k in msg for k in ["exception", "error", "traceback", "attributeerror", "keyerror"]):
        return "CODE"
    return "UNKNOWN"
