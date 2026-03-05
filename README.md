# Autonomous Maintenance Agent

A proactive background agent that continuously monitors a software repository, identifies maintenance opportunities, prioritizes them by risk/impact, and autonomously implements low-risk fixes using the Claude Agent SDK.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   Maintenance Cycle (per interval)              │
│                                                                 │
│  ┌──────────┐    ┌────────────┐    ┌─────────────┐             │
│  │ Scanner  │───▶│ Identifier │───▶│ Prioritizer │             │
│  │          │    │            │    │ (Claude API)│             │
│  └──────────┘    └────────────┘    └──────┬──────┘             │
│                                           │                     │
│                                    ┌──────▼──────┐             │
│                                    │   Filter    │             │
│                                    │ risk < 8    │             │
│                                    │ impact >= 3 │             │
│                                    └──────┬──────┘             │
│                                           │ Approved tasks      │
│                                    ┌──────▼──────┐             │
│                                    │  Executor   │             │
│                                    │ (Agent SDK) │             │
│                                    └──────┬──────┘             │
│                                           │                     │
│                                    ┌──────▼──────┐             │
│                                    │    Git      │             │
│                                    │ Integration │             │
│                                    └──────┬──────┘             │
│                                           │                     │
│                                    ┌──────▼──────┐             │
│                                    │ Audit Logger│             │
│                                    └─────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

### Components

| Component | File | Role |
|-----------|------|------|
| **Scanner** | `agent/scanner.py` | Perceives repository state — files, doc gaps, code smells, dependency manifests |
| **Identifier** | `agent/identifier.py` | Converts observations into structured `MaintenanceTask` objects |
| **Prioritizer** | `agent/prioritizer.py` | Uses Claude API (adaptive thinking) to score impact + risk for each task |
| **Executor** | `agent/executor.py` | Uses Claude Agent SDK to autonomously implement approved tasks |
| **Git Integration** | `agent/git_integration.py` | Creates per-task branches and commits agent changes |
| **Scheduler** | `agent/scheduler.py` | Orchestrates the cycle on a configurable interval via APScheduler |

## Task Types Detected

- **Missing README** — no top-level `README.md` or `README.rst`
- **Missing docstrings** — module, class, or function docstrings absent in Python files
- **Missing type hints** — functions without return type annotations
- **Long functions** — functions exceeding 60 lines (refactor candidates)
- **TODO/FIXME comments** — unresolved annotations in source code
- **Dependency audits** — unpinned or potentially stale dependencies
- **Low test coverage** — test-to-source file ratio below 20%

## Prioritization

Each task is scored by Claude:

- **Impact (1-10):** Developer value delivered
- **Risk (1-10):** Probability of introducing a regression
- **Priority = Impact × (10 − Risk)** — high impact, low risk tasks execute first

Tasks with `risk_score ≥ 8` are **deferred** for human review. Tasks with `impact_score < 3` are skipped.

## Installation

```bash
pip install -r requirements.txt
```

Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY="your-key-here"
```

## Usage

### Run one maintenance cycle (dry run mode — tasks only)
```bash
python main.py tasks --repo /path/to/your/repo
```

### Run one full maintenance cycle (scan → identify → prioritize → execute → commit)
```bash
python main.py run --repo /path/to/your/repo --once
```

### Start the background daemon (runs every 60 minutes by default)
```bash
python main.py run --repo /path/to/your/repo
```

### Scan only (health report, no changes)
```bash
python main.py scan --repo /path/to/your/repo
```

### JSON output (for integration with other tools)
```bash
python main.py tasks --repo . --json | jq '.[].title'
```

## Configuration

Edit `config/settings.toml`:

```toml
[agent]
model = "claude-opus-4-6"
max_turns = 30          # max agent turns per task
max_budget_usd = 2.0    # USD budget cap per task

[scheduler]
interval_minutes = 60   # how often the cycle runs
run_on_startup = true   # run immediately on daemon start

[prioritizer]
auto_execute_min_impact = 3   # tasks below this impact are skipped
human_review_min_risk = 8     # tasks at or above this risk are deferred
max_tasks_per_cycle = 5       # cap on tasks per cycle

[git]
auto_commit = true
branch_prefix = "agent/maintenance"
author_name = "Autonomous Maintenance Agent"
author_email = "agent@maintenance.bot"
```

## Audit Log

Every task execution is recorded in `logs/audit.log` as newline-delimited JSON:

```json
{
  "cycle_start": "2026-03-05T10:00:00",
  "task_id": "docstring:agent/scanner.py",
  "task_kind": "add_function_docstring",
  "task_title": "Add missing docstrings in agent/scanner.py",
  "impact_score": 5,
  "risk_score": 1,
  "priority": 45,
  "success": true,
  "files_modified": ["agent/scanner.py"],
  "summary": "Added module and function docstrings to scanner.py.",
  "branch": "agent/maintenance/docstring-agent-scanner-py-20260305100032",
  "commit_sha": "abc1234..."
}
```

## Design Principles

1. **Minimal blast radius** — Only low-risk tasks execute autonomously; high-risk tasks surface for human review
2. **Auditability** — Every change is logged, branched, and committed with structured metadata
3. **Reactivity + Proactivity** — Scans on schedule but also responds to the current state of the codebase
4. **No silent failures** — Errors are logged and surfaced; the agent never silently skips or corrupts work
5. **Configurable autonomy** — Risk and impact thresholds are tunable without code changes
