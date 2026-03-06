"""
Terrance — Morning Report Generator
-------------------------------------
Reads the audit log and repository state to produce a concise daily briefing,
delivered at 7:55 AM CST every morning.

Usage (standalone):
    python -m agent.reporter --repo /path/to/repo

Scheduled automatically by MaintenanceScheduler at 7:55 AM CST.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Central Standard Time offset (UTC-6). CST does not observe DST.
CST = timezone(timedelta(hours=-6))


def _now_cst() -> datetime:
    return datetime.now(tz=CST)


def _read_audit_records(audit_file: str, since: datetime) -> list[dict]:
    """Return audit records written after *since* (CST-aware comparison)."""
    path = Path(audit_file)
    if not path.exists():
        return []

    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                # cycle_start is stored as UTC ISO string (no tzinfo suffix)
                raw = record.get("cycle_start", "")
                if raw:
                    ts = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
                    if ts >= since.astimezone(timezone.utc):
                        records.append(record)
            except (json.JSONDecodeError, ValueError):
                continue
    return records


def generate_report(
    audit_file: str,
    repo_root: str,
    lookback_hours: int = 24,
    output_dir: Optional[str] = None,
) -> str:
    """
    Build Terrance's morning report.

    Returns the report as a plain-text string and optionally saves it to
    *output_dir*/morning_report_YYYY-MM-DD.txt.
    """
    now = _now_cst()
    since = now - timedelta(hours=lookback_hours)
    records = _read_audit_records(audit_file, since)

    # Aggregate stats
    total = len(records)
    succeeded = [r for r in records if r.get("success")]
    failed = [r for r in records if not r.get("success")]
    branches = [r["branch"] for r in succeeded if r.get("branch")]
    files_touched: set[str] = set()
    for r in succeeded:
        files_touched.update(r.get("files_modified") or [])

    # Group by task kind
    kind_counts: dict[str, int] = {}
    for r in succeeded:
        kind = r.get("task_kind", "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    # Deferred (high-risk) tasks from the most recent cycle, if any
    deferred_seen: list[str] = []
    # Pull deferred titles from any audit records that carry that field
    # (we also capture them from scheduler cycle summaries stored in logs)
    # Best effort: list failed task titles as needing attention
    needs_attention = [r.get("task_title", r.get("task_id", "unknown")) for r in failed]

    date_str = now.strftime("%A, %B %-d, %Y")
    time_str = now.strftime("%-I:%M %p CST")

    lines: list[str] = []
    lines.append("=" * 62)
    lines.append(f"  Good morning! This is Terrance — your Maintenance Report")
    lines.append(f"  {date_str}  |  {time_str}")
    lines.append("=" * 62)
    lines.append("")
    lines.append(f"Repository:  {repo_root}")
    lines.append(f"Period:      Last {lookback_hours} hours")
    lines.append("")
    lines.append("─" * 62)
    lines.append("  OVERNIGHT ACTIVITY SUMMARY")
    lines.append("─" * 62)

    if total == 0:
        lines.append("")
        lines.append("  No maintenance tasks were executed in the last")
        lines.append(f"  {lookback_hours} hours. The agent may not have run, or")
        lines.append("  no qualifying tasks were found.")
        lines.append("")
    else:
        lines.append("")
        lines.append(f"  Tasks executed   :  {total}")
        lines.append(f"  Tasks succeeded  :  {len(succeeded)}")
        lines.append(f"  Tasks failed     :  {len(failed)}")
        lines.append(f"  Branches created :  {len(branches)}")
        lines.append(f"  Files modified   :  {len(files_touched)}")
        lines.append("")

        if kind_counts:
            lines.append("  Work breakdown:")
            for kind, count in sorted(kind_counts.items(), key=lambda x: -x[1]):
                label = kind.replace("_", " ").title()
                lines.append(f"    • {label:<35} {count:>3} task(s)")
            lines.append("")

        if succeeded:
            lines.append("─" * 62)
            lines.append("  COMPLETED TASKS")
            lines.append("─" * 62)
            lines.append("")
            for r in succeeded:
                title = r.get("task_title", r.get("task_id", ""))
                branch = r.get("branch", "")
                sha = (r.get("commit_sha") or "")[:8]
                impact = r.get("impact_score", "?")
                summary = r.get("summary", "")
                lines.append(f"  ✓  {title}")
                if summary:
                    lines.append(f"     {summary[:72]}")
                if branch:
                    lines.append(f"     Branch: {branch}")
                    if sha:
                        lines.append(f"     Commit: {sha}")
                lines.append(f"     Impact score: {impact}/10")
                lines.append("")

    if needs_attention:
        lines.append("─" * 62)
        lines.append("  NEEDS YOUR ATTENTION  (failed tasks)")
        lines.append("─" * 62)
        lines.append("")
        for title in needs_attention:
            lines.append(f"  ✗  {title}")
        lines.append("")
        lines.append("  Review the audit log for details:")
        lines.append(f"    {audit_file}")
        lines.append("")

    # Backlog section
    try:
        from .backlog import BacklogManager
        import os
        backlog_file = os.path.join(
            os.path.dirname(audit_file).replace("logs", "reports"),
            "terrance_backlog.json",
        )
        # Normalize: if audit_file is "logs/audit.log", look in "reports/"
        backlog_path = audit_file.replace("logs/audit.log", "reports/terrance_backlog.json")
        bm = BacklogManager(backlog_path)
        bsummary = bm.get_summary()
        by_status = bsummary.get("by_status", {})
        by_repo = bsummary.get("by_repo", {})

        total_tasks = sum(by_status.values())
        if total_tasks > 0:
            lines.append("─" * 62)
            lines.append("  LIVING BACKLOG  (all repositories)")
            lines.append("─" * 62)
            lines.append("")
            lines.append(f"  Pending (auto-queue)     :  {by_status.get('pending', 0)}")
            lines.append(f"  Deferred (human review)  :  {by_status.get('deferred', 0)}")
            lines.append(f"  Failed                   :  {by_status.get('failed', 0)}")
            lines.append(f"  Done (all time)          :  {by_status.get('done', 0)}")
            lines.append("")

            if len(by_repo) > 1:
                lines.append("  Per repository:")
                for repo_path, counts in sorted(by_repo.items()):
                    repo_name = repo_path.split("/")[-1] if "/" in repo_path else repo_path
                    pending_n = counts.get("pending", 0)
                    deferred_n = counts.get("deferred", 0)
                    lines.append(f"    {repo_name:<28}  {pending_n} pending, {deferred_n} deferred")
                lines.append("")

            # Top pending tasks
            top_pending = sorted(
                bm.get_pending(), key=lambda t: t.get("priority", 0), reverse=True
            )[:5]
            if top_pending:
                lines.append("  Top items in queue:")
                for t in top_pending:
                    repo_name = t["repo"].split("/")[-1]
                    lines.append(f"    [{t.get('impact_score','?')}/10] {t['title'][:48]}  ({repo_name})")
                lines.append("")

            deferred_items = bm.get_deferred()
            if deferred_items:
                lines.append("  Needs your review (deferred):")
                for t in deferred_items[:5]:
                    repo_name = t["repo"].split("/")[-1]
                    lines.append(f"    [risk {t.get('risk_score','?')}/10] {t['title'][:45]}  ({repo_name})")
                if len(deferred_items) > 5:
                    lines.append(f"    … and {len(deferred_items) - 5} more")
                lines.append("")

            lines.append(f"  Full backlog:  reports/BACKLOG.md")
            lines.append("")
    except Exception:
        pass  # backlog is optional; never crash the report

    lines.append("─" * 62)
    lines.append("  QUICK TIPS")
    lines.append("─" * 62)
    lines.append("")
    if branches:
        lines.append("  • Review the new branches above and open PRs for any")
        lines.append("    changes you want to merge into your main branch.")
    lines.append("  • Run  python main.py tasks    to preview today's queue.")
    lines.append("  • Run  python main.py scan     to see current repo health.")
    lines.append("  • Run  python main.py backlog  to browse the full backlog.")
    lines.append("")
    lines.append("=" * 62)
    lines.append("  Have a great day!  — Terrance")
    lines.append("=" * 62)
    lines.append("")

    report = "\n".join(lines)

    # Optionally persist to disk
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        filename = out_path / f"morning_report_{now.strftime('%Y-%m-%d')}.txt"
        filename.write_text(report, encoding="utf-8")
        logger.info("Terrance morning report saved to %s", filename)

    return report


def deliver_report(config: dict, repo_root: str) -> None:
    """
    Called by the scheduler at 7:55 AM CST.
    Generates the report, prints it to stdout, and saves it to reports/.
    """
    audit_file = config.get("logging", {}).get("audit_file", "logs/audit.log")
    output_dir = config.get("reporting", {}).get("output_dir", "reports")
    lookback_hours = config.get("reporting", {}).get("lookback_hours", 24)

    report = generate_report(
        audit_file=audit_file,
        repo_root=repo_root,
        lookback_hours=lookback_hours,
        output_dir=output_dir,
    )
    print(report)
    logger.info("Terrance morning report delivered.")
