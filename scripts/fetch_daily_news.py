#!/usr/bin/env python3
"""
fetch_daily_news.py — Daily ForexFactory Event Fetcher

Scrapes today's high-impact USD events from ForexFactory and saves them to
  news/YYYY-MM-DD_news.csv

Also:
  - Deletes any news/YYYY-MM-DD_news.csv files older than 2 days
  - Notifies main.py by updating config_live.yaml's csv_path to today's file

Usage:
    # Run manually:
    python scripts/fetch_daily_news.py

    # Schedule via cron (runs at midnight and 6am IST every day):
    0 0,6 * * * cd /path/to/Quant_trading && source venv/bin/activate && python scripts/fetch_daily_news.py

Requirements:
    pip install requests beautifulsoup4
    (already in requirements.txt if present, otherwise: pip install requests bs4)
"""

import sys
import os
import csv
import json
import time
import re
import yaml
from datetime import datetime, timedelta
from pathlib import Path

# -─────────────────────────────────────────────────────────────────────────────
# Project root (so we can run from anywhere)
PROJECT_ROOT = Path(__file__).parent.parent
NEWS_DIR = PROJECT_ROOT / "news"
CONFIG_DIR = PROJECT_ROOT / "config"

CURRENCIES_TO_TRACK = {"USD"}
HIGH_IMPACT_KEYWORDS = {"high", "red"}   # ForexFactory impact levels we care about
MAX_RETRIES = 3
RETRY_DELAY_SEC = 5
MAX_FILE_AGE_DAYS = 2                    # Delete dated CSVs older than this

