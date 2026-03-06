"""
Bill — Entry Point
------------------------------------------
Usage:
  python main.py run   [--repo PATH] [--once]
  python main.py scan  [--repo PATH]
  python main.py tasks [--repo PATH]

Commands:
  run    Start the maintenance agent (daemon or one-shot)
  scan   Only scan the repo and print the health report
  tasks  Scan + identify + prioritize tasks, print them, do NOT execute
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import toml
import typer
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("log_file", "logs/agent.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=handlers,
    )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(config_path: str = "config/settings.toml") -> dict:
    path = Path(config_path)
    if not path.exists():
        # Try relative to this file's parent
        path = Path(__file__).parent / config_path
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return toml.load(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(help="Autonomous Repository Maintenance Agent")
console = Console()

_REPO_OPTION = typer.Option(
    None,
    "--repo",
    "-r",
    help="Path to the target repository (default: current working directory)",
)
_CONFIG_OPTION = typer.Option(
    "config/settings.toml",
    "--config",
    "-c",
    help="Path to settings.toml",
)


@app.command()
def run(
    repo: str = _REPO_OPTION,
    config_path: str = _CONFIG_OPTION,
    once: bool = typer.Option(False, "--once", help="Run a single cycle and exit"),
    no_startup: bool = typer.Option(False, "--no-startup", help="Skip the immediate startup cycle"),
):
    """Start the autonomous maintenance agent."""
    config = _load_config(config_path)
    _setup_logging(config)
    repo_root = str(Path(repo).resolve()) if repo else str(Path.cwd())

    console.print(f"[bold green]Bill[/bold green]")
    console.print(f"Repository : {repo_root}")
    console.print(f"Config     : {config_path}")
    console.print(f"Mode       : {'one-shot' if once else 'daemon'}")

    from agent.scheduler import MaintenanceScheduler
    scheduler = MaintenanceScheduler(config, repo_root)

    if once:
        summary = scheduler.run_once()
        _print_cycle_summary(summary)
    else:
        run_now = config["scheduler"].get("run_on_startup", True) and not no_startup
        scheduler.start(run_now=run_now, blocking=True)


@app.command()
def scan(
    repo: str = _REPO_OPTION,
    config_path: str = _CONFIG_OPTION,
    output_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """Scan the repository and display a health report."""
    config = _load_config(config_path)
    _setup_logging(config)
    repo_root = str(Path(repo).resolve()) if repo else str(Path.cwd())

    from agent.scanner import RepositoryScanner
    scanner = RepositoryScanner(config)
    snapshot = scanner.scan(repo_root)

    if output_json:
        import dataclasses
        print(json.dumps(dataclasses.asdict(snapshot), default=str, indent=2))
        return

    console.print(f"\n[bold]Repository Health Report[/bold] — {repo_root}")
    console.print(f"Scanned at: {snapshot.scanned_at.isoformat()}")

    t = Table(show_header=True, header_style="bold magenta")
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    for k, v in snapshot.summary.items():
        t.add_row(k.replace("_", " ").title(), str(v))
    console.print(t)

    if snapshot.doc_gaps:
        console.print(f"\n[yellow]Documentation Gaps ({len(snapshot.doc_gaps)})[/yellow]")
        for g in snapshot.doc_gaps[:10]:
            console.print(f"  {g.file_path}:{g.line}  {g.kind}  {g.symbol or ''}")
        if len(snapshot.doc_gaps) > 10:
            console.print(f"  … and {len(snapshot.doc_gaps) - 10} more")

    if snapshot.code_smells:
        console.print(f"\n[yellow]Code Smells ({len(snapshot.code_smells)})[/yellow]")
        for s in snapshot.code_smells[:10]:
            console.print(f"  {s.file_path}:{s.line}  [{s.kind}] {s.description[:80]}")
        if len(snapshot.code_smells) > 10:
            console.print(f"  … and {len(snapshot.code_smells) - 10} more")


@app.command()
def tasks(
    repo: str = _REPO_OPTION,
    config_path: str = _CONFIG_OPTION,
    skip_prioritize: bool = typer.Option(False, "--skip-prioritize", help="Skip the Claude API prioritization step"),
    output_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """Scan, identify, and prioritize maintenance tasks (dry run — no execution)."""
    config = _load_config(config_path)
    _setup_logging(config)
    repo_root = str(Path(repo).resolve()) if repo else str(Path.cwd())

    from agent.scanner import RepositoryScanner
    from agent.identifier import TaskIdentifier
    from agent.prioritizer import TaskPrioritizer

    scanner = RepositoryScanner(config)
    snapshot = scanner.scan(repo_root)

    identifier = TaskIdentifier()
    task_list = identifier.identify(snapshot)

    if not skip_prioritize:
        prioritizer = TaskPrioritizer(config)
        task_list = prioritizer.prioritize(task_list)

    if output_json:
        import dataclasses
        print(json.dumps([dataclasses.asdict(t) for t in task_list], default=str, indent=2))
        return

    console.print(f"\n[bold]Maintenance Tasks[/bold] — {repo_root}")
    console.print(f"Total identified: {len(task_list)}")

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("#", justify="right", width=3)
    t.add_column("Priority", justify="right", width=8)
    t.add_column("Impact", justify="right", width=6)
    t.add_column("Risk", justify="right", width=5)
    t.add_column("Kind", width=25)
    t.add_column("Title")

    for i, task in enumerate(task_list, 1):
        risk_color = "red" if task.risk_score >= 8 else "yellow" if task.risk_score >= 5 else "green"
        t.add_row(
            str(i),
            str(task.priority),
            str(task.impact_score),
            f"[{risk_color}]{task.risk_score}[/{risk_color}]",
            task.kind.value,
            task.title[:70],
        )
    console.print(t)


@app.command()
def report(
    repo: str = _REPO_OPTION,
    config_path: str = _CONFIG_OPTION,
    hours: int = typer.Option(24, "--hours", "-h", help="Hours of history to include in the report"),
    save: bool = typer.Option(True, "--save/--no-save", help="Save report to reports/ directory"),
):
    """Generate and print Bill's morning report immediately."""
    config = _load_config(config_path)
    _setup_logging(config)
    repo_root = str(Path(repo).resolve()) if repo else str(Path.cwd())

    from agent.reporter import generate_report
    audit_file = config.get("logging", {}).get("audit_file", "logs/audit.log")
    output_dir = config.get("reporting", {}).get("output_dir", "reports") if save else None

    result = generate_report(
        audit_file=audit_file,
        repo_root=repo_root,
        lookback_hours=hours,
        output_dir=output_dir,
    )
    print(result)


