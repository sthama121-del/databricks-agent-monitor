# Databricks Agent Monitor
## Production-Grade Multi-Agent AI Workflow for Azure Databricks Failure Remediation

---

## Architecture Overview

```
APScheduler (90-min polling)
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph State Machine                   │
│                                                             │
│  [Monitor] ──► [Reasoner] ──► [Evaluator] ──► [Guardrails] │
│      │              │               │               │       │
│      │         retry/fallback    route to:      block if   │
│      │         prompt             ├─ notify     injection   │
│      │                            ├─ retry      detected   │
│      │                            ├─ escalate              │
│      │                            └─ manual review         │
│      │                                   │                 │
│      │                            [Notifier] ◄─────────────┘
│      │                                   │
│      │                        ┌── HITL INTERRUPT ──┐
│      │                        │  Human Decision     │
│      │                        │  (approve/reject)   │
│      │                        └─────────────────────┘
│      │                                   │
│      │                            [Executor]
│      │                            (dry-run safe)
│      │                                   │
│      └──────────────────────────► [Recorder]
│                                  SQLite + JSONL
└─────────────────────────────────────────────────────────────┘
         │                │
    LangSmith          Arize Phoenix
    (traces)           (local OSS)
```

---

## Quick Start

### 1. Clone and set up environment

```bash
cd C:\Users\srikr\Documents\github
git clone <repo> databricks-agent-monitor
cd databricks-agent-monitor

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env
# Edit .env with your Azure OpenAI, Databricks, LangSmith, SendGrid credentials
```

### 3. Run a manual trigger (local dev, no scheduler wait)

```bash
python app.py trigger
```

This will:
- Detect a simulated Databricks job failure
- Run GPT-4o root-cause analysis
- Evaluate the reasoning quality
- Validate outputs through guardrails
- Print the incident email to stdout
- **PAUSE** for human approval (HITL interrupt)

### 4. Resume with human decision

```bash
python app.py resume \
  --trace-id <trace_id_from_step_3> \
  --decision approve_restart \
  --operator you@company.com
```

Valid decisions: `approve_restart | approve_scale_cluster | approve_retry_job | reject_and_escalate | request_manual_fix | close_no_action`

### 5. Start continuous scheduler

```bash
python app.py run
```

---

## Agent Architecture (8 Agents)

| Agent | Node | Role |
|-------|------|------|
| 1 | `monitor_node` | Scan Databricks, extract failed job metadata + logs |
| 2 | `reasoning_node` | GPT-4o root-cause analysis (structured JSON) |
| 3 | `evaluator_node` | Independent critic: score reasoning quality + route |
| 4 | `guardrails_node` | Schema validation, injection detection, allowlist |
| 5 | `notifier_node` | Build and send incident email (SendGrid) |
| 6 | `approval_node` | LangGraph `interrupt()` — pause for human decision |
| 7 | `executor_node` | Safe remediation execution (dry-run by default) |
| 8 | `recorder_node` | SQLite + JSONL audit persistence |

---

## Routing Logic

```
monitor → [no failure] → END
monitor → [failure] → reasoner

reasoner → [dead-lettered] → recorder
reasoner → [ok] → evaluator

evaluator → [proceed_to_notify]              → guardrails → notifier
evaluator → [require_manual_review]          → guardrails → notifier (flagged)
evaluator → [retry_reasoning_with_fallback]  → reasoner (fallback prompt)
evaluator → [reject_reasoning_and_escalate]  → recorder (no notification)

guardrails → [passed]  → notifier
guardrails → [failed]  → recorder

notifier → approval (HITL INTERRUPT)

approval → [approve_*]           → executor → recorder
approval → [close/manual/ticket] → recorder
```

---

## HITL Pause / Resume Pattern

LangGraph uses `interrupt_before=["approval_node"]` at compile time, combined with `MemorySaver` checkpointing:

1. Graph runs through notifier, then pauses **before** `approval_node`
2. State is checkpointed in MemorySaver (keyed by `thread_id = trace_id`)
3. Human receives email, reviews the incident
4. Human runs: `python app.py resume --trace-id <id> --decision <decision> --operator <email>`
5. `app.py` calls `graph.update_state()` to inject `HumanDecision` into checkpoint
6. `graph.invoke(None, config)` resumes from the checkpoint
7. `approval_node` reads the injected decision, `executor_node` runs the approved action

**Production note:** For resumption across process restarts, replace `MemorySaver` with `SqliteSaver` or a Redis-backed checkpointer.

---

## LangSmith Tracing

LangSmith tracing is automatically enabled when these env vars are set:

```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-key
LANGCHAIN_PROJECT=databricks-agent-monitor
```

Every LLM call, tool call, and node transition is automatically captured. No callback injection needed in LangGraph 0.2+.

**What LangSmith captures per incident:**
- Full prompt and response for each LLM call
- Token usage and latency per node
- Reasoning → Evaluator → Guardrails trace chain
- Human decision events
- Graph routing decisions

