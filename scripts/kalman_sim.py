#!/usr/bin/env python3
"""
Interactive Kalman backtest SIMULATOR (Plotly Dash, browser).

Watch the Kalman v2 2026 backtest play out trade-by-trade on a candle chart and
tweak parameters live — every knob re-runs instantly because the slow part (the
real KalmanRegimeStrategy.on_bar() replay) is already cached to CSV.

    python scripts/kalman_sim.py            # serve at http://127.0.0.1:8050
    python scripts/kalman_sim.py --port 8060

TWO TIERS OF PARAMETERS, both instant:
  * Exit / sizing  -> fed straight into backtest_kalman_2026_fixed.simulate():
        SL (points), R:R (-> TP = SL x RR), lot, cost/side, breakeven/lock on,
        daily $ cap, max positions, directional (no-hedge) lock.
  * Setup / selectivity -> a pre-filter on the cached signals (their metadata is
        stored, so no re-run): enable trend-buy / trend-sell / range-buy /
        range-sell independently, and raise the min strength / min ADX gate.

The signal cache is FIXED (config_live_5000 strategy params: ADX>17, OU z 2.0,
RSI gates, session mask). Changing the strategy's internal thresholds themselves
still needs `python scripts/backtest_kalman_2026_fixed.py --refresh-signals`.

Transport: Play / Pause / Step / Reset, a speed selector, |< >| jump-to-trade,
and a scrubber slider. The candle panel scrolls a window up to "now"; the equity
panel builds up against a fixed full-period axis. Stats update live as of the
current bar AND show the full-run result for the current parameters.
"""
import sys
import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Reuse the real, audited engine: load_15m_2026 / simulate / stats / max_drawdown.
_spec = importlib.util.spec_from_file_location(
    "bt", ROOT / "scripts/backtest_kalman_2026_fixed.py")
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)

from src.data.indicators import Indicators

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, dcc, html, Input, Output, State, ctx, no_update

# ---------------------------------------------------------------------------
# Constants / theme
# ---------------------------------------------------------------------------
INITIAL = bt.INITIAL_CAPITAL          # 5000.0
SIG_CACHE = bt.SIG_CACHE
TRADES_CSV = bt.TRADES_OUT

GREEN, RED, BLUE, AMBER = "#26a69a", "#ef5350", "#42a5f5", "#ffb300"
GREY, BG, PANEL = "#8a93a3", "#0e1117", "#161b22"
TREND_C, RANGE_C = "rgba(38,166,154,0.07)", "rgba(255,179,0,0.08)"

INTERVAL_MS = 150
SPEEDS = [1, 2, 4, 8, 16, 32, 64]     # bars advanced per tick / per Step
DEFAULT_WINDOW = 250                  # candles shown in the scrolling window

SETUP_DEFS = [("tb", "trend", "BUY"), ("ts", "trend", "SELL"),
              ("rb", "range", "BUY"), ("rs", "range", "SELL")]
SETUP_LABELS = {"tb": "Trend BUY", "ts": "Trend SELL",
                "rb": "Range BUY", "rs": "Range SELL"}

# ---------------------------------------------------------------------------
# Load data ONCE (read-only module globals — single-user localhost app)
# ---------------------------------------------------------------------------
print("loading bars + signals + indicators ...")
BARS = bt.load_15m_2026()
N = len(BARS)
CLOSE = BARS["close"]
KALMAN = Indicators.kalman_filter(CLOSE, q=1e-5, r=0.01).to_numpy(float)
REGIME = Indicators.rv_regime(CLOSE, rv_window=20, rv_ma_window=100)\
    .reindex(BARS.index).fillna(0).to_numpy(float)   # 1=trend 0=range
IDX = BARS.index
IDX_NP = IDX.to_numpy()                # for fast plotting slices

