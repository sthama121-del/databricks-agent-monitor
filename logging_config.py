"""
logging_config.py
-----------------
Structured logging configuration using structlog + standard logging.
Why structlog: produces JSON-formatted logs with automatic context binding,
               compatible with Azure Monitor, Datadog, and Splunk ingestion.
Masking: sensitive fields are redacted at the processor level before emission.
"""

import logging
import re
import sys
from typing import Any, Dict, MutableMapping

import structlog
from config import SECRET_PATTERNS, get_settings


def _mask_secrets(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """
    structlog processor: redact secrets from any string value in the log event.
    Why: Prevents tokens/passwords leaking into log aggregators or LangSmith.
    """
    pattern = re.compile(
        r"(" + "|".join(re.escape(p) for p in SECRET_PATTERNS) + r")"
        r"[=:\s]+[^\s,;\"']+",
        re.IGNORECASE,
    )

    def _mask(val: Any) -> Any:
        if isinstance(val, str):
            return pattern.sub(r"\1=***REDACTED***", val)
        return val

    return {k: _mask(v) for k, v in event_dict.items()}


def configure_logging() -> None:
    """
    Set up structlog with JSON output and secret masking.
    Call once at application startup in app.py.
    """
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Standard library logging base
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,            # Bind trace_id etc.
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _mask_secrets,                                       # Redact before emit
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),                 # JSON output
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__) -> structlog.BoundLogger:
    """Return a structlog logger. Bind trace_id from context if available."""
    return structlog.get_logger(name)


def bind_trace_context(trace_id: str, correlation_id: str, node: str) -> None:
    """
    Bind trace/correlation IDs to structlog context vars for this async context.
    Why: Ensures every log line in a node execution carries the incident ID.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        correlation_id=correlation_id,
        node=node,
    )
