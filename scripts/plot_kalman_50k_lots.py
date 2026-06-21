import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import yaml
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scripts.backtest_kalman_2026_fixed import (
    load_15m_2026, generate_signals, simulate, stats, max_drawdown)

CAPITAL = 50_000.0
SL, RR, COST, CAP = 33.0, 1.0, 0.20, 295.0
cfg = yaml.safe_load(open(PROJECT_ROOT / "config/config_live_50000.yaml"))
bars = load_15m_2026()
sig = generate_signals(bars, cfg, refresh=False)

# Data span
span_start, span_end = bars.index.min(), bars.index.max()
days = (span_end - span_start).days
months = days / 30.44
print(f"DATA SPAN: {span_start.date()} -> {span_end.date()}  "
      f"= {days} calendar days = {months:.2f} months  ({len(bars):,} 15m bars)")

def eq(lot):
    t, _ = simulate(bars, sig, sl_pts=SL, rr=RR, lot=lot, cost=COST, daily_cap=CAP)
    t = t.sort_values("exit_ts").copy()
    t["equity"] = CAPITAL + t["pnl"].cumsum()
    s = stats(t); dd, ddp = max_drawdown(t, CAPITAL)
    return t, s, dd, ddp

t04, s04, dd04, ddp04 = eq(0.04)
t40, s40, dd40, ddp40 = eq(0.40)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), sharex=True,
                               gridspec_kw={"height_ratios": [3, 1]})

# --- Equity panel ---
ax1.plot(pd.to_datetime(t04["exit_ts"]), t04["equity"], lw=1.6,
         color="#1f77b4", label=f"lot 0.04  →  +${s04['net']:,.0f} (+{s04['net']/CAPITAL*100:.1f}%), DD {ddp04:.1f}%")
ax1.plot(pd.to_datetime(t40["exit_ts"]), t40["equity"], lw=1.6,
         color="#d62728", label=f"lot 0.40  →  +${s40['net']:,.0f} (+{s40['net']/CAPITAL*100:.1f}%), DD {ddp40:.1f}%")
ax1.axhline(CAPITAL, color="#888", ls="--", lw=1, label="start $50,000")
ax1.axhline(CAPITAL - 3500, color="#000", ls=":", lw=1.4,
            label="live kill-switch −7% ($46,500)")
# Mark where lot 0.40 first breaches the kill switch
breach = t40[t40["equity"] <= CAPITAL - 3500]
if len(breach):
    bx = pd.to_datetime(breach["exit_ts"].iloc[0])
    ax1.axvline(bx, color="#d62728", ls="-", lw=0.8, alpha=0.5)
    ax1.annotate(f"lot 0.40 trips the\nkill switch here\n({bx.date()})",
                 xy=(bx, CAPITAL - 3500), xytext=(bx, CAPITAL - 12000),
                 color="#d62728", fontsize=9, ha="center",
                 arrowprops=dict(arrowstyle="->", color="#d62728"))
ax1.set_ylabel("Equity (USD)")
ax1.set_title("Kalman v2 — $50k account, lot 0.04 vs lot 0.40 (XAUUSD 15m, 2026 YTD, kill switch OFF in sim)")
ax1.legend(loc="upper left", fontsize=9)
ax1.grid(alpha=0.25)

# --- Drawdown panel ---
for t, c, lbl in [(t04, "#1f77b4", "lot 0.04"), (t40, "#d62728", "lot 0.40")]:
    peak = t["equity"].cummax()
    ddpct = (t["equity"] - peak) / CAPITAL * 100
    ax2.fill_between(pd.to_datetime(t["exit_ts"]), ddpct, 0, color=c, alpha=0.30)
    ax2.plot(pd.to_datetime(t["exit_ts"]), ddpct, color=c, lw=1, label=lbl)
ax2.axhline(-7, color="#000", ls=":", lw=1.4, label="−7% live limit")
ax2.set_ylabel("Drawdown (% of $50k)")
ax2.set_xlabel("Date")
ax2.legend(loc="lower left", fontsize=9)
ax2.grid(alpha=0.25)

fig.tight_layout()
out = PROJECT_ROOT / "reports/figs/kalman_50k_lot_compare.png"
fig.savefig(out, dpi=130)
print(f"SAVED: {out}")