# -─────────────────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"[news_fetcher] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def fetch_forexfactory_events(target_date: datetime) -> list[dict]:
    """
    Scrape ForexFactory calendar for target_date.

    Returns a list of dicts: {date, time, currency, impact, event}
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        _log("ERROR: Missing dependencies. Run: pip install requests beautifulsoup4")
        sys.exit(1)

    # ForexFactory URL format: ?week=march3.2026  (uses the Monday of the week)
    # Easier: use the specific date format they support
    date_str = target_date.strftime("%b%d.%Y").lower()   # e.g. mar4.2026
    url = f"https://www.forexfactory.com/calendar?day={date_str}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _log(f"Fetching {url} (attempt {attempt}/{MAX_RETRIES})...")
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            break
        except Exception as e:
            _log(f"  Request failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SEC)
    else:
        _log("All retries failed — could not fetch ForexFactory data")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    events = []

    # ForexFactory table rows have class "calendar__row"
    rows = soup.find_all("tr", class_=lambda c: c and "calendar__row" in c)

    current_date_str = target_date.strftime("%m-%d-%Y")
    current_time = ""

    for row in rows:
        # Time cell (sometimes blank for multi-event same time)
        time_cell = row.find("td", class_=lambda c: c and "calendar__time" in c)
        if time_cell:
            t = time_cell.get_text(strip=True)
            if t:
                current_time = t  # keep last seen time for rows that share it

        # Currency cell
        currency_cell = row.find("td", class_=lambda c: c and "calendar__currency" in c)
        if not currency_cell:
            continue
        currency = currency_cell.get_text(strip=True).upper()
        if currency not in CURRENCIES_TO_TRACK:
            continue

        # Impact cell — look for the impact span/icon
        impact_cell = row.find("td", class_=lambda c: c and "calendar__impact" in c)
        if not impact_cell:
            continue
        impact_span = impact_cell.find("span")
        impact = ""
        if impact_span:
            classes = " ".join(impact_span.get("class", []))
            if "high" in classes.lower():
                impact = "High"
            elif "red" in classes.lower():
                impact = "High"  # some versions use 'red' CSS class
            elif "medium" in classes.lower():
                impact = "Medium"
            elif "low" in classes.lower():
                impact = "Low"
        if impact.lower() not in HIGH_IMPACT_KEYWORDS:
            continue

        # Event name cell
        title_cell = row.find("td", class_=lambda c: c and "calendar__event" in c)
        event_name = title_cell.get_text(strip=True) if title_cell else "Unknown"

        if not current_time:
            current_time = "00:00am"

        events.append({
            "Date": current_date_str,
            "Time": current_time,
            "Currency": currency,
            "Impact": impact,
            "Event": event_name,
        })
        _log(f"  Found: {current_time} | {currency} | {impact} | {event_name}")

    _log(f"Total high-impact USD events found: {len(events)}")
    return events


def save_events_csv(events: list[dict], target_date: datetime) -> Path:
    """Save events to news/YYYY-MM-DD_news.csv and return the path."""
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    csv_name = target_date.strftime("%Y-%m-%d") + "_news.csv"
    csv_path = NEWS_DIR / csv_name

    fieldnames = ["Date", "Time", "Currency", "Impact", "Event"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(events)

    _log(f"Saved {len(events)} events → {csv_path}")
    return csv_path


def delete_old_news_files(max_age_days: int = MAX_FILE_AGE_DAYS) -> None:
    """Delete news/YYYY-MM-DD_news.csv files older than max_age_days."""
    if not NEWS_DIR.exists():
        return

    cutoff = datetime.now() - timedelta(days=max_age_days)
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})_news\.csv$")

    for f in NEWS_DIR.iterdir():
        if not f.is_file():
            continue
        m = pattern.match(f.name)
        if not m:
            continue  # skip files like MAR_news.csv (manual ones)
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d")
            if file_date < cutoff:
                f.unlink()
                _log(f"Deleted old news file: {f.name}")
        except Exception as e:
            _log(f"Could not check/delete {f.name}: {e}")


def update_config_csv_path(csv_path: Path) -> None:
    """
    Update csv_path in ALL config_live*.yaml files under config/.
    This ensures every config picks up today's news file on next restart.
    """
    if not CONFIG_DIR.exists():
        _log(f"Config dir not found at {CONFIG_DIR}, skipping config update")
        return

    # Force forward slashes so YAML is portable AND so the string is safe to
    # inject into a regex replacement (backslashes would be parsed as backrefs).
    rel_path = csv_path.relative_to(PROJECT_ROOT).as_posix()
    config_files = sorted(CONFIG_DIR.glob("config_live*.yaml"))

    if not config_files:
        _log("No config_live*.yaml files found")
        return

    # Use a lambda replacement: avoids any re-escaping of the rel_path string.
    def _make_replacer(prefix_group_idx: int):
        return lambda m: m.group(prefix_group_idx) + rel_path

    for config_file in config_files:
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                content = f.read()

            if not content.strip():
                _log(f"Refusing to rewrite empty config: {config_file.name}")
                continue

            # Replace csv_path: <anything>.csv under news_filter section
            new_content = re.sub(
                r"(news_filter:.*?csv_path:\s*)[\w./\-]+\.csv",
                _make_replacer(1),
                content,
                flags=re.DOTALL,
            )

            if new_content == content:
                # Pattern didn't match — try simpler single-line replace
                new_content = re.sub(
                    r"(csv_path:\s*)[\w./\-]+\.csv",
                    _make_replacer(1),
                    content,
                )

            if new_content != content:
                # Hard safety gate: never write a truncated/empty result.
                if len(new_content) < max(200, int(len(content) * 0.5)):
                    _log(f"Refusing suspicious rewrite of {config_file.name} ({len(content)} -> {len(new_content)} bytes)")
                    continue
                with open(config_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new_content)
                _log(f"Updated {config_file.name} csv_path → {rel_path}")
            else:
                _log(f"No csv_path found in {config_file.name}, skipped")

        except Exception as e:
            _log(f"Failed to update {config_file.name}: {e}")


def main():
    today = datetime.now()
    _log(f"=== Daily News Fetch for {today.strftime('%Y-%m-%d')} ===")

    # 1. Delete stale files (> 2 days old)
    _log("Step 1: Cleaning up old news files...")
    delete_old_news_files()

    # 2. Fetch today's events
    _log("Step 2: Fetching today's ForexFactory events...")
    events = fetch_forexfactory_events(today)

    # 3. Save to CSV (even if empty — so main.py doesn't fail to load)
    _log("Step 3: Saving events CSV...")
    csv_path = save_events_csv(events, today)

    # 4. Update config so main.py picks up today's file
    _log("Step 4: Updating all config_live*.yaml files...")
    update_config_csv_path(csv_path)

    _log(f"=== Done! {len(events)} events saved to {csv_path.name} ===")

    if len(events) == 0:
        _log("NOTE: No high-impact USD events found today — news filter will pass all signals")


if __name__ == "__main__":
    main()