@app.command()
def backlog(
    config_path: str = _CONFIG_OPTION,
    all_repos: bool = typer.Option(False, "--all", help="Show tasks from all repos (default: current repo only)"),
    show_done: bool = typer.Option(False, "--done", help="Include completed tasks"),
):
    """Browse Bill's living cross-repository backlog."""
    config = _load_config(config_path)
    _setup_logging(config)

    output_dir = config.get("reporting", {}).get("output_dir", "reports")
    backlog_file = f"{output_dir}/bill_backlog.json"

    from agent.backlog import BacklogManager
    bm = BacklogManager(backlog_file)

    repo_filter = None if all_repos else str(Path.cwd())
    pending = bm.get_pending(repo=repo_filter)
    deferred = bm.get_deferred(repo=repo_filter)
    summary = bm.get_summary()

    console.print(f"\n[bold]Bill's Living Backlog[/bold]")
    console.print(f"State file : {backlog_file}")
    console.print(f"Scope      : {'all repositories' if all_repos else str(Path.cwd())}")

    by_status = summary.get("by_status", {})
    console.print(
        f"\nPending [green]{by_status.get('pending', 0)}[/green]  "
        f"Deferred [yellow]{by_status.get('deferred', 0)}[/yellow]  "
        f"Failed [red]{by_status.get('failed', 0)}[/red]  "
        f"Done [cyan]{by_status.get('done', 0)}[/cyan]"
    )

    if pending:
        console.print("\n[bold green]Pending — Auto-Queue[/bold green]")
        t = Table(show_header=True, header_style="bold green")
        t.add_column("Priority", justify="right", width=8)
        t.add_column("Impact", justify="right", width=6)
        t.add_column("Kind", width=22)
        t.add_column("Title")
        t.add_column("Repo", width=14)
        for task in sorted(pending, key=lambda x: x.get("priority", 0), reverse=True):
            repo_name = Path(task["repo"]).name
            t.add_row(
                str(task.get("priority", "?")),
                str(task.get("impact_score", "?")),
                task["kind"].replace("_", " ").title(),
                task["title"][:55],
                repo_name,
            )
        console.print(t)

    if deferred:
        console.print("\n[bold yellow]Deferred — Needs Human Review[/bold yellow]")
        t = Table(show_header=True, header_style="bold yellow")
        t.add_column("Risk", justify="right", width=5)
        t.add_column("Impact", justify="right", width=6)
        t.add_column("Kind", width=22)
        t.add_column("Title")
        t.add_column("Repo", width=14)
        for task in sorted(deferred, key=lambda x: x.get("impact_score", 0), reverse=True):
            repo_name = Path(task["repo"]).name
            t.add_row(
                f"[red]{task.get('risk_score', '?')}[/red]",
                str(task.get("impact_score", "?")),
                task["kind"].replace("_", " ").title(),
                task["title"][:55],
                repo_name,
            )
        console.print(t)

    if show_done:
        done_tasks = [
            e for e in bm.all_tasks()
            if e["status"] == "done"
            and (repo_filter is None or e["repo"] == repo_filter)
        ]
        if done_tasks:
            console.print("\n[bold cyan]Completed[/bold cyan]")
            t = Table(show_header=True, header_style="bold cyan")
            t.add_column("Completed", width=10)
            t.add_column("Kind", width=22)
            t.add_column("Title")
            t.add_column("Branch")
            for task in sorted(done_tasks, key=lambda x: x.get("completed_at") or "", reverse=True)[:20]:
                completed = (task.get("completed_at") or "")[:10]
                branch = (task.get("branch") or "—").split("/")[-1][:28]
                t.add_row(
                    completed,
                    task["kind"].replace("_", " ").title(),
                    task["title"][:55],
                    branch,
                )
            console.print(t)

    if not pending and not deferred:
        console.print("\n[dim]No tasks in the backlog yet. Run the agent to populate it.[/dim]")

    console.print(f"\n[dim]Full backlog → {output_dir}/BACKLOG.md[/dim]\n")


def _print_cycle_summary(summary: dict):
    console.print(f"\n[bold]Maintenance Cycle Complete[/bold]")
    console.print(f"Tasks identified : {summary['tasks_identified']}")
    console.print(f"Tasks approved   : {summary['tasks_approved']}")
    console.print(f"Tasks succeeded  : {summary['tasks_succeeded']}")
    console.print(f"Tasks failed     : {summary['tasks_failed']}")
    console.print(f"Tasks deferred   : {summary['tasks_deferred']} (high-risk → human review)")

    if summary.get("deferred_titles"):
        console.print("\n[yellow]Deferred tasks (require human review):[/yellow]")
        for title in summary["deferred_titles"]:
            console.print(f"  • {title}")

    if summary.get("task_results"):
        console.print("\n[bold]Executed tasks:[/bold]")
        for r in summary["task_results"]:
            status = "[green]✓[/green]" if r["success"] else "[red]✗[/red]"
            console.print(f"  {status} {r['task_title']}")
            if r.get("branch"):
                console.print(f"      Branch: {r['branch']}  Commit: {r.get('commit_sha', '')[:8]}")
            if r.get("error"):
                console.print(f"      Error: {r['error']}", style="red")


if __name__ == "__main__":
    app()
