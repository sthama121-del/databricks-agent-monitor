"""
persistence.py
--------------
Local persistence layer using SQLite for incident records and JSONL for audit trail.
Why two stores: SQLite for queryable incident history; JSONL for append-only immutable audit.
Financial-grade: JSONL audit is append-only (never overwritten) for compliance.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, Optional

from config import get_settings
from logging_config import get_logger
from state import IncidentState

logger = get_logger(__name__)


def _ensure_dirs() -> None:
    """Create persistence directories if they don't exist."""
    settings = get_settings()
    os.makedirs(os.path.dirname(os.path.abspath(settings.sqlite_db_path)), exist_ok=True)


def init_db() -> None:
    """
    Create the SQLite incidents table if it doesn't exist.
    Call once at application startup.
    """
    _ensure_dirs()
    settings = get_settings()
    with sqlite3.connect(settings.sqlite_db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                trace_id           TEXT PRIMARY KEY,
                correlation_id     TEXT,
                job_id             TEXT,
                run_id             TEXT,
                job_name           TEXT,
                error_category     TEXT,
                incident_status    TEXT,
                notification_status TEXT,
                execution_status   TEXT,
                human_decision     TEXT,
                approved_action    TEXT,
                created_at         TEXT,
                updated_at         TEXT,
                full_state_json    TEXT     -- Full state blob for replay
            )
        """)
        conn.commit()
    logger.info("persistence.db.initialized", path=settings.sqlite_db_path)


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections with auto-close."""
    settings = get_settings()
    conn = sqlite3.connect(settings.sqlite_db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def upsert_incident(state: IncidentState) -> None:
    """
    Insert or update an incident record in SQLite.
    The full state JSON enables replay/debugging from any checkpoint.
    """
    _ensure_dirs()
    with get_db_conn() as conn:
        conn.execute("""
            INSERT INTO incidents (
                trace_id, correlation_id, job_id, run_id, job_name,
                error_category, incident_status, notification_status,
                execution_status, human_decision, approved_action,
                created_at, updated_at, full_state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                incident_status     = excluded.incident_status,
                notification_status = excluded.notification_status,
                execution_status    = excluded.execution_status,
                human_decision      = excluded.human_decision,
                approved_action     = excluded.approved_action,
                updated_at          = excluded.updated_at,
                full_state_json     = excluded.full_state_json
        """, (
            state.trace_id,
            state.correlation_id,
            state.job_id,
            state.run_id,
            state.job_name,
            state.error_category,
            state.incident_status,
            state.notification_status,
            state.execution_status,
            state.human_decision.decision if state.human_decision else None,
            state.approved_action,
            state.created_at,
            state.updated_at,
            state.model_dump_json(),
        ))
        conn.commit()
    logger.info("persistence.incident.upserted", trace_id=state.trace_id)


def append_audit_record(state: IncidentState, event: str, details: Optional[Dict[str, Any]] = None) -> None:
    """
    Append an immutable audit record to the JSONL audit trail.
    Why JSONL: append-only, no row can be updated/deleted — satisfies financial audit requirements.
    Each line = one atomic event in the incident lifecycle.
    """
    _ensure_dirs()
    settings = get_settings()
    record = {
        "timestamp":      datetime.utcnow().isoformat(),
        "event":          event,
        "trace_id":       state.trace_id,
        "correlation_id": state.correlation_id,
        "job_id":         state.job_id,
        "run_id":         state.run_id,
        "graph_version":  state.graph_version,
        "prompt_version": state.prompt_version,
        "model_name":     state.model_name,
        "incident_status": state.incident_status,
        "details":        details or {},
    }
    with open(settings.audit_jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.info("persistence.audit.appended", audit_event=event, trace_id=state.trace_id)


def write_dead_letter(state: IncidentState, reason: str) -> None:
    """
    Write a failed incident to the dead-letter store for manual review and replay.
    Why: Ensures no incident is silently lost, even if all retry paths fail.
    """
    _ensure_dirs()
    settings = get_settings()
    record = {
        "timestamp":  datetime.utcnow().isoformat(),
        "reason":     reason,
        "trace_id":   state.trace_id,
        "state_json": state.model_dump(),
    }
    with open(settings.dead_letter_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.warning("persistence.dead_letter.written", trace_id=state.trace_id, reason=reason)


def get_incident(trace_id: str) -> Optional[IncidentState]:
    """Load a previously stored incident state for replay or debugging."""
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT full_state_json FROM incidents WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        if row:
            return IncidentState.model_validate_json(row["full_state_json"])
        return None


def is_duplicate_incident(job_id: str, run_id: str) -> bool:
    """
    Idempotency check: return True if this run_id is already recorded.
    Why: Prevents the scheduler from processing the same failure twice.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT trace_id FROM incidents WHERE job_id = ? AND run_id = ?",
            (job_id, run_id)
        ).fetchone()
        return row is not None
