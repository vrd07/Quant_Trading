"""
ForexFactory News Event Filter for XAUUSD.

Loads high-impact USD events scraped from ForexFactory's calendar
and provides a blackout check so the trading system can pause around
major economic releases (CPI, NFP, FOMC, etc.).

Setup:
    git clone https://github.com/fizahkhalid/forex_factory_calendar_news_scraper.git
    pip install -r requirements.txt
    python scraper.py          # generates news/FEB_news.csv

Usage:
    from src.data.news_filter import load_ff_events, is_news_blackout
    events = load_ff_events('news/FEB_news.csv')
    if is_news_blackout(datetime.now(), events):
        print("Skip trading — high-impact news window")
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import Iterable, Optional, Union

import pytz


def load_ff_events(
    csv_path: str = "news/MAR_news.csv",
    currency: Union[str, Iterable[str]] = "USD",
    impacts: Optional[list] = None,
) -> pd.DataFrame:
    """
    Load ForexFactory calendar events from a scraped CSV.

    Args:
        csv_path: Path to the CSV file.
        currency: Currency filter — accepts a single ticker ("USD") or a
            list/tuple (["USD", "EUR"]). YAML can write either form. Default
            USD preserves back-compat for configs that only trade USD pairs.
        impacts: List of impact levels to keep (default: ['high', 'red']).

    Returns:
        DataFrame of filtered events with parsed time column.
    """
    if impacts is None:
        impacts = ["high", "red"]

    # Normalise currency arg to an upper-cased list so downstream filtering
    # is uniform whether YAML wrote `currency: USD` or `currency: [USD, EUR]`.
    if isinstance(currency, str):
        currencies = [currency.upper()]
    else:
        currencies = [str(c).upper() for c in currency]

    df = pd.read_csv(csv_path)

    # Normalise column names
    df.columns = [c.strip().lower() for c in df.columns]

    # Filter currency (single or multiple)
    if "currency" in df.columns:
        df = df[df["currency"].str.strip().str.upper().isin(currencies)]

    # Filter impact
    if "impact" in df.columns:
        df = df[df["impact"].str.strip().str.lower().isin([i.lower() for i in impacts])]

    # Parse time. fetch_daily_news.py writes 12-hour times with am/pm suffix
    # ("11:30pm", "07:00am") and uses "00:00am" as a placeholder when the
    # ForexFactory row has no time. Normalise case for %p, drop placeholders.
    if "time" in df.columns:
        times = df["time"].astype(str).str.strip().str.upper()
        times = times.where(~times.isin({"", "NAN", "00:00AM", "ALL DAY", "TENTATIVE"}), other=pd.NA)
        df["time"] = pd.to_datetime(times, format="%I:%M%p", errors="coerce")

    return df.reset_index(drop=True)


def is_news_blackout(
    current_time: datetime,
    events_df: pd.DataFrame,
    buffer_min: int = 15,
    timezone: str = "Asia/Kolkata",
) -> bool:
    """
    Check if current_time falls within the news blackout window.

    The blackout window is [event_time − buffer, event_time + buffer].

    Args:
        current_time: Current datetime (tz-naive or tz-aware).
        events_df: DataFrame from load_ff_events().
        buffer_min: Minutes before and after event to block trading.
        timezone: Timezone for comparison.

    Returns:
        True if inside a blackout window (should NOT trade).
    """
    if events_df.empty or "time" not in events_df.columns:
        return False

    tz = pytz.timezone(timezone)

    # Make current_time timezone-aware
    if current_time.tzinfo is None:
        current_aware = tz.localize(current_time)
    else:
        current_aware = current_time.astimezone(tz)

    buffer = timedelta(minutes=buffer_min)

    for _, row in events_df.iterrows():
        event_time = row["time"]
        if pd.isna(event_time):
            continue

        # Build today's event datetime from the stored time-of-day
        event_dt = current_aware.replace(
            hour=event_time.hour,
            minute=event_time.minute,
            second=0,
            microsecond=0,
        )

        if (event_dt - buffer) <= current_aware <= (event_dt + buffer):
            return True

    return False
