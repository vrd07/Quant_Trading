"""Generate the equity-curve PNG used in the project README.

Reads the audit_v3_budget_*.csv files from data/backtests and plots each
strategy's equity curve on a single chart. Output: docs/equity_curves.png.

Re-run this script after a fresh audit cycle:
    python docs/generate_equity_curve.py
"""

from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ROOT = Path(__file__).resolve().parent.parent
BT = ROOT / "data" / "backtests"
OUT = ROOT / "docs" / "equity_curves.png"

# Audit-v3 budget run is the cleanest comparable set: all strategies under the
# same per-trade USD risk budget over the same backtest window.
STRATEGIES = [
    ("audit_v3_budget_kalman_regime",  "Kalman Regime",     "#2E86AB"),
    ("audit_v3_budget_momentum",       "Momentum",          "#A23B72"),
    ("audit_v3_budget_breakout",       "Breakout",          "#F18F01"),
    ("audit_v3_budget_mini_medallion", "Mini Medallion v1", "#C73E1D"),
]

fig, ax = plt.subplots(figsize=(12, 6))

for stem, label, color in STRATEGIES:
    csv = BT / f"{stem}.csv"
    if not csv.exists():
        print(f"skip: {csv} not found")
        continue
    df = pd.read_csv(csv, parse_dates=["timestamp"])
    if df.empty:
        continue
    ax.plot(df["timestamp"], df["equity"], label=label, color=color, linewidth=1.5)

ax.set_title(
    "Per-Strategy Equity Curve (audit-v3 budget run, Jan 2025 → Mar 2026)",
    fontsize=14, fontweight="bold", pad=15,
)
ax.set_xlabel("Date")
ax.set_ylabel("Equity (USD)")
ax.axhline(50000, color="gray", linestyle="--", linewidth=0.8, alpha=0.5, label="Starting equity")
ax.legend(loc="upper left", framealpha=0.95)
ax.grid(True, alpha=0.3)

ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
fig.autofmt_xdate()

# Footer with summary stats
footer = (
    "Kalman Regime: +4.62%, PF 1.15, 1252 trades, DD -2.74%   |   "
    "Momentum: +4.68%, PF 1.10, 2023 trades, DD -5.33%   |   "
    "Breakout: +1.23%, PF 1.02, 907 trades, DD -5.60%   |   "
    "Mini Medallion v1: -3.44%, PF 0.85 (disabled, retuned to v5)"
)
fig.text(0.5, 0.01, footer, ha="center", fontsize=8, style="italic", color="#444")

plt.tight_layout(rect=[0, 0.03, 1, 1])
fig.savefig(OUT, dpi=140, bbox_inches="tight")
print(f"wrote {OUT}")
