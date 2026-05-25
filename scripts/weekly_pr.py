#!/usr/bin/env python3
"""
Saturday automation: generate the weekly report, open a PR with it, and have
Claude review + merge it.

Flow:
  1. Run scripts/weekly_report.py  -> docs/weekly/weekly_report_<week>.md
  2. Branch from origin/main (clean, isolated from any auto-committer on main)
  3. Commit just the report file, push, open a PR via `gh`
  4. Invoke headless `claude -p` to review the PR and merge it (squash).
     If claude is unavailable / errors, the PR is LEFT OPEN for manual review
     (never silently merged without the review step).

Designed to run unattended from com.quanttrading.weekly-report (Sat 20:00 local).

Usage:
    python scripts/weekly_pr.py                 # full run
    python scripts/weekly_pr.py --dry-run       # generate + show, no git/PR
    python scripts/weekly_pr.py --no-merge       # open PR but don't auto-merge
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PY = str(PROJECT_ROOT / "venv" / "bin" / "python")


def run(cmd, check=True, capture=False):
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True,
                       capture_output=capture)
    if check and r.returncode != 0:
        if capture:
            print(r.stdout); print(r.stderr, file=sys.stderr)
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(cmd)}")
    return r


def week_start_date(offset: int = 0) -> str:
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=offset)
    return monday.isoformat()


def main():
    ap = argparse.ArgumentParser(description="Weekly report -> PR -> review+merge")
    ap.add_argument("--week-offset", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true",
                    help="generate the report only; no git/push/PR")
    ap.add_argument("--no-merge", action="store_true",
                    help="open the PR but do not auto-merge (leave for manual review)")
    args = ap.parse_args()

    wk = week_start_date(args.week_offset)
    report_file = PROJECT_ROOT / "docs" / "weekly" / f"weekly_report_{wk}.md"
    branch = f"weekly-report/{wk}"

    # 1. Generate the report (writes docs/weekly/ + appends metrics history).
    print("==> generating report")
    run([PY, "scripts/weekly_report.py", "--week-offset", str(args.week_offset)])
    if not report_file.exists():
        raise SystemExit(f"report not generated at {report_file}")

    if args.dry_run:
        print(f"\n[dry-run] report at {report_file} — skipping git/PR")
        return

    # 2. Build the PR in an ISOLATED git worktree based on origin/main, so we
    #    never switch branches in the main checkout (the live bot may be running
    #    from it) and the PR contains ONLY the report — clean, isolated from any
    #    unpushed local commits / auto-committer on main.
    print("==> preparing isolated worktree")
    report_body = report_file.read_text()
    run(["git", "fetch", "origin", "main"])
    wt = PROJECT_ROOT / ".git" / "weekly_pr_worktree"
    run(["git", "worktree", "remove", "--force", str(wt)], check=False)
    run(["git", "worktree", "add", "-f", "-B", branch, str(wt), "origin/main"])
    try:
        wt_report = wt / "docs" / "weekly" / report_file.name
        wt_report.parent.mkdir(parents=True, exist_ok=True)
        wt_report.write_text(report_body)
        run(["git", "-C", str(wt), "add", "-f",
             f"docs/weekly/{report_file.name}"])
        run(["git", "-C", str(wt), "commit", "-m", f"docs: weekly report {wk}"])
        run(["git", "-C", str(wt), "push", "-u", "origin", branch,
             "--force-with-lease"])

        # 3. Open the PR (body = the report itself).
        print("==> opening PR")
        pr = run(["gh", "pr", "create", "--base", "main", "--head", branch,
                  "--title", f"Weekly report {wk}", "--body", report_body],
                 capture=True)
        pr_url = (pr.stdout or "").strip().splitlines()[-1] if pr.stdout else ""
        print(f"  PR: {pr_url}")
    finally:
        run(["git", "worktree", "remove", "--force", str(wt)], check=False)

    if args.no_merge:
        print("[--no-merge] PR opened, leaving for manual review.")
        return

    # 4. Claude reviews + merges. Headless, scoped to gh/git on this PR.
    print("==> Claude review + merge")
    review_prompt = (
        f"You are doing the Saturday weekly-report review for the PR at {pr_url} "
        f"(branch {branch}) in the current repo. Steps:\n"
        f"1. Read the PR's report file docs/weekly/weekly_report_{wk}.md.\n"
        "2. Post ONE concise PR review comment (gh pr comment) flagging only the "
        "important items: ML staleness/degradation, net-losing weeks, manual-trade "
        "losses, and any strategy whose performance score is declining.\n"
        "3. If the report's Status is not catastrophic, merge with "
        "`gh pr merge --squash --delete-branch`. If Status shows a hard failure "
        "you cannot assess, leave it open and say why in the comment.\n"
        "Be terse."
    )
    claude = _claude_bin()
    if not claude:
        print("  [warn] claude CLI not found — PR left open for manual review.")
        return
    r = subprocess.run(
        [claude, "-p", review_prompt, "--permission-mode", "bypassPermissions"],
        cwd=PROJECT_ROOT, text=True, capture_output=True,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        print("  [warn] claude review/merge failed — PR left open.")


def _claude_bin():
    # launchd has a minimal PATH (no ~/.local/bin), so check the known install
    # location before falling back to PATH lookup.
    import shutil
    explicit = Path.home() / ".local" / "bin" / "claude"
    if explicit.exists():
        return str(explicit)
    return shutil.which("claude")


if __name__ == "__main__":
    main()