SIG_DF = pd.read_csv(SIG_CACHE, parse_dates=["signal_ts"])
SIG_DF["side_u"] = SIG_DF["side"].str.upper()
STR_LO = float(np.floor(SIG_DF.strength.min() * 100) / 100)
print(f"  {N} bars, {len(SIG_DF)} cached signals  ({IDX.min()} -> {IDX.max()})")

_SIM_CACHE: dict[str, dict] = {}       # param-signature -> sim result


# ---------------------------------------------------------------------------
# Simulation (filter cached signals -> bt.simulate -> equity/stats), memoized
# ---------------------------------------------------------------------------
def _key(p: dict) -> str:
    return "|".join(str(p[k]) for k in (
        "sl", "rr", "lot", "cost", "be", "cap", "maxpos", "dirlock",
        "setups", "minstr", "minadx"))


def _filter_signals(p: dict) -> pd.DataFrame:
    s = SIG_DF
    mask = pd.Series(False, index=s.index)
    for tag, mode, side in SETUP_DEFS:
        if tag in p["setups"]:
            mask |= (s["mode"] == mode) & (s["side_u"] == side)
    s = s[mask & (s["strength"] >= p["minstr"]) & (s["adx"] >= p["minadx"])]
    return s


def run_sim(p: dict) -> str:
    """Return a cache key; compute + memoize the result the first time."""
    k = _key(p)
    if k in _SIM_CACHE:
        return k
    sigf = _filter_signals(p)
    cap = p["cap"] if p["cap"] and p["cap"] > 0 else 1e9
    trades, skipped = bt.simulate(
        BARS, sigf, sl_pts=p["sl"], rr=p["rr"], lot=p["lot"], cost=p["cost"],
        be_enabled=p["be"], daily_cap=cap, max_positions=int(p["maxpos"]),
        directional_lock=p["dirlock"])

    if len(trades) == 0:
        _SIM_CACHE[k] = dict(empty=True, n_signals=len(sigf))
        return k

    trades = trades.sort_values("exit_ts").reset_index(drop=True)
    trades["entry_pos"] = IDX.get_indexer(trades["entry_ts"], method="nearest")
    trades["exit_pos"] = IDX.get_indexer(trades["exit_ts"], method="nearest")
    eq_val = INITIAL + trades["pnl"].cumsum()
    st = bt.stats(trades)
    dd_abs, dd_pct = bt.max_drawdown(trades, INITIAL)

    _SIM_CACHE[k] = dict(
        empty=False, trades=trades, n_signals=len(sigf),
        eq_pos=np.concatenate([[0], trades["exit_pos"].to_numpy()]),
        eq_val=np.concatenate([[INITIAL], eq_val.to_numpy()]),
        eq_lo=float(min(INITIAL, eq_val.min())) - 120,
        eq_hi=float(max(INITIAL, eq_val.max())) + 120,
        entry_sorted=np.sort(trades["entry_pos"].to_numpy()),
        stats=st, dd_abs=dd_abs, dd_pct=dd_pct, skipped=dict(skipped))
    return k


# ---------------------------------------------------------------------------
# Figure builder (pure view: slice cached sim at the current bar `cur`)
# ---------------------------------------------------------------------------
def build_figure(cur: int, key: str, window: int):
    cur = int(max(1, min(cur, N - 1)))
    lo = max(0, cur - int(window))
    sim = _SIM_CACHE.get(key)

    fig = make_subplots(
        rows=2, cols=1, row_heights=[0.68, 0.32], vertical_spacing=0.06,
        subplot_titles=("XAUUSD 15m — price · Kalman line · trades (scrolling)",
                        "Equity ($) — building up over 2026 YTD"))

    xw = IDX_NP[lo:cur + 1]
    ow = BARS["open"].to_numpy()[lo:cur + 1]
    hw = BARS["high"].to_numpy()[lo:cur + 1]
    lw = BARS["low"].to_numpy()[lo:cur + 1]
    cw = BARS["close"].to_numpy()[lo:cur + 1]

    # regime background shading (merge consecutive same-regime bars into spans)
    rg = REGIME[lo:cur + 1]
    start = 0
    for i in range(1, len(rg) + 1):
        if i == len(rg) or rg[i] != rg[start]:
            fig.add_vrect(x0=xw[start], x1=xw[min(i, len(xw) - 1)],
                          fillcolor=(TREND_C if rg[start] == 1 else RANGE_C),
                          line_width=0, layer="below", row=1, col=1)
            start = i

    fig.add_trace(go.Candlestick(
        x=xw, open=ow, high=hw, low=lw, close=cw, name="XAUUSD",
        increasing_line_color=GREEN, decreasing_line_color=RED,
        increasing_fillcolor=GREEN, decreasing_fillcolor=RED,
        line_width=1, whiskerwidth=0.4, showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=xw, y=KALMAN[lo:cur + 1], mode="lines", name="Kalman",
        line=dict(color=BLUE, width=2)), row=1, col=1)

    cur_ts = IDX[cur]
    win_ts = IDX[lo]

    if sim and not sim.get("empty"):
        t = sim["trades"]
        vis = t[(t["entry_pos"] <= cur) & (t["exit_pos"] >= lo)]
        be_x, be_y, se_x, se_y = [], [], [], []     # buy/sell entry markers
        wx_x, wx_y, lx_x, lx_y = [], [], [], []      # win/loss exit markers
        ox_x, ox_y = [], []                          # still-open entry markers
        for _, r in vis.iterrows():
            closed = r["exit_pos"] <= cur
            x_end = IDX[min(int(r["exit_pos"]), cur)]
            x_start = IDX[max(int(r["entry_pos"]), lo)]
            # SL / TP brackets, live up to "now"
            for lvl, col in ((r["sl0"], RED), (r["tp"], GREEN)):
                fig.add_shape(type="line", x0=x_start, x1=x_end, y0=lvl, y1=lvl,
                              line=dict(color=col, width=1, dash="dot"),
                              opacity=0.55, row=1, col=1)
            # entry marker (only if the entry bar is inside the visible window)
            if r["entry_pos"] >= lo:
                if not closed:
                    ox_x.append(r["entry_ts"]); ox_y.append(r["entry"])
                elif r["side"] == "buy":
                    be_x.append(r["entry_ts"]); be_y.append(r["entry"])
                else:
                    se_x.append(r["entry_ts"]); se_y.append(r["entry"])
            # exit marker (only once closed and inside the window)
            if closed and r["exit_pos"] >= lo:
                if r["pnl"] > 0:
                    wx_x.append(r["exit_ts"]); wx_y.append(r["exit"])
                else:
                    lx_x.append(r["exit_ts"]); lx_y.append(r["exit"])

        def mk(x, y, sym, color, name, size=12):
            if not x:
                return
            fig.add_trace(go.Scatter(
                x=x, y=y, mode="markers", name=name,
                marker=dict(symbol=sym, size=size, color=color,
                            line=dict(width=1, color="#0b0e13"))),
                row=1, col=1)

        mk(be_x, be_y, "triangle-up", GREEN, "entry BUY (closed)")
        mk(se_x, se_y, "triangle-down", RED, "entry SELL (closed)")
        mk(ox_x, ox_y, "circle-open", BLUE, "entry (open)", size=11)
        mk(wx_x, wx_y, "circle", GREEN, "exit win", size=8)
        mk(lx_x, lx_y, "x", RED, "exit loss", size=8)

        # equity built up to now
        ep = sim["eq_pos"]
        m = ep <= cur
        fig.add_trace(go.Scatter(
            x=IDX_NP[ep[m]], y=sim["eq_val"][m], mode="lines",
            line=dict(color=BLUE, width=1.8), showlegend=False,
            fill="tozeroy", fillcolor="rgba(66,165,245,0.10)"), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=[IDX_NP[ep[m]][-1]], y=[sim["eq_val"][m][-1]], mode="markers",
            marker=dict(color=BLUE, size=7), showlegend=False), row=2, col=1)
        fig.update_yaxes(range=[sim["eq_lo"], sim["eq_hi"]], row=2, col=1)

    # reference lines
    fig.add_hline(y=INITIAL, line=dict(color=GREY, width=1, dash="dash"),
                  row=2, col=1)
    fig.add_vline(x=cur_ts, line=dict(color=GREY, width=1, dash="dot"),
                  row=1, col=1)

    fig.update_xaxes(range=[win_ts, cur_ts],
                     rangebreaks=[dict(bounds=["sat", "mon"])],
                     rangeslider_visible=False, row=1, col=1)
    fig.update_xaxes(range=[IDX[0], IDX[-1]], row=2, col=1)
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=PANEL,
        margin=dict(l=58, r=14, t=38, b=26), height=700, autosize=True,
        legend=dict(orientation="h", y=1.07, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        font=dict(size=11), uirevision="static", dragmode="pan",
        hovermode="x unified")
    fig.update_annotations(font=dict(size=11, color=GREY))   # subplot titles
    return fig


def stats_children(cur: int, key: str):
    cur = int(max(1, min(cur, N - 1)))
    sim = _SIM_CACHE.get(key)
    head = f"📅 {pd.Timestamp(IDX[cur]).strftime('%Y-%m-%d %H:%M')} UTC"
    if not sim:
        return [html.Div(head)]
    if sim.get("empty"):
        return [html.Div(head),
                html.Div(f"⚠ no trades — {sim['n_signals']} signals passed the "
                         f"filters but produced 0 fills", style={"color": AMBER})]

    t = sim["trades"]
    done = t[t["exit_pos"] <= cur]
    n = len(done)
    wr = 100 * (done["pnl"] > 0).mean() if n else 0.0
    net_now = done["pnl"].sum()
    eq_now = INITIAL + net_now
    peak = (INITIAL + done["pnl"].cumsum()).cummax().iloc[-1] if n else INITIAL
    dd_now = eq_now - peak
    open_now = int(((t["entry_pos"] <= cur) & (t["exit_pos"] > cur)).sum())

    s, sk = sim["stats"], sim["skipped"]
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    full_pf_col = GREEN if s["pf"] >= 1 else RED
    net_col = GREEN if net_now >= 0 else RED

    def row(label, value, color=None):
        return html.Div(className="ks-row", children=[
            html.Span(label, className="k"),
            html.Span(value, className="v",
                      style={"color": color} if color else None)])

    return [
        html.Div(head, style={"fontWeight": 700, "marginBottom": "6px",
                              "fontSize": "13px"}),
        html.Div("AS OF NOW", className="ks-mini"),
        row("equity", f"${eq_now:,.0f}  ({100*(eq_now-INITIAL)/INITIAL:+.1f}%)",
            net_col),
        row("trades closed", f"{n} / {len(t)}"),
        row("open now", f"{open_now}"),
        row("win rate", f"{wr:.0f}%"),
        row("drawdown", f"${dd_now:,.0f}", RED if dd_now < 0 else None),
        html.Hr(className="ks-hr"),
        html.Div("FULL RUN · THESE PARAMS", className="ks-mini"),
        row("net P&L", f"${s['net']:+,.0f}", GREEN if s["net"] >= 0 else RED),
        row("profit factor", pf, full_pf_col),
        row("win rate", f"{s['wr']:.0f}%  ({s['n']} trades)"),
        row("expectancy", f"${s['exp']:+.2f}/trade"),
        row("max drawdown", f"${sim['dd_abs']:,.0f} ({sim['dd_pct']:.1f}%)", RED),
        row("max consec loss", f"{s['mcl']}"),
        html.Hr(className="ks-hr"),
        html.Div("SKIPPED SIGNALS", className="ks-mini"),
        row("signals used", f"{sim['n_signals']}"),
        row("→ max positions", f"{sk.get('max_positions', 0)}"),
        row("→ directional lock", f"{sk.get('directional_lock', 0)}"),
        row("→ daily cap", f"{sk.get('daily_cap', 0)}"),
        row("cap-hit days", f"{sk.get('cap_hit_days', 0)}"),
    ]


# ---------------------------------------------------------------------------
# Dash app + layout
# ---------------------------------------------------------------------------
app = Dash(__name__, title="Kalman Backtest Simulator",
           update_title=None, meta_tags=[
               {"name": "viewport",
                "content": "width=device-width, initial-scale=1"}])

app.index_string = """<!DOCTYPE html>
<html><head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #0e1117; }
.ks-app { color:#e6edf3; min-height:100vh; max-width:1720px; margin:0 auto;
  font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif; padding:14px 18px; }
.ks-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
.ks-title { font-size:21px; font-weight:800; letter-spacing:.2px; }
.ks-sub { color:#8a93a3; font-size:13px; }
.ks-transport { display:flex; align-items:center; gap:6px; flex-wrap:wrap;
  background:#161b22; padding:9px 11px; border-radius:9px; margin-bottom:8px; }
.ks-btn { background:#21262d; color:#e6edf3; border:1px solid #2a2f3a; border-radius:7px;
  padding:7px 13px; cursor:pointer; font-size:13px; font-weight:600; line-height:1;
  transition:background .12s, border-color .12s; }
.ks-btn:hover { background:#2d333b; border-color:#3a414c; }
.ks-btn:active { background:#373e47; transform:translateY(1px); }
.ks-btn-go { background:#15493f; border-color:#1c6657; }
.ks-btn-go:hover { background:#1a5a4d; }
.ks-sep { width:1px; height:22px; background:#2a2f3a; margin:0 5px; }
.ks-spacer { flex:1 1 auto; }
.ks-lbl-inline { color:#8a93a3; font-size:12px; margin:0 3px 0 9px; }
.ks-summary-bar { background:#11161d; border:1px solid #1f2630; border-radius:8px;
  padding:8px 13px; margin-bottom:10px; font-size:12.5px; color:#cdd6e0; min-height:19px;
  font-family:ui-monospace,'SF Mono',Menlo,monospace; font-variant-numeric:tabular-nums; }
.ks-body { display:flex; align-items:flex-start; gap:14px; }
.ks-chart { flex:1 1 auto; min-width:0; }
.ks-side { flex:0 0 322px; background:#161b22; border-radius:11px; padding:15px;
  position:sticky; top:14px; max-height:calc(100vh - 28px); overflow-y:auto; }
.ks-cardhead { font-weight:700; font-size:11.5px; letter-spacing:.6px; margin:16px 0 9px;
  text-transform:uppercase; }
.ks-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.ks-field label { color:#8a93a3; font-size:11px; display:block; margin-bottom:4px; }
.ks-field input { width:100%; background:#0b0e13; color:#e6edf3; border:1px solid #2a2f3a;
  border-radius:6px; padding:6px 8px; font-size:13px; font-variant-numeric:tabular-nums; }
.ks-field input:focus { outline:none; border-color:#42a5f5; box-shadow:0 0 0 2px rgba(66,165,245,.18); }
.ks-slider-field { margin-top:14px; }
.ks-check label { display:block; padding:3px 0; font-size:13px; cursor:pointer; }
.ks-check input { margin-right:8px; vertical-align:middle; }
.ks-legend { display:flex; gap:16px; flex-wrap:wrap; color:#8a93a3; font-size:11.5px; margin:7px 2px 0; }
.ks-slider-hint { color:#5a6675; font-size:10.5px; text-align:center; margin:10px 0 2px; }
.ks-row { display:flex; justify-content:space-between; align-items:baseline; padding:2px 0;
  font-family:ui-monospace,'SF Mono',Menlo,monospace; font-size:13px; }
.ks-row .k { color:#8a93a3; padding-right:10px; }
.ks-row .v { font-weight:600; text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.ks-mini { color:#7d8694; font-size:10px; letter-spacing:1.2px; margin:9px 0 4px; font-weight:600; }
hr.ks-hr { border:none; border-top:1px solid #2a2f3a; margin:11px 0; }
.ks-dd .Select-control, .ks-dd .Select-control:hover { background:#0b0e13; border-color:#2a2f3a; }
.ks-dd .Select-value-label, .ks-dd .Select-placeholder,
.ks-dd .Select--single > .Select-control .Select-value { color:#e6edf3 !important; }
.ks-dd .Select-menu-outer { background:#161b22; border-color:#2a2f3a; }
.ks-dd .Select-option { background:#161b22; color:#e6edf3; }
.ks-dd .Select-option.is-focused { background:#2d333b; }
.ks-dd .Select-arrow { border-color:#8a93a3 transparent transparent; }
@media (max-width:1024px) {
  .ks-body { flex-direction:column; }
  .ks-side { flex:1 1 auto; width:100%; position:static; max-height:none; }
  .ks-grid { grid-template-columns:1fr 1fr 1fr; }
}
@media (max-width:560px) {
  .ks-app { padding:10px; } .ks-grid { grid-template-columns:1fr 1fr; }
}
</style></head><body>{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer></body></html>"""

# month boundaries -> scrubber tick marks (turns the timeline into Jan..Jun)
MONTH_MARKS, _pm = {}, None
for _i in range(N):
    _k = (IDX[_i].year, IDX[_i].month)
    if _k != _pm:
        MONTH_MARKS[int(_i)] = {"label": IDX[_i].strftime("%b"),
                                "style": {"color": GREY, "fontSize": "10px"}}
        _pm = _k


def num(id_, label, value, **kw):
    return html.Div(className="ks-field", children=[
        html.Label(label),
        dcc.Input(id=id_, type="number", value=value, debounce=True, **kw)])


def leg(sym, txt, color):
    return html.Span([html.Span(sym, style={"color": color, "fontWeight": 700}),
                      " " + txt])


_DD = {"width": "86px", "display": "inline-block", "verticalAlign": "middle"}

app.layout = html.Div(className="ks-app", children=[

    html.Div(className="ks-head", children=[
        html.Span("⚡ Kalman Backtest Simulator", className="ks-title"),
        html.Span("XAUUSD 15m · 2026 YTD · live-faithful fills", className="ks-sub"),
    ]),

    # transport bar
    html.Div(className="ks-transport", children=[
        html.Button("▶ Play", id="btn-play", n_clicks=0, className="ks-btn ks-btn-go"),
        html.Button("⏸ Pause", id="btn-pause", n_clicks=0, className="ks-btn"),
        html.Button("⏭ Step", id="btn-step", n_clicks=0, className="ks-btn"),
        html.Button("⏮ Reset", id="btn-reset", n_clicks=0, className="ks-btn"),
        html.Span(className="ks-sep"),
        html.Button("◀ Trade", id="btn-prev", n_clicks=0, className="ks-btn"),
        html.Button("Trade ▶", id="btn-next", n_clicks=0, className="ks-btn"),
        html.Span(className="ks-spacer"),
        html.Span("speed", className="ks-lbl-inline"),
        dcc.Dropdown(id="speed", className="ks-dd", clearable=False, style=_DD,
                     options=[{"label": f"{s}×", "value": s} for s in SPEEDS],
                     value=8),
        html.Span("window", className="ks-lbl-inline"),
        dcc.Dropdown(id="window", className="ks-dd", clearable=False, style=_DD,
                     options=[{"label": f"{w}", "value": w}
                              for w in (120, 250, 400, 600)], value=DEFAULT_WINDOW),
    ]),

    html.Div(id="param-summary", className="ks-summary-bar"),

    # main: chart (left) + controls (right) — stacks under 1024px
    html.Div(className="ks-body", children=[
        html.Div(className="ks-chart", children=[
            dcc.Graph(id="graph", style={"width": "100%"},
                      config={"scrollZoom": True, "displayModeBar": True,
                              "displaylogo": False, "responsive": True}),
            html.Div(className="ks-legend", children=[
                leg("▲", "buy entry", GREEN), leg("▼", "sell entry", RED),
                leg("◌", "open trade", BLUE), leg("●", "win exit", GREEN),
                leg("✕", "loss exit", RED), leg("━", "Kalman line", BLUE),
                leg("▮", "trend / range shading", GREY),
            ]),
            html.Div("◀  drag to scrub the timeline  ▶", className="ks-slider-hint"),
            dcc.Slider(id="pos-slider", min=1, max=N - 1, step=1,
                       value=DEFAULT_WINDOW, marks=MONTH_MARKS,
                       tooltip={"placement": "bottom"}, updatemode="drag"),
        ]),

        html.Div(className="ks-side", children=[
            html.Div("Exit / Sizing", className="ks-cardhead",
                     style={"color": BLUE, "marginTop": "0"}),
            html.Div(className="ks-grid", children=[
                num("sl", "Stop loss (pts)", bt.SL_PTS, min=2, step=1),
                num("rr", "Risk : Reward", bt.RR, min=0.25, step=0.25),
                num("lot", "Lot size", bt.LOT, min=0.01, step=0.01),
                num("cost", "Cost / side (pts)", bt.COST, min=0, step=0.05),
                num("maxpos", "Max positions", bt.MAX_POSITIONS, min=1, step=1),
                num("cap", "Daily cap ($, 0=off)", bt.DAILY_LOSS_CAP, min=0, step=10),
            ]),
            dcc.Checklist(id="toggles", className="ks-check", value=["be", "dirlock"],
                          options=[{"label": " Breakeven / lock ratchet", "value": "be"},
                                   {"label": " Directional (no-hedge) lock", "value": "dirlock"}],
                          style={"marginTop": "11px"}),

            html.Div("Setup / Selectivity", className="ks-cardhead",
                     style={"color": AMBER}),
            dcc.Checklist(id="setups", className="ks-check",
                          options=[{"label": f" {SETUP_LABELS[t]}", "value": t}
                                   for t, _, _ in SETUP_DEFS],
                          value=[t for t, _, _ in SETUP_DEFS]),
            html.Div(className="ks-field ks-slider-field", children=[
                html.Label("Min signal strength"),
                dcc.Slider(id="minstr", min=STR_LO, max=1.0, step=0.01, value=STR_LO,
                           marks={STR_LO: {"label": f"{STR_LO}", "style": {"color": GREY, "fontSize": "10px"}},
                                  1.0: {"label": "1.0", "style": {"color": GREY, "fontSize": "10px"}}},
                           tooltip={"placement": "bottom"})]),
            html.Div(className="ks-field ks-slider-field", children=[
                html.Label("Min ADX (trend gate)"),
                dcc.Slider(id="minadx", min=0, max=50, step=1, value=0,
                           marks={v: {"label": str(v), "style": {"color": GREY, "fontSize": "10px"}}
                                  for v in (0, 25, 50)},
                           tooltip={"placement": "bottom"})]),

            html.Hr(className="ks-hr"),
            html.Div(id="stats"),
        ]),
    ]),

    dcc.Interval(id="interval", interval=INTERVAL_MS, disabled=True),
    dcc.Store(id="sim-key"),
])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@app.callback(
    Output("sim-key", "data"),
    Input("sl", "value"), Input("rr", "value"), Input("lot", "value"),
    Input("cost", "value"), Input("maxpos", "value"), Input("cap", "value"),
    Input("toggles", "value"), Input("setups", "value"),
    Input("minstr", "value"), Input("minadx", "value"))
