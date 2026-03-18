# Databricks Agent Monitor
## Production-Grade Multi-Agent AI Workflow for Azure Databricks Failure Remediation

> ⚠️ **Note (Local Demo Version)**
>
> This repository contains a **locally runnable demo version** of the Databricks Agent Monitor.
>
> Due to repository cleanup and recovery, the current implementation includes:
> - Minimal stubbed agent nodes (for workflow demonstration)
> - Fully working LangGraph orchestration (trigger + resume)
> - Human-in-the-loop (HITL) pause/resume flow
>
> The architecture, orchestration, and workflow design reflect production intent, while node implementations are simplified for demonstration.

---

## Architecture Overview


APScheduler (90-min polling)
│
▼
┌─────────────────────────────────────────────────────────────┐
│ LangGraph State Machine │
│ │
│ [Monitor] ──► [Reasoner] ──► [Evaluator] ──► [Guardrails] │
│ │ │ │ │ │
│ │ retry/fallback route to: block if │
│ │ prompt ├─ notify injection │
│ │ ├─ retry detected │
│ │ ├─ escalate │
│ │ └─ manual review │
│ │ │ │
│ │ [Notifier] ◄─────────────┘
│ │ │
│ │ ┌── HITL INTERRUPT ──┐
│ │ │ Human Decision │
│ │ │ (approve/reject) │
│ │ └─────────────────────┘
│ │ │
│ │ [Executor]
│ │ (dry-run safe)
│ │ │
│ └──────────────────────────► [Recorder]
│ SQLite + JSONL
└─────────────────────────────────────────────────────────────┘
│ │
LangSmith Arize Phoenix
(traces) (local OSS)


---

## Quick Start

### 1. Clone and setup

```bash
git clone <repo> databricks-agent-monitor
cd databricks-agent-monitor

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
2. Configure environment
copy .env.example .env

(Optional for demo — can skip API keys if using stubbed nodes)

3. Trigger workflow (recommended demo)
python app.py trigger

This will:

Simulate a Databricks failure detection workflow

Execute multi-agent orchestration via LangGraph

Print agent execution logs

Pause for human approval (HITL interrupt)

4. Resume with human decision
python app.py resume \
  --trace-id <TRACE_ID> \
  --decision approve_restart \
  --operator you@company.com

Valid decisions (demo subset):

approve_restart | deny_restart | request_more_info
5. Run scheduler (optional)
python app.py run
Agent Architecture (8 Agents)

ℹ️ Current implementation uses lightweight stubbed nodes for local execution.

Agent	Node	Role
Monitor	monitor_node	Detect failures (simulated)
Reasoner	reasoning_node	Root cause analysis (stubbed)
Evaluator	evaluator_node	Evaluate reasoning
Guardrails	guardrails_node	Validate outputs
Notifier	notifier_node	Notify incident
Approval	approval_node	HITL pause/resume
Executor	executor_node	Execute remediation
Recorder	recorder_node	Persist results
Workflow (Simplified)
trigger → monitor → reasoning → evaluator → guardrails → notifier → PAUSE
resume → approval → executor → recorder → COMPLETE
HITL (Human-in-the-Loop)

This system demonstrates:

Workflow pause using LangGraph interrupt

Checkpoint persistence

Resume with human decision

Controlled execution after approval

Demo Commands (IMPORTANT)
Step 1
python app.py trigger
Step 2
python app.py resume --trace-id <TRACE_ID> --decision approve_restart --operator you@company.com
LangSmith Tracing
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-key

⚠️ If not configured, you may see 403 Forbidden
This does not affect local execution

Phoenix UI

Access:

http://localhost:6006
File Structure (Current Demo)
databricks-agent-monitor/
├── app.py
├── graph.py
├── state.py
├── config.py
├── scheduler.py
├── persistence.py
├── nodes/
│   ├── monitor.py
│   ├── reasoner.py
│   ├── evaluator.py
│   ├── guardrails.py
│   ├── notifier.py
│   ├── approval.py
│   ├── executor.py
│   └── recorder.py
├── persistence_store/
├── requirements.txt
├── .env.example
└── README.md

⚠️ prompts/ and tests/ are not included in this demo version

Key Features Demonstrated

LangGraph multi-agent orchestration

Human-in-the-loop workflow

CLI-driven execution

State checkpointing

Resume-based workflows

Observability hooks (LangSmith, Phoenix)

Known Limitations

Stubbed node logic (no real Databricks calls)

LangSmith may show 403 without credentials

No persistent checkpoint backend (MemorySaver only)

No test suite in current version

Production Upgrade Path

Replace stub nodes with real Databricks APIs

Add vector DB + RAG for RCA

Add test suite (pytest)

Add prompt versioning

Use Redis/Postgres checkpointer

Integrate Azure Key Vault

Deploy via Azure Container Apps

Summary

This project demonstrates:

✔ Multi-agent orchestration
✔ HITL pause/resume
✔ Real-world failure remediation pattern
✔ Production-ready architecture thinking

Even in its demo form, it reflects enterprise-grade design principles.


---

# 🚀 FINAL STEP

Now run:

```powershell
git add README.md
git commit -m "docs: updated README with demo-safe and accurate description"
git push