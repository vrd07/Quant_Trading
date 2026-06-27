# New Strategy Backtests — #10–13 (2026-06-27)

Production-engine backtests (`run_backtest.py --timeframe 15m`) for the four most
recently added strategies, each on its in-code-gated live symbol. Risk-bypassed
(strategy-native SL/TP), 2.5y Dukascopy history. Run SHA `19c8543`.

> ⚠️ These are research/production-engine results graded against the `backtest.md`
> §1 gates. All four FAIL G4 (Sharpe < 1) — expected for low-frequency / wide-stop
> edges — and the calendar strategies fail G1 (daily win-rate) by construction
> (one trade per week). They ship as **diversifiers**, not as stand-alone gate-passers.

## Summary

| # | Strategy | Symbol | Trades | Win% | PF | Return | Max DD | Report |
|---|----------|--------|-------:|-----:|---:|-------:|-------:|--------|
| 10 | squeeze_breakout | XAUUSD | 283 | 36.7% | **1.50** | +27.27% | −5.76% | [details](backtest_squeeze_breakout_2026-06-27/per_strategy/squeeze_breakout.md) |
| 11 | stoch_pullback | XAUUSD | 658 | 33.4% | **1.22** | +20.34% | −7.30% | [details](backtest_stoch_pullback_2026-06-27/per_strategy/stoch_pullback.md) |
| 12 | index_overnight | US30 | 123 | 58.5% | **1.78** | +0.30% | −0.06% | [details](backtest_index_overnight_2026-06-27/per_strategy/index_overnight.md) |
| 13 | wednesday_drift | AUDJPY | 126 | 60.3% | **1.67** | +0.42% | −0.14% | [details](backtest_wednesday_drift_2026-06-27/per_strategy/wednesday_drift.md) |

> Returns for #12/#13 are small because the production engine sizes them with
> placeholder index-CFD / cross specs at $25k capital and they trade only once per
> week — read them on PF and max-DD, not absolute $.

---

## #10 — SqueezeBreakout (XAUUSD, 15m)

Volatility-coil → expansion breakout, fixed 33-pt stop, RR 2.0, HTF EMA-trend gate.

![squeeze_breakout equity](backtest_squeeze_breakout_2026-06-27/equity_curves.png)

PF **1.50**, +27.27%, max DD −5.76% over 283 trades (36.7% win-rate; the edge is
the 2:1 payoff on a low hit-rate). Gates: G3/G5/G6 pass, G1/G2/G4 fail.

## #11 — StochPullback (XAUUSD, 15m)

EMA(50) trend-continuation + Stochastic cool-off pullback, structural stop, RR 2.0,
London→NY session filter.

![stoch_pullback equity](backtest_stoch_pullback_2026-06-27/equity_curves.png)

PF **1.22**, +20.34%, max DD −7.30% over 658 trades. The structurally weakest gold
strategy — shipped as a loosely-correlated diversifier.

## #12 — IndexOvernight (US30, 15m)

"Turnaround Tuesday" equity-index overnight drift — long Tue cash-close → Wed
cash-open, one trade/week, wide catastrophe stop, time exit.

![index_overnight equity](backtest_index_overnight_2026-06-27/equity_curves.png)

PF **1.78**, win-rate 58.5%, max DD −0.06% over 123 trades. First gold-uncorrelated
edge; survives `--enforce-risk` perfectly (kill switch never trips).

## #13 — WednesdayDrift (AUDJPY, 15m)

Mid-week JPY-weakness / risk-on carry drift — long Tue session-close → Wed
session-close, one trade/week, wide stop, time exit.

![wednesday_drift equity](backtest_wednesday_drift_2026-06-27/equity_curves.png)

PF **1.67**, win-rate 60.3%, max DD −0.14% over 126 trades. Most diversifying
driver in the book (carry/JPY/risk — uncorrelated to gold and equities).