def resim(sl, rr, lot, cost, maxpos, cap, toggles, setups, minstr, minadx):
    toggles = toggles or []
    p = dict(sl=float(sl or bt.SL_PTS), rr=float(rr or bt.RR),
             lot=float(lot or bt.LOT), cost=float(cost or 0.0),
             maxpos=int(maxpos or 1), cap=float(cap or 0.0),
             be="be" in toggles, dirlock="dirlock" in toggles,
             setups=tuple(sorted(setups or [])),
             minstr=round(float(minstr or 0), 3), minadx=float(minadx or 0))
    return run_sim(p)


@app.callback(
    Output("param-summary", "children"),
    Input("sim-key", "data"),
    State("sl", "value"), State("rr", "value"), State("lot", "value"))
def param_summary(key, sl, rr, lot):
    sl, rr, lot = float(sl or 0), float(rr or 0), float(lot or 0)
    vpl = bt.VALUE_PER_LOT
    risk, reward = sl * lot * vpl, sl * rr * lot * vpl
    parts = [f"SL {sl:.0f} pts", f"TP {sl*rr:.0f} pts (RR {rr:g})",
             f"risk ${risk:.0f}/trade ({100*risk/INITIAL:.1f}% acct)",
             f"reward ${reward:.0f}"]
    sim = _SIM_CACHE.get(key)
    if sim and not sim.get("empty"):
        s = sim["stats"]
        pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        parts.append(html.Span(
            f"  ▸  full run  ${s['net']:+,.0f}  ·  PF {pf}  ·  {s['n']} trades  "
            f"·  maxDD ${sim['dd_abs']:,.0f}",
            style={"color": GREEN if s["net"] >= 0 else RED, "fontWeight": 700}))
    out, sep = [], "   ·   "
    for i, p in enumerate(parts):
        if i:
            out.append(sep)
        out.append(p)
    return out


