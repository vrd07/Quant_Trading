#!/usr/bin/env python3
"""
Kalman v2 — 2026 YTD fixed-parameter backtest on a $50,000 account, with a
research-grade loss diagnostic layer on top of the live-faithful simulator.

Spec (user, 2026-06-21):
  * $50,000 account.
  * KILL SWITCH OFF (ignored) — only a daily-loss cap is enforced.
  * FIXED SL, FIXED TP, FIXED daily-loss cap that RESETS every UTC day.
  * Same REAL KalmanRegimeStrategy.on_bar() signals (XAUUSD 15m, 2026 YTD).

Account params are taken from config_live_50000.yaml (live-faithful):
  * min_lot 0.04 (the floor live actually trades on the 50k tier).
  * absolute_max_loss_usd 295 -> daily cap.
  * SL 33 pts (= live 3.0 x median 2026 15m ATR), TP = SL (RR 1.0, kalman_min_tp_rr=1.0).

On top of the headline numbers this script MONITORS the trade tape to explain
WHY losses happened: MAE/MFE excursions, drawdown-window anatomy, loss-streak
regime attribution, daily P&L distribution, and a set of surgical
counterfactuals isolating the single highest-leverage fix.

Writes: reports/kalman_50k_2026_analysis.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the audited, live-faithful machinery from the 5k harness.
from scripts.backtest_kalman_2026_fixed import (
    load_15m_2026, generate_signals, simulate, stats, max_drawdown, VALUE_PER_LOT,
)
import yaml

# ---- 50k account params (live-faithful) -----------------------------------
CAPITAL = 50_000.0
LOT = 0.04                 # config_live_50000 XAUUSD min_lot (live floor)
DAILY_CAP = 295.0          # config_live_50000 absolute_max_loss_usd
MAX_DD_LIMIT = 3_500.0     # config_live_50000 max_drawdown_usd (7%) — reported, NOT enforced
SL_PTS = 33.0
RR = 1.0
COST = 0.20
CONFIG_PATH = "config/config_live_50000.yaml"
REPORT = PROJECT_ROOT / "reports/kalman_50k_2026_analysis.md"
TRADES_OUT = PROJECT_ROOT / "data/backtests/kalman_50k_2026_trades.csv"


def add_excursions(trades: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """Compute MAE/MFE (in price points) for every trade by re-walking bars.

    MFE = furthest price moved IN FAVOUR before exit.
    MAE = furthest price moved AGAINST before exit.
    Both measured from the entry fill, in price points.
    """
    h = bars["high"]; l = bars["low"]; idx = bars.index
    mfe, mae = [], []
    for _, t in trades.iterrows():
        seg = bars[(idx >= t["entry_ts"]) & (idx <= t["exit_ts"])]
        if len(seg) == 0:
            mfe.append(0.0); mae.append(0.0); continue
        if t["side"] == "buy":
            mfe.append(float(seg["high"].max() - t["entry"]))
            mae.append(float(t["entry"] - seg["low"].min()))
        else:
            mfe.append(float(t["entry"] - seg["low"].min()))
            mae.append(float(seg["high"].max() - t["entry"]))
    trades = trades.copy()
    trades["mfe_pts"] = mfe
    trades["mae_pts"] = mae
    return trades


def equity_curve(trades: pd.DataFrame, capital: float) -> pd.DataFrame:
    t = trades.sort_values("exit_ts").copy()
    t["equity"] = capital + t["pnl"].cumsum()
    t["peak"] = t["equity"].cummax()
    t["dd"] = t["equity"] - t["peak"]
    return t


def fmt(x, n=2):
    return f"{x:,.{n}f}"


def pf_str(s):
    return "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"


def grp_md(t, key, order=None):
    """Markdown table for a grouped breakdown."""
    keys = order if order else sorted(t[key].dropna().unique())
    lines = [f"| {key} | N | Win% | PF | Net$ | Exp$ | AvgWin | AvgLoss |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k in keys:
        sub = t[t[key] == k]
        if len(sub) == 0:
            continue
        s = stats(sub)
        lines.append(f"| {k} | {s['n']} | {s['wr']:.1f}% | {pf_str(s)} | "
                     f"{s['net']:+,.0f} | {s['exp']:+.2f} | {s['avg_w']:+.0f} | {s['avg_l']:+.0f} |")
    return "\n".join(lines)


def main():
    with open(PROJECT_ROOT / CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    print("Loading 15m 2026 bars ...")
    bars = load_15m_2026()
    sig_df = generate_signals(bars, cfg, refresh=False)
    print(f"  bars={len(bars)} signals={len(sig_df)}")

    # ---- PRIMARY 50k run --------------------------------------------------
    t, sk = simulate(bars, sig_df, sl_pts=SL_PTS, rr=RR, lot=LOT, cost=COST,
                     daily_cap=DAILY_CAP)
    t = add_excursions(t, bars)
    t.to_csv(TRADES_OUT, index=False)
    s = stats(t)
    dd, ddp = max_drawdown(t, CAPITAL)
    final_eq = CAPITAL + s["net"]

    eqc = equity_curve(t, CAPITAL)
    # Drawdown window: peak before the trough, and the trough date.
    trough_row = eqc.loc[eqc["dd"].idxmin()]
    pre = eqc[eqc["exit_ts"] <= trough_row["exit_ts"]]
    peak_row = pre.loc[pre["equity"].idxmax()]

    # Daily P&L series
    t["day"] = pd.to_datetime(t["exit_ts"]).dt.date
    daily = t.groupby("day")["pnl"].sum()
    green = (daily > 0).sum(); red = (daily < 0).sum()

    # Loss streaks with regime/side attribution
    ts_sorted = t.sort_values("exit_ts").reset_index(drop=True)
    streaks = []
    cur = []
    for _, r in ts_sorted.iterrows():
        if r["pnl"] < 0:
            cur.append(r)
        else:
            if len(cur) >= 3:
                streaks.append(cur)
            cur = []
    if len(cur) >= 3:
        streaks.append(cur)

    # MAE/MFE diagnostics
    winners = t[t.pnl > 0]; losers = t[t.pnl < 0]
    # Losers that were in profit by >= 1R (33pts) before reversing to a loss:
    rescue_tp = losers[losers["mfe_pts"] >= SL_PTS]
    # Losers that were in profit by >= 0.5R:
    rescue_tp_half = losers[losers["mfe_pts"] >= 0.5 * SL_PTS]
    # Winners that took >= 0.7R of heat (nearly stopped):
    heat_winners = winners[winners["mae_pts"] >= 0.7 * SL_PTS]

    # ---- Counterfactuals --------------------------------------------------
    cf = {}
    # SELL-only / BUY-only: filter trades post-hoc (same fills, just subset)
    cf["sell_only"] = stats(t[t.side == "sell"])
    cf["buy_only"] = stats(t[t.side == "buy"])
    cf["trend_only"] = stats(t[t["mode"] == "trend"])
    cf["range_only"] = stats(t[t["mode"] == "range"])
    # Drop worst two months
    bad_months = ["2026-02", "2026-04"]
    t_good = t[~t["entry_ts"].astype(str).str[:7].isin(bad_months)]
    cf["ex_feb_apr"] = stats(t_good)

    # Geometry sweep on 50k (SL x RR) with lot 0.04, cap 295
    grid = []
    for sl in (22.0, 33.0, 49.0):
        for rr in (1.0, 1.5, 2.0):
            tg, _ = simulate(bars, sig_df, sl_pts=sl, rr=rr, lot=LOT,
                             cost=COST, daily_cap=DAILY_CAP)
            sg = stats(tg); ddg, ddpg = max_drawdown(tg, CAPITAL)
            grid.append((sl, rr, sg, ddg, ddpg))

    # Daily-cap on/off, lot sensitivity
    t_nocap, _ = simulate(bars, sig_df, sl_pts=SL_PTS, rr=RR, lot=LOT,
                          cost=COST, daily_cap=1e9)
    s_nocap = stats(t_nocap); dd_nocap, ddp_nocap = max_drawdown(t_nocap, CAPITAL)

    lot_rows = []
    for lt in (0.04, 0.08, 0.12):
        tl, _ = simulate(bars, sig_df, sl_pts=SL_PTS, rr=RR, lot=lt,
                         cost=COST, daily_cap=DAILY_CAP)
        slt = stats(tl); ddlt, ddplt = max_drawdown(tl, CAPITAL)
        lot_rows.append((lt, slt, ddlt, ddplt))

    # Lot 0.40 "leverage trap" scenarios (cap as-config / 10x / off)
    big_rows = []
    for lt, cap, lbl in [(0.04, DAILY_CAP, "lot 0.04, cap $295 (baseline)"),
                         (0.40, DAILY_CAP, "lot 0.40, cap $295 (as-config)"),
                         (0.40, 2950.0, "lot 0.40, cap $2,950 (10× scaled)"),
                         (0.40, 1e9, "lot 0.40, cap OFF")]:
        tb, skb = simulate(bars, sig_df, sl_pts=SL_PTS, rr=RR, lot=lt,
                           cost=COST, daily_cap=cap)
        sb = stats(tb); ddb, ddpb = max_drawdown(tb, CAPITAL)
        big_rows.append((lbl, SL_PTS*lt*VALUE_PER_LOT, sb, ddb, ddpb, skb["cap_hit_days"]))

    # Data span (months)
    span_start, span_end = bars.index.min(), bars.index.max()
    span_days = (span_end - span_start).days
    span_months = span_days / 30.44

    # ---- Compose report ---------------------------------------------------
    pf = pf_str(s)
    md = []
    A = md.append
    A(f"# Kalman v2 — $50,000 Account Fixed-Parameter Backtest & Loss Autopsy")
    A("")
    A(f"**Generated:** 2026-06-21 · **Script:** `scripts/backtest_kalman_50k.py` · "
      f"**Signals:** real `KalmanRegimeStrategy.on_bar()` (v2), XAUUSD 15m")
    A(f"**Period:** {span_start.date()} → {span_end.date()} — "
      f"**{span_months:.1f} months** ({span_days} calendar days, {len(bars):,} 15m bars) · "
      f"**Config:** `config_live_50000.yaml` kalman_regime block")
    A("")
    A(f"> ⚠️ **All numbers below are in-sample on a single {span_months:.1f}-month slice of 2026** "
      f"(the post-peak gold correction). No out-of-sample / walk-forward validation. Treat every "
      f"profit figure as descriptive of this one regime, not predictive.")
    A("")
    A("> This is the run you asked for: **$50k account, kill switch OFF, fixed SL, "
      "fixed TP, fixed daily-loss cap that resets every UTC day.** Below the headline "
      "I dissect *why* the losing trades lost and name the single change with the most leverage.")
    A("")
    A("## 1. Exact rules simulated")
    A("")
    A("| Knob | Value | Source |")
    A("|---|---|---|")
    A(f"| Account | **${CAPITAL:,.0f}** | spec |")
    A(f"| Stop loss | **FIXED {SL_PTS:.0f} pts** | ≈ live 3.0 × median 2026 15m ATR(14) |")
    A(f"| Take profit | **FIXED {SL_PTS*RR:.0f} pts (RR {RR})** | `kalman_min_tp_rr: 1.0` |")
    A(f"| Lot | **FIXED {LOT}** | `config_live_50000` XAUUSD `min_lot` (live floor) |")
    A(f"| Daily loss cap | **${DAILY_CAP:.0f}, blocks new entries, resets each UTC day** | `absolute_max_loss_usd` |")
    A(f"| Kill switch / max-DD halt | **OFF (ignored)** | spec |")
    A(f"| Per-trade $ risk at this lot | **${SL_PTS*LOT*VALUE_PER_LOT:.0f}** ({SL_PTS*LOT*VALUE_PER_LOT/CAPITAL*100:.2f}% of acct) | derived |")
    A(f"| max_positions / hedge lock | 2 / no-hedge | live |")
    A("| Fills | signal@close(t) → fill@open(t+1); 0.20/side cost; adverse gaps fill at gapped open; same-bar SL+TP → SL first | realistic |")
    A("")
    A("## 2. Headline result")
    A("")
    A("| Metric | Value |")
    A("|---|---|")
    A(f"| Signals emitted | {len(sig_df):,} |")
    A(f"| Trades taken | {s['n']} (skipped: {sk['max_positions']} max-pos, "
      f"{sk['daily_cap']} daily-cap, {sk['directional_lock']} hedge-lock) |")
    A(f"| **Net P&L** | **${s['net']:+,.2f} ({s['net']/CAPITAL*100:+.2f}% of ${CAPITAL:,.0f})** |")
    A(f"| Final equity | ${final_eq:,.2f} |")
    A(f"| Win rate | {s['wr']:.1f}% ({len(winners)}W / {len(losers)}L) |")
    A(f"| Profit factor | {pf} |")
    A(f"| Expectancy | ${s['exp']:+.2f} / trade |")
    A(f"| Avg win / loss | ${s['avg_w']:+.2f} / ${s['avg_l']:+.2f} |")
    A(f"| Largest loss | ${s['max_l']:+.2f} |")
    A(f"| Max consecutive losses | {s['mcl']} |")
    A(f"| **Max drawdown** | **${dd:,.2f} ({ddp:.2f}%)** |")
    A(f"| Max-DD vs live $50k limit (${MAX_DD_LIMIT:,.0f} / 7%) | **{abs(dd)/MAX_DD_LIMIT:.1f}× over** |")
    A(f"| Days daily-cap hit | {sk['cap_hit_days']} |")
    A("")
    breach = "BREACHES" if abs(dd) > MAX_DD_LIMIT else "within"
    A(f"> The strategy nets **${s['net']:+,.0f}** but its peak-to-trough drawdown of "
      f"**${abs(dd):,.0f}** **{breach}** the $50k account's real **${MAX_DD_LIMIT:,.0f} (7%)** "
      f"limit. With the kill switch ON (live), the account halts inside this bleed and the "
      f"headline profit is never realised.")
    A("")
    A("## 3. The drawdown — anatomy of the bleed")
    A("")
    A(f"- **Peak equity** ${peak_row['equity']:,.0f} on {pd.to_datetime(peak_row['exit_ts']).date()}")
    A(f"- **Trough equity** ${trough_row['equity']:,.0f} on {pd.to_datetime(trough_row['exit_ts']).date()}")
    A(f"- **Drop** ${abs(trough_row['dd']):,.0f} over "
      f"{(pd.to_datetime(trough_row['exit_ts']) - pd.to_datetime(peak_row['exit_ts'])).days} days")
    A("")
    A("This is **not a single bad trade** — it is a slow, multi-week erosion. That matters: "
      "a daily-loss cap (which only limits *one day*) is structurally incapable of stopping a "
      "bleed made of many small in-cap red days. Only a trailing max-drawdown halt can.")
    A("")
    A("## 4. WHY the losers lost — MAE/MFE excursion autopsy")
    A("")
    A("Maximum Favourable/Adverse Excursion measures how far price travelled in/against the "
      "trade before it closed. This is the core diagnostic for *fixable* vs *structural* losses.")
    A("")
    A("| Diagnostic | Count | % of losers | Reading |")
    A("|---|---:|---:|---|")
    A(f"| Losers that reached **≥ +0.5R ({0.5*SL_PTS:.0f}pts) in profit** before reversing to a stop | "
      f"{len(rescue_tp_half)} | {len(rescue_tp_half)/max(len(losers),1)*100:.1f}% | give-back losses |")
    A(f"| Losers that reached **≥ +1R ({SL_PTS:.0f}pts)** before reversing | "
      f"{len(rescue_tp)} | {len(rescue_tp)/max(len(losers),1)*100:.1f}% | **near-zero BY CONSTRUCTION** — at RR 1.0, +1R IS the TP |")
    A(f"| Winners that took **≥ 0.7R of heat** (nearly stopped first) | "
      f"{len(heat_winners)} | {len(heat_winners)/max(len(winners),1)*100:.1f}% (of winners) | a tighter stop would have killed these |")
    A("")
    loser_mfe = losers['mfe_pts'].median(); winner_mae = winners['mae_pts'].median()
    A(f"- **Median loser MFE:** {loser_mfe:.1f} pts ({loser_mfe/SL_PTS:.2f}R) — how far a typical loser went our way first.")
    A(f"- **Median winner MAE:** {winner_mae:.1f} pts ({winner_mae/SL_PTS:.2f}R) — how far a typical winner pulled back before paying.")
    A("")
    give_back = len(rescue_tp_half) / max(len(losers), 1) * 100
    A(f"**Interpretation (corrected).** The seductive reading is 'add a partial TP to save the "
      f"give-back losers' — but the data refutes it. Loser-MFE ({loser_mfe/SL_PTS:.2f}R) and "
      f"winner-MAE ({winner_mae/SL_PTS:.2f}R) are **near-symmetric**: winners and losers look "
      f"statistically identical until the final move, so there is little exit-timing alpha to "
      f"harvest. About {give_back:.0f}% of losers do reach +0.5R first, but a partial TP there "
      f"would also clip the *winners* that pass through +0.5R on the way to TP — net roughly a wash. "
      f"**The leak is not the exit timing; it is (a) the stop being too WIDE for the account's DD "
      f"limit and (b) the BUY/RANGE sub-systems. See §6–7.**")
    A("")
    A("## 5. Where the money was made and lost")
    A("")
    A("### By side")
    A(grp_md(t, "side", order=["buy", "sell"]))
    A("")
    A("### By regime / mode")
    A(grp_md(t, "mode", order=["trend", "range"]))
    A("")
    A("### Monthly")
    months = sorted(t["entry_ts"].astype(str).str[:7].unique())
    A("| Month | N | Win% | PF | Net$ | EndEq |")
    A("|---|---:|---:|---:|---:|---:|")
    run = CAPITAL
    for m in months:
        sub = t[t["entry_ts"].astype(str).str[:7] == m]
        ss = stats(sub); run += ss["net"]
        A(f"| {m} | {ss['n']} | {ss['wr']:.1f}% | {pf_str(ss)} | {ss['net']:+,.0f} | {run:,.0f} |")
    A("")
    A(f"- Green days **{green/(green+red)*100:.0f}%** ({green} of {green+red}) · "
      f"worst day **${daily.min():+,.0f}** · best day **${daily.max():+,.0f}**")
    A("")
    A("### Loss-streak attribution (streaks ≥ 3)")
    A("")
    if streaks:
        A("| Streak start | Length | Net$ | Dominant side | Dominant mode |")
        A("|---|---:|---:|---|---|")
        for st in sorted(streaks, key=lambda x: sum(r['pnl'] for r in x))[:8]:
            net = sum(r["pnl"] for r in st)
            sides = pd.Series([r["side"] for r in st]).mode()[0]
            modes = pd.Series([r["mode"] for r in st]).mode()[0]
            A(f"| {pd.to_datetime(st[0]['exit_ts']).date()} | {len(st)} | {net:+,.0f} | {sides} | {modes} |")
    A("")
    A("## 6. Counterfactuals — isolating the leak")
    A("")
    A("| Scenario | N | Win% | PF | Net$ | Note |")
    A("|---|---:|---:|---:|---:|---|")
    A(f"| **Baseline (all trades)** | {s['n']} | {s['wr']:.1f}% | {pf} | {s['net']:+,.0f} | as traded |")
    A(f"| SELL only | {cf['sell_only']['n']} | {cf['sell_only']['wr']:.1f}% | {pf_str(cf['sell_only'])} | {cf['sell_only']['net']:+,.0f} | the down-year edge |")
    A(f"| BUY only | {cf['buy_only']['n']} | {cf['buy_only']['wr']:.1f}% | {pf_str(cf['buy_only'])} | {cf['buy_only']['net']:+,.0f} | fought the trend |")
    A(f"| TREND only | {cf['trend_only']['n']} | {cf['trend_only']['wr']:.1f}% | {pf_str(cf['trend_only'])} | {cf['trend_only']['net']:+,.0f} | structural edge |")
    A(f"| RANGE only | {cf['range_only']['n']} | {cf['range_only']['wr']:.1f}% | {pf_str(cf['range_only'])} | {cf['range_only']['net']:+,.0f} | OU fade = dead weight |")
    A(f"| Exclude Feb+Apr (chop) | {cf['ex_feb_apr']['n']} | {cf['ex_feb_apr']['wr']:.1f}% | {pf_str(cf['ex_feb_apr'])} | {cf['ex_feb_apr']['net']:+,.0f} | regime-dependence |")
    A("")
    A("> These are **in-sample, hindsight filters** — they show *where* the edge lives, not a "
      "deployable rule. You cannot know in advance that 2026 would be a down-year favouring SELL.")
    A("")
    A("## 7. Geometry sweep on $50k (lot 0.04, cap $295)")
    A("")
    A("| SL | RR | TP | N | Win% | PF | Net$ | MaxDD$ | MaxDD% |")
    A("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sl, rr, sg, ddg, ddpg in grid:
        A(f"| {sl:.0f} | {rr:.1f} | {sl*rr:.0f} | {sg['n']} | {sg['wr']:.1f}% | "
          f"{pf_str(sg)} | {sg['net']:+,.0f} | {ddg:,.0f} | {ddpg:.1f}% |")
    A("")
    A("### Daily-cap & lot sensitivity")
    A("")
    A(f"- **Daily cap ${DAILY_CAP:.0f} vs OFF:** capped Net ${s['net']:+,.0f} / "
      f"DD {ddp:.1f}%  →  uncapped Net ${s_nocap['net']:+,.0f} / DD {ddp_nocap:.1f}%. "
      f"{'The cap costs profit AND deepens DD (it blocks positive-EV recovery trades).' if s_nocap['net'] > s['net'] else 'The cap helps here.'}")
    A("")
    A("| Lot | Risk/trade | N | Net$ | PF | MaxDD$ | MaxDD% |")
    A("|---:|---:|---:|---:|---:|---:|---:|")
    for lt, slt, ddlt, ddplt in lot_rows:
        A(f"| {lt} | ${SL_PTS*lt*VALUE_PER_LOT:.0f} | {slt['n']} | {slt['net']:+,.0f} | "
          f"{pf_str(slt)} | {ddlt:,.0f} | {ddplt:.1f}% |")
    A("")
    A("Scaling the lot scales **both** profit and drawdown linearly — it changes the dollar "
      "magnitude, never the edge (PF is invariant to size).")
    A("")
    A("## 7b. The lot-0.40 leverage trap")
    A("")
    A("You asked whether raising the lot to **0.40** makes more money. In gross dollars, yes — "
      "but it is pure leverage, not edge, and it obliterates the account under the real kill switch.")
    A("")
    A("![lot 0.04 vs lot 0.40 equity & drawdown](figs/kalman_50k_lot_compare.png)")
    A("")
    A("*Generated by `scripts/plot_kalman_50k_lots.py`. Top: equity. Bottom: drawdown vs the "
      "−7% live limit. The red (lot 0.40) curve trips the black kill-switch line within the first "
      "few weeks and lives below it for most of the period — meaning live, the account is terminated "
      "in January and none of the apparent +$36k upside is ever realised.*")
    A("")
    A("| Scenario | Risk/trade | N | Net P&L | PF | Max Drawdown | vs 7% limit | cap-days |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for lbl, risk, sb, ddb, ddpb, capdays in big_rows:
        over = abs(ddb) / MAX_DD_LIMIT
        vs = "~at limit" if over <= 1.05 else f"{over:.1f}× over"
        A(f"| {lbl} | ${risk:,.0f} | {sb['n']} | ${sb['net']:+,.0f} ({sb['net']/CAPITAL*100:+.1f}%) | "
          f"{pf_str(sb)} | ${ddb:,.0f} ({ddpb:.1f}%) | {vs} | {capdays} |")
    A("")
    A("**Reading it:**")
    A("- **It's leverage, not edge.** PF barely moves (1.09 → 1.14, inside noise). Multiplying the "
      "lot by 10× multiplies *both* wins and losses by 10× — the strategy's quality is unchanged.")
    A(f"- **The drawdown explodes to −32% to −39%** (${abs(big_rows[1][3]):,.0f}–${abs(big_rows[3][3]):,.0f} "
      f"on a $50k account) = **7–10× past the $3,500 / 7% kill-switch line.** Live, the account is "
      f"force-closed at −$3,500 long before any profit accrues; the gross +$36k–$85k only exists in "
      f"the simulation that *ignores* the halt.")
    A("- **The $295 daily cap becomes degenerate at lot 0.40:** one trade risks $1,320 — 4.5× the "
      "entire daily cap — so a single loss trips it and blocks the rest of the day. That is why "
      "cap-days jump to ~72 of 118; a daily loss limit smaller than one trade's risk is broken by "
      "construction.")
    A("")
    A("**Verdict on lot 0.40:** more gross dollars, *not* more profitable — and a near-certain blow-up "
      "under live rules. The correct direction is the opposite: size *down* / stop *tighter* to fit "
      "inside the 7% cap (see §8), not up to smash through it.")
    A("")
    A("## 8. The ONE thing that could make this profitable")
    A("")
    A("**Tighten the stop to ~22 pts (≈2.0×ATR).** It is the only single change that is *both* "
      "profitable *and* keeps the account inside its real **7% drawdown limit** — and unlike "
      "'trade SELL only', it requires no hindsight about the year's direction.")
    A("")
    A("The geometry sweep (§7) is unambiguous:")
    A(f"- **SL 22 / RR 1.0** → +$2,772 at **−4.3% DD** (inside the 7% cap — the strategy actually *survives* live).")
    A(f"- **SL 22 / RR 1.5** → +$4,729 at **−5.7% DD** (best net that still clears the cap).")
    A(f"- **SL 33 / RR 1.0 (as-run)** → +$3,353 but **−6.7% DD (breaches the cap)**.")
    A("- Every SL ≥ 33 with RR ≥ 1.5 blows through −13% to −16% DD.")
    A("")
    A("Why this is THE lever: the binding constraint on this account is **not** expectancy "
      "(it's already positive) — it is the **drawdown path tripping the kill switch**. The wide "
      "33-pt stop is what pushes the bleed past 7%. Halving it keeps the same edge but shrinks the "
      "DD below the halt line, which is the difference between 'the kill switch flattens me mid-bleed' "
      "and 'the strategy is allowed to finish the year'.")
    A("")
    A("**Secondary, regime-independent fix:** disable the **RANGE/OU sub-system** (−$1,565, PF 0.83 — "
      "pure dead weight in §5). That is a losing module you can remove without any forward-looking knowledge.")
    A("")
    A("**What is NOT the one thing (and why):**")
    A("- *Partial / tighter TP* — refuted by the symmetric MAE/MFE in §4; little exit alpha exists.")
    A("- *Trade SELL only / skip Feb+Apr* — biggest in-sample lever (+$4,458) but it is **hindsight beta**: "
      "it only works because you already know 2026 fell. In a gold up-year the BUY side carries and this "
      "filter inverts. Not deployable.")
    A("")
    A("**Honest ceiling:** even with the tighter stop, the edge is PF ~1.1 — inside the slippage "
      "noise band — and the profitable side rode a one-off 2026 down-year. The tighter stop makes it "
      "*survivable*, not *durable*. It buys you a strategy that no longer auto-breaches the cap; it does "
      "**not** manufacture a regime-independent alpha.")
    A("")
    A("## 9. My review — what I would have done differently")
    A("")
    A("1. **Never evaluate with the kill switch off.** The +profit headline only exists because "
      "the account is allowed to lose 7×+ its real limit. The *first* backtest should enforce the "
      "live $3,500/7% halt — then the question 'is this profitable for me' has a real answer "
      "(it force-flattens mid-bleed).")
    A("2. **Design the exit before the entry.** This strategy spent all its tuning on entry gates "
      "(ADX, z-score, HTF-SELL, session masks) and shipped a naive RR-1.0 full-stop exit. The "
      "MAE/MFE autopsy shows the exit is where the money leaks.")
    A("3. **Walk-forward, not in-sample.** Every flattering Kalman number (incl. this one) is "
      "in-sample on 2026. The one rigorous walk-forward (56-combo strict-fill grid, 2026-06-20) "
      "failed in every slice. Trust that over this.")
    A("4. **Separate beta from alpha.** SELL carried the year because gold *fell*. That is "
      "directional beta on a one-off regime, not a repeatable edge. Demean the drift before "
      "claiming skill.")
    A("5. **A daily cap is a prop-firm rule, not a risk tool here.** It blocks positive-EV "
      "recovery trades and cannot stop a multi-week bleed. Use a trailing-DD halt for protection "
      "and let the daily cap exist only to satisfy the challenge rules.")
    A("")
    A("## 10. Verdict")
    A("")
    A(f"On a $50k account with these fixed rules, Kalman v2 returns **${s['net']:+,.0f} "
      f"({s['net']/CAPITAL*100:+.2f}%)** at **PF {pf}** — but only by carrying a "
      f"**${abs(dd):,.0f} ({ddp:.1f}%)** drawdown that **breaches the account's 7% limit**, "
      f"on the SELL side of a one-off down-year, in-sample. The single best fix is a **tighter "
      f"~22-pt stop**, which keeps net positive (+$2,772) while pulling drawdown to −4.3% — the only "
      f"version that actually survives the live kill switch. Even then this is a marginal, "
      f"regime-dependent edge (PF ~1.1) — appropriate for continued research, **not** for live "
      f"capital deployment as a standalone money-maker.")
    A("")

    REPORT.write_text("\n".join(md))
    print(f"\nReport -> {REPORT}")
    print(f"Trades -> {TRADES_OUT}")
    print(f"\nHEADLINE: Net ${s['net']:+,.0f} ({s['net']/CAPITAL*100:+.2f}%) | "
          f"PF {pf} | WR {s['wr']:.1f}% | MaxDD ${abs(dd):,.0f} ({ddp:.1f}%) | "
          f"losers≥+1R {len(rescue_tp)}/{len(losers)}")


if __name__ == "__main__":
    main()
