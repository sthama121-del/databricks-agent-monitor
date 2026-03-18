"""
scheduler.py
------------
APScheduler-based polling scheduler that triggers the LangGraph workflow every N minutes.
Why APScheduler: lightweight, works locally and can be replaced with Azure Functions
                 Timer Trigger or Azure Container Apps Jobs in production.
Test mode: set SCHEDULER_TEST_MODE=true to fire immediately without waiting.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)


def run_incident_workflow() -> None:
    """
    Callback invoked by APScheduler each polling interval.
    Imports graph lazily to avoid circular import at module load time.
    Runs one full workflow cycle — monitor → (reason → evaluate → notify → approve → execute → record).

    Note: HITL pause/resume is handled separately via `app.py resume` command.
    This scheduler only triggers the initial incident detection pass.
    """
    from graph import compile_graph
    from state import IncidentState

    settings = get_settings()
    logger.info("scheduler.trigger", interval_minutes=settings.scheduler_interval_minutes)

    state   = IncidentState()
    app     = compile_graph()
    config  = {"configurable": {"thread_id": state.trace_id}}

    try:
        # Stream graph execution for observability
        for chunk in app.stream(state, config=config, stream_mode="values"):
            last_state = chunk

        logger.info("scheduler.cycle_complete", trace_id=state.trace_id)
    except Exception as exc:
        logger.error("scheduler.cycle_error", error=str(exc), trace_id=state.trace_id)


def start_scheduler() -> BackgroundScheduler:
    """
    Start APScheduler in background mode.
    Returns the scheduler so app.py can shut it down cleanly on exit.
    """
    settings = get_settings()
    scheduler = BackgroundScheduler()

    if settings.scheduler_test_mode:
        # Fire once immediately for local testing
        logger.info("scheduler.test_mode_immediate_trigger")
        run_incident_workflow()
    else:
        scheduler.add_job(
            run_incident_workflow,
            trigger=IntervalTrigger(minutes=settings.scheduler_interval_minutes),
            id="databricks_monitor",
            replace_existing=True,
            next_run_time=datetime.utcnow(),  # Fire once immediately on start
        )
        scheduler.start()
        logger.info(
            "scheduler.started",
            interval_minutes=settings.scheduler_interval_minutes,
        )

    return scheduler