@app.callback(
    Output("interval", "disabled"),
    Input("btn-play", "n_clicks"), Input("btn-pause", "n_clicks"),
    prevent_initial_call=True)
def play_pause(_p, _z):
    return ctx.triggered_id == "btn-pause"


@app.callback(
    Output("pos-slider", "value"),
    Input("interval", "n_intervals"), Input("btn-step", "n_clicks"),
    Input("btn-reset", "n_clicks"), Input("btn-next", "n_clicks"),
    Input("btn-prev", "n_clicks"),
    State("pos-slider", "value"), State("speed", "value"),
    State("window", "value"), State("sim-key", "data"),
    prevent_initial_call=True)
def move(_n, _s, _r, _nx, _pv, cur, speed, window, key):
    trig = ctx.triggered_id
    cur = int(cur or 0)
    if trig == "btn-reset":
        return int(window or DEFAULT_WINDOW)
    if trig in ("interval", "btn-step"):
        return min(cur + int(speed or 1), N - 1)
    sim = _SIM_CACHE.get(key)
    entries = sim["entry_sorted"] if sim and not sim.get("empty") else np.array([])
    if trig == "btn-next" and len(entries):
        nxt = entries[entries > cur]
        return int(nxt[0]) if len(nxt) else no_update
    if trig == "btn-prev" and len(entries):
        prv = entries[entries < cur]
        return int(prv[-1]) if len(prv) else no_update
    return no_update


@app.callback(
    Output("graph", "figure"), Output("stats", "children"),
    Input("pos-slider", "value"), Input("sim-key", "data"),
    Input("window", "value"))
def render(cur, key, window):
    cur = int(cur or DEFAULT_WINDOW)
    return build_figure(cur, key, window or DEFAULT_WINDOW), stats_children(cur, key)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    print(f"\n  ▶  open  http://{args.host}:{args.port}  in your browser\n")
    runner = getattr(app, "run", None) or app.run_server
    runner(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
