"""
app.py
------
Main application entry point.
Provides three CLI modes:
  python app.py run         — Start the scheduler (normal operation)
  python app.py trigger     — Trigger one workflow cycle manually (local testing)
  python app.py resume      — Resume a paused HITL workflow with human decision
  python app.py replay      — Replay a stored incident from SQLite (debugging)

LangSmith: tracing is activated automatically via LANGCHAIN_TRACING_V2 env var
           set in .env — no extra code needed in LangGraph 0.2+.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# ── Bootstrap: load .env FIRST before any other imports ──────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── Configure logging before importing application modules ────────────────────
from logging_config import configure_logging
configure_logging()

from logging_config import get_logger
logger = get_logger(__name__)


def _setup_langsmith() -> None:
    """
    Configure LangSmith tracing environment variables.
    LangGraph 0.2+ auto-detects LANGCHAIN_TRACING_V2 and sends traces to LangSmith.
    No callback injection needed — LangChain instruments the chain automatically.
    """
    os.environ.setdefault("LANGCHAIN_TRACING_V2",  os.getenv("LANGCHAIN_TRACING_V2", "true"))
    os.environ.setdefault("LANGCHAIN_API_KEY",      os.getenv("LANGCHAIN_API_KEY", ""))
    os.environ.setdefault("LANGCHAIN_PROJECT",      os.getenv("LANGCHAIN_PROJECT", "databricks-agent-monitor"))
    os.environ.setdefault("LANGCHAIN_ENDPOINT",     os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"))
    logger.info("langsmith.configured", project=os.environ.get("LANGCHAIN_PROJECT"))


def _setup_phoenix() -> None:
    """
    Start Arize Phoenix local OSS observability server if enabled.
    Phoenix provides a local UI at http://localhost:6006 for trace visualization.
    """
    from config import get_settings
    settings = get_settings()
    if not settings.phoenix_enabled:
        return
    try:
        import phoenix as px
        from openinference.instrumentation.langchain import LangChainInstrumentor
        px.launch_app()
        LangChainInstrumentor().instrument()
        logger.info("phoenix.started", port=settings.phoenix_port)
    except ImportError:
        logger.warning("phoenix.not_installed", hint="pip install arize-phoenix")
    except Exception as exc:
        logger.warning("phoenix.startup_failed", error=str(exc))


def cmd_run(args: argparse.Namespace) -> None:
    """Start the background scheduler for continuous polling."""
    from persistence import init_db
    from scheduler import start_scheduler

    _setup_langsmith()
    _setup_phoenix()
    init_db()

    scheduler = start_scheduler()
    logger.info("app.running", mode="scheduler")

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("app.shutdown")
        scheduler.shutdown()


def cmd_trigger(args: argparse.Namespace) -> None:
    """Manually trigger one workflow cycle without waiting for the scheduler."""
    from graph import compile_graph
    from persistence import init_db
    from state import IncidentState

    _setup_langsmith()
    init_db()

    state  = IncidentState()
    app    = compile_graph()
    config = {"configurable": {"thread_id": state.trace_id}}

    logger.info("app.manual_trigger", trace_id=state.trace_id)
    print(f"\n[INFO] Starting workflow. Trace ID: {state.trace_id}\n")

    for chunk in app.stream(state, config=config, stream_mode="values"):
        node_name = list(chunk.keys())[-1] if isinstance(chunk, dict) else "state"
        print(f"  ✓ Node complete: {node_name}")

    print(f"\n[INFO] Workflow paused for human approval (trace_id={state.trace_id})")
    print(f"[INFO] Run:  python app.py resume --trace-id {state.trace_id} "
          f"--decision approve_restart --operator you@company.com")


def cmd_resume(args: argparse.Namespace) -> None:
    """
    Resume a paused HITL workflow by injecting a human decision.

    HOW IT WORKS:
    1. Rebuild the compiled graph (same MemorySaver — NOTE: in-process only).
    2. Call graph.update_state() to inject the human_decision into the checkpoint.
    3. Call graph.invoke(None, config) to resume from the interrupt point.

    Production note: For durable resumption across process restarts, swap MemorySaver
    for SqliteSaver or a Redis-backed checkpointer so state survives restarts.
    """
    from graph import compile_graph
    from nodes.approval import VALID_DECISIONS
    from state import HumanDecision, IncidentState

    trace_id = args.trace_id
    decision = args.decision
    operator = args.operator

    if decision not in VALID_DECISIONS:
        print(f"[ERROR] Invalid decision '{decision}'. Valid: {sorted(VALID_DECISIONS)}")
        sys.exit(1)

    _setup_langsmith()

    app    = compile_graph()
    config = {"configurable": {"thread_id": trace_id}}

    # Inject the human decision into the checkpointed state
    from datetime import datetime
    human_decision = HumanDecision(
        decision=decision,
        decided_by=operator,
        decided_at=datetime.utcnow().isoformat(),
        notes=args.notes,
    )

    app.update_state(config, {"human_decision": human_decision})
    logger.info("app.resume", trace_id=trace_id, decision=decision, operator=operator)

    print(f"\n[INFO] Resuming workflow trace_id={trace_id}, decision={decision}")

    # Resume from the interrupt point
    for chunk in app.stream(None, config=config, stream_mode="values"):
        node_name = list(chunk.keys())[-1] if isinstance(chunk, dict) else "state"
        print(f"  ✓ Node complete: {node_name}")

    print(f"\n[INFO] Workflow complete. Check persistence_store/ for audit records.")


def cmd_replay(args: argparse.Namespace) -> None:
    """Reload and display a stored incident state for debugging."""
    from persistence import get_incident
    import json

    state = get_incident(args.trace_id)
    if not state:
        print(f"[ERROR] No incident found for trace_id: {args.trace_id}")
        sys.exit(1)

    print(json.dumps(state.model_dump(), indent=2, default=str))


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Databricks Agent Monitor — Multi-Agent AI Workflow"
    )
    sub = parser.add_subparsers(dest="command")

    # run
    sub.add_parser("run", help="Start the background scheduler")

    # trigger
    sub.add_parser("trigger", help="Manually trigger one workflow cycle")

    # resume
    resume_p = sub.add_parser("resume", help="Resume a paused HITL workflow")
    resume_p.add_argument("--trace-id",  required=True, help="Incident trace_id")
    resume_p.add_argument("--decision",  required=True, help="Human decision")
    resume_p.add_argument("--operator",  required=True, help="Approver email/ID")
    resume_p.add_argument("--notes",     default=None,  help="Optional notes")

    # replay
    replay_p = sub.add_parser("replay", help="Replay a stored incident (debug)")
    replay_p.add_argument("--trace-id", required=True)

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "trigger":
        cmd_trigger(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "replay":
        cmd_replay(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
