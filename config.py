"""
config.py
---------
Central configuration hub for all environment variables and constants.
Why: Separates config from code, enabling easy migration from local to Azure.
Pattern: Pydantic BaseSettings with env file support.
"""

import os
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


# ── Prompt versioning ─────────────────────────────────────────────────────────
# Increment these when prompts change to enable regression tracking in EvalOps.
PROMPT_VERSION = "1.0.0"
GRAPH_VERSION  = "1.0.0"

# ── Action allowlist ──────────────────────────────────────────────────────────
# Hard-coded allowlist so the executor can never attempt unlisted actions.
ALLOWED_ACTIONS = {
    "restart_job",
    "retry_job",
    "scale_cluster",
    "create_incident_ticket",
    "notify_support",
    "close_no_action",
    "manual_fix",
}

# ── Secret masking patterns ───────────────────────────────────────────────────
# Used by the guardrails agent and log redactor before storing or sending logs.
SECRET_PATTERNS = [
    "token", "password", "secret", "api_key", "Bearer",
    "Authorization", "credential", "passwd", "dapi",
]

# ── Confidence thresholds ─────────────────────────────────────────────────────
# Below MIN_CONFIDENCE → evaluator forces manual review (no automation).
MIN_CONFIDENCE_FOR_AUTO = 0.70
MAX_RETRY_REASONING     = 2     # Max LLM retry attempts before escalation.
MAX_LOG_LINES           = 200   # Trim log excerpts to avoid token overuse.


class Settings(BaseSettings):
    """
    Settings loaded from environment variables (.env file or system env).
    Pydantic validates types automatically at startup.
    """

    # Azure OpenAI
    azure_openai_api_key:             str = Field(..., env="AZURE_OPENAI_API_KEY")
    azure_openai_endpoint:            str = Field(..., env="AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment:          str = Field("gpt-4o", env="AZURE_OPENAI_DEPLOYMENT")
    azure_openai_api_version:         str = Field("2024-02-01", env="AZURE_OPENAI_API_VERSION")
    azure_openai_fallback_deployment: str = Field("gpt-4-turbo", env="AZURE_OPENAI_FALLBACK_DEPLOYMENT")

    # Databricks
    databricks_host:       str = Field(..., env="DATABRICKS_HOST")
    databricks_token:      str = Field(..., env="DATABRICKS_TOKEN")
    databricks_cluster_id: str = Field("", env="DATABRICKS_CLUSTER_ID")

    # LangSmith
    langchain_tracing_v2: str = Field("true", env="LANGCHAIN_TRACING_V2")
    langchain_api_key:    str = Field("", env="LANGCHAIN_API_KEY")
    langchain_project:    str = Field("databricks-agent-monitor", env="LANGCHAIN_PROJECT")
    langchain_endpoint:   str = Field("https://api.smith.langchain.com", env="LANGCHAIN_ENDPOINT")

    # Arize Phoenix
    phoenix_enabled: bool = Field(True,        env="PHOENIX_ENABLED")
    phoenix_host:    str  = Field("localhost", env="PHOENIX_HOST")
    phoenix_port:    int  = Field(6006,        env="PHOENIX_PORT")

    # Email
    sendgrid_api_key: str = Field("", env="SENDGRID_API_KEY")
    email_from:       str = Field("", env="EMAIL_FROM")
    email_to:         str = Field("", env="EMAIL_TO")

    # Scheduler
    scheduler_interval_minutes: int  = Field(90,    env="SCHEDULER_INTERVAL_MINUTES")
    scheduler_test_mode:        bool = Field(False, env="SCHEDULER_TEST_MODE")

    # Application
    app_env:      str  = Field("local", env="APP_ENV")
    log_level:    str  = Field("INFO",  env="LOG_LEVEL")
    dry_run_mode: bool = Field(True,    env="DRY_RUN_MODE")

    # Persistence
    sqlite_db_path:     str = Field("./persistence_store/incidents.db",    env="SQLITE_DB_PATH")
    audit_jsonl_path:   str = Field("./persistence_store/audit.jsonl",     env="AUDIT_JSONL_PATH")
    dead_letter_path:   str = Field("./persistence_store/dead_letter.jsonl", env="DEAD_LETTER_PATH")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings loader — reads .env once per process.
    Why lru_cache: avoids re-parsing .env on every call across nodes.
    """
    return Settings()