**Dashboard:** https://smith.langchain.com → Project: `databricks-agent-monitor`

---

## Arize Phoenix (Local OSS)

Phoenix provides a local observability UI at `http://localhost:6006`:

```bash
# Start Phoenix (auto-started by app.py if PHOENIX_ENABLED=true)
python -c "import phoenix as px; px.launch_app()"
```

---

## GenAIOps / LLMOps Practices

### Prompt Versioning
- `PROMPT_VERSION` constant in `config.py` — increment on every prompt change
- Version stored in `IncidentState.prompt_version` and every audit record
- Enables A/B comparison of prompts across incidents

### Model Versioning  
- `AZURE_OPENAI_DEPLOYMENT` tracked in state and audit records
- Fallback deployment: `AZURE_OPENAI_FALLBACK_DEPLOYMENT`

### Cost Tracking
- Input/output tokens tracked per node in `TokenUsage`
- Estimated cost (USD) calculated using GPT-4o pricing
- All stored in SQLite and JSONL for per-incident cost reporting

### Evaluation Strategy
- **Online:** Evaluator Agent scores every reasoning output before action
- **Offline:** Use JSONL audit records to build labeled evaluation datasets
- **Golden tests:** `tests/` directory contains known failure class test cases
- **Threshold:** `MIN_CONFIDENCE_FOR_AUTO=0.70` — configurable in `config.py`

### Rollback Strategy
- If new prompt version degrades quality, revert `PROMPT_VERSION` in `.env`
- Historical incidents retain their original `prompt_version` for comparison
- SQLite + JSONL enable replay of any past incident with any prompt version

---

## Security & Governance

- **Secret masking:** `logging_config.py` redacts secrets at log emission time
- **Log redaction:** `mcp_tools.py` redacts all logs before LLM processing
- **Prompt injection:** `guardrails_node` detects and blocks injected log content
- **Allowlist:** `ALLOWED_ACTIONS` in `config.py` — no unlisted actions can execute
- **Dry-run default:** `DRY_RUN_MODE=true` — no real actions without explicit opt-in
- **Immutable audit:** JSONL is append-only — every decision is permanently recorded
- **HITL gate:** executor will not run without a `HumanDecision` in state

---

## File Structure

```
databricks-agent-monitor/
├── app.py                    # CLI entry point (run/trigger/resume/replay)
├── graph.py                  # LangGraph orchestration and routing
├── state.py                  # Canonical IncidentState Pydantic model
├── config.py                 # Environment config and constants
├── logging_config.py         # Structured logging + secret masking
├── mcp_tools.py              # Databricks tool layer (real + simulated)
├── persistence.py            # SQLite + JSONL audit persistence
├── scheduler.py              # APScheduler polling trigger
├── nodes/
│   ├── monitor.py            # Agent 1: detect failed jobs
│   ├── reasoner.py           # Agent 2: GPT-4o root-cause analysis
│   ├── evaluator.py          # Agent 3: critic/evaluation
│   ├── guardrails.py         # Agent 4: validation + injection detection
│   ├── notifier.py           # Agent 5: email notification
│   ├── approval.py           # Agent 6: HITL interrupt/resume
│   ├── executor.py           # Agent 7: safe remediation execution
│   └── recorder.py           # Agent 8: persistence
├── prompts/
│   ├── reasoning_prompt.py   # Primary RCA prompt (versioned)
│   ├── evaluator_prompt.py   # Critic prompt (versioned)
│   └── fallback_reasoning_prompt.py  # Conservative fallback
├── tests/
│   ├── test_reasoning.py
│   ├── test_evaluator.py
│   ├── test_guardrails.py
│   ├── test_graph_routing.py
│   └── test_persistence.py
├── sample_data/
│   ├── failed_job_log_oom.txt
│   ├── failed_job_log_auth.txt
│   └── failed_job_log_timeout.txt
├── persistence_store/        # Runtime: incidents.db, audit.jsonl
├── checkpoints/              # For future durable checkpointer
├── requirements.txt
├── .env.example
└── README.md
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Production Migration Notes

1. **Checkpointer:** Replace `MemorySaver` with `SqliteSaver` or `AsyncPostgresSaver`
2. **Databricks:** Set `DATABRICKS_SIMULATE=false` and provide real credentials
3. **Email:** Configure SendGrid API key or switch to Office365 API
4. **Managed Identity:** Replace `DATABRICKS_TOKEN` with managed identity auth
5. **Observability:** Connect LangSmith project to Azure Monitor via OTLP export
6. **Secrets:** Move all secrets from `.env` to Azure Key Vault
7. **Scaling:** Deploy as Azure Container App with APScheduler → Azure Functions Timer

---

## Known Limitations

- `MemorySaver` is in-process only — process restart loses HITL state
- SendGrid email delivery is best-effort (graceful degradation to log)
- Arize Phoenix local only — production needs Phoenix Cloud or Azure Monitor
- Databricks simulate mode returns random 0-2 failures per cycle
- Cost estimates use hardcoded GPT-4o pricing — verify against actual Azure billing
