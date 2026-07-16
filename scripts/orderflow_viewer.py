#!/usr/bin/env python3
"""
Order-flow marking viewer (Plotly Dash) — Stage 1 of the order-flow spec.

Candles over a volume-at-price heatmap with proxy order-flow marks:
delta divergences, absorption zones, footprint imbalances, sweeps, and
liquidity-withdrawal warnings, plus a cumulative-delta subplot. All
quantities are proxies from Dukascopy quote ticks — no real DOM exists
for spot gold. Sliders re-run detectors instantly; tick loads are cached.

    python scripts/orderflow_viewer.py                 # http://127.0.0.1:8050
    python scripts/orderflow_viewer.py --port 8060 --symbol XAUUSD
    LIVE mode: switch the Mode radio to LIVE while the bot's MT5 terminal runs — marks appear as each candle closes; reads ONLY mt5_status.json (never the bridge command channel).
"""
import argparse
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html
from plotly.subplots import make_subplots

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_dukascopy_ticks import ensure_ticks  # noqa: E402
from src.microstructure import features as ft  # noqa: E402
from src.microstructure import live_feed as lfeed  # noqa: E402
from src.microstructure import live_marks as lmarks  # noqa: E402
import pandas as pd  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

MARK_STYLE = {
    "bearish_divergence": dict(symbol="triangle-down", color="#d62728"),
    "bullish_divergence": dict(symbol="triangle-up", color="#2ca02c"),
    "absorption_of_selling": dict(symbol="square", color="#2ca02c"),
    "absorption_of_buying": dict(symbol="square", color="#d62728"),
    "imbalance_buy": dict(symbol="diamond", color="#1f77b4"),
    "imbalance_sell": dict(symbol="diamond", color="#ff7f0e"),
    "sweep_high": dict(symbol="x", color="#d62728"),
    "sweep_low": dict(symbol="x", color="#2ca02c"),
    "liquidity_withdrawal": dict(symbol="line-ns-open", color="#7f7f7f"),
}

LIVE_PARAM_KEYS = ("lookback", "band_pts", "flow_pctile", "ratio",
                   "burst_pctile", "spread_pctile", "price_bin")
_LIVE: dict = {"tap": None, "backfill": None, "feed": None, "day": None}


def _ensure_live(symbol: str):
    """Start (once) the tap/backfill/feed trio for today. Idempotent; rolls
    over automatically when the UTC day changes."""
    today = datetime.now(timezone.utc).date()
    if _LIVE["tap"] is None or _LIVE["day"] != today:
        if _LIVE["tap"] is not None:
            _LIVE["tap"].stop()
        tap = lfeed.StatusTap(symbol)
        tap.preload_spill(today)
        tap.start()
        _LIVE.update(
            tap=tap,
            backfill=lfeed.DukaBackfill(symbol, today),
            feed=lmarks.SignalFeed(lfeed.LIVE_DIR / symbol / f"{today}_signals.jsonl"),
            day=today,
        )
    return _LIVE["tap"], _LIVE["backfill"], _LIVE["feed"]


def _feed_color(kind: str) -> str:
    good = ("buy", "bullish", "selling", "low")   # *_of_selling = buyers defending
    return "#2ca02c" if any(g in kind for g in good) else "#d62728"


def _feed_table(entries, limit=50):
    rows = [html.Tr([
        html.Td(e.emitted_at[11:19]),
        html.Td(e.bar_ts[11:16]),
        html.Td(e.kind, style={"color": _feed_color(e.kind)}),
        html.Td(f"{e.price:.2f}"),
        html.Td(f"{e.strength:.2f}"),
    ]) for e in list(entries)[::-1][:limit]]
    header = html.Tr([html.Th(h) for h in
                      ("emitted", "bar", "kind", "price", "strength")])
    return html.Table([header] + rows, style={"fontSize": "12px", "width": "100%"})


@lru_cache(maxsize=4)
def _ticks(symbol: str, start_iso: str, end_iso: str):
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    ensure_ticks(symbol, start, end)
    return ft.load_ticks(symbol, start, end)


def _detect(df, bars, delta, show, p):
    """Run only the enabled detectors; return list[FlowEvent]."""
    events = []
    if "divergence" in show:
        events += ft.delta_divergence(bars, delta, lookback=int(p["lookback"]))
    if "absorption" in show:
        events += ft.absorption_zones(df, band_pts=p["band_pts"],
                                      flow_pctile=p["flow_pctile"])
    if "imbalance" in show:
        events += ft.imbalance_events(df, freq=p["timeframe"],
                                      price_bin=p["price_bin"], ratio=p["ratio"])
    if "sweep" in show:
        events += ft.sweep_events(df, burst_pctile=p["burst_pctile"])
    if "withdrawal" in show:
        events += ft.liquidity_withdrawal(df, spread_pctile=p["spread_pctile"])
    return events


def build_figure(df, timeframe, show, p, events=None) -> go.Figure:
    bars = ft.resample_bars(df, timeframe)
    delta = ft.bar_delta(df, timeframe)
    span_days = max((df.index[-1] - df.index[0]).days, 1)
    time_bin = "15min" if span_days <= 14 else "1h"   # heatmap guard for long ranges
    vap = ft.volume_at_price(df, price_bin=p["price_bin"], time_bin=time_bin)
    nodes = ft.profile_nodes(vap)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.72, 0.28], vertical_spacing=0.03)
    fig.add_trace(go.Heatmap(x=vap.columns, y=vap.index, z=vap.values,
                             colorscale="Blues", opacity=0.5, showscale=False,
                             hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Candlestick(x=bars.index, open=bars["open"], high=bars["high"],
                                 low=bars["low"], close=bars["close"],
                                 name=timeframe), row=1, col=1)
    for y in nodes["hvn"]:
        fig.add_hline(y=y, line=dict(color="rgba(255,165,0,0.6)", width=1), row=1, col=1)
    for y in nodes["lvn"]:
        fig.add_hline(y=y, line=dict(color="rgba(128,128,128,0.4)", width=1, dash="dot"),
                      row=1, col=1)

    if events is None:
        events = _detect(df, bars, delta, show, {**p, "timeframe": timeframe})
    by_kind = {}
    for e in events:
        by_kind.setdefault(e.kind, []).append(e)
    for kind, evs in by_kind.items():
        style = MARK_STYLE[kind]
        fig.add_trace(go.Scatter(
            x=[e.ts for e in evs], y=[e.price for e in evs], mode="markers",
            name=kind, marker=dict(size=11, line=dict(width=1), **style),
            hovertext=[f"{kind}<br>{e.ts:%m-%d %H:%M}<br>px {e.price:.2f}"
                       f"<br>strength {e.strength:.2f}" for e in evs],
            hoverinfo="text"), row=1, col=1)

    if "defended" in show:
        for lvl in lmarks.defended_levels(events):
            color = "42,160,44" if lvl.side == "buyers" else "214,39,40"
            fig.add_hline(y=lvl.price, row=1, col=1,
                          line=dict(color=f"rgba({color},{min(0.25 + 0.15 * lvl.touches, 0.9)})",
                                    width=2 + lvl.touches),
                          annotation_text=f"defended x{lvl.touches}",
                          annotation_font_size=9)
    if "pools" in show:
        for pool in lmarks.liquidity_pools(bars):
            fig.add_hline(y=pool.price, row=1, col=1,
                          line=dict(color="rgba(128,0,128,0.55)", width=1, dash="dash"),
                          annotation_text=f"{pool.kind} ({pool.side}, inferred)",
                          annotation_font_size=8)

    fig.add_trace(go.Bar(x=delta.index, y=delta["delta"], name="delta",
                         marker_color=["#2ca02c" if v >= 0 else "#d62728"
                                       for v in delta["delta"]]), row=2, col=1)
    fig.add_trace(go.Scatter(x=delta.index, y=delta["cum_delta"], name="cum delta",
                             line=dict(color="#1f77b4", width=2)), row=2, col=1)
    fig.update_layout(height=880, xaxis_rangeslider_visible=False,
                      margin=dict(l=40, r=20, t=30, b=30),
                      legend=dict(orientation="h", y=1.02),
                      uirevision="keep-zoom")
    return fig


def make_app(symbol: str) -> Dash:
    app = Dash(__name__)
    end_default = date.today() - timedelta(days=1)
    start_default = end_default - timedelta(days=4)

    def slider(id_, lo, hi, step, val, label):
        return html.Div([html.Label(label, style={"fontSize": "12px"}),
                         dcc.Slider(lo, hi, step, value=val, id=id_,
                                    marks=None, tooltip={"placement": "bottom",
                                                         "always_visible": True})],
                        style={"marginBottom": "6px"})

    controls = html.Div([
        html.H3(f"{symbol} order-flow marks (proxy)"),
        dcc.RadioItems(["HISTORY", "LIVE"], "HISTORY", id="mode", inline=True),
        html.Div(id="live-status", style={"fontSize": "12px", "margin": "4px 0"}),
        dcc.DatePickerRange(id="dates", start_date=start_default, end_date=end_default,
                            display_format="YYYY-MM-DD"),
        dcc.RadioItems(["1min", "5min", "15min"], "5min", id="timeframe", inline=True),
        dcc.Checklist(["divergence", "absorption", "imbalance", "sweep", "withdrawal",
                       "defended", "pools"],
                      ["divergence", "absorption", "imbalance", "sweep"], id="show"),
        slider("lookback", 5, 60, 1, 20, "divergence lookback (bars)"),
        slider("band_pts", 0.1, 2.0, 0.1, 0.5, "absorption band (pts)"),
        slider("flow_pctile", 50, 99, 1, 90, "absorption flow pctile"),
        slider("ratio", 2.0, 6.0, 0.5, 3.0, "imbalance ratio"),
        slider("burst_pctile", 80, 99.5, 0.5, 95, "sweep burst pctile"),
        slider("spread_pctile", 80, 99.5, 0.5, 95, "withdrawal spread pctile"),
        slider("price_bin", 0.25, 2.0, 0.25, 0.5, "price bin (pts)"),
        html.Div(id="feed", style={"maxHeight": "320px", "overflowY": "auto",
                                   "marginTop": "8px"}),
        dcc.Interval(id="live-interval", interval=20_000, disabled=True),
    ], style={"width": "270px", "padding": "10px", "flexShrink": "0"})

    app.layout = html.Div([
        controls,
        html.Div(dcc.Loading(dcc.Graph(id="chart")), style={"flexGrow": "1"}),
    ], style={"display": "flex"})

    @app.callback(Output("live-interval", "disabled"), Output("dates", "disabled"),
                  Input("mode", "value"))
    def toggle_mode(mode):
        live = mode == "LIVE"
        return (not live), live

    @app.callback(
        Output("chart", "figure"),
        Output("feed", "children"), Output("live-status", "children"),
        Input("dates", "start_date"), Input("dates", "end_date"),
        Input("timeframe", "value"), Input("show", "value"),
        Input("lookback", "value"), Input("band_pts", "value"),
        Input("flow_pctile", "value"), Input("ratio", "value"),
        Input("burst_pctile", "value"), Input("spread_pctile", "value"),
        Input("price_bin", "value"),
        Input("mode", "value"), Input("live-interval", "n_intervals"))
    def update(start, end, timeframe, show, lookback, band_pts, flow_pctile,
               ratio, burst_pctile, spread_pctile, price_bin, mode, _n):
        params = dict(lookback=lookback, band_pts=band_pts, flow_pctile=flow_pctile,
                      ratio=ratio, burst_pctile=burst_pctile,
                      spread_pctile=spread_pctile, price_bin=price_bin)
        show = show or []
        if mode == "LIVE":
            tap, backfill, feed = _ensure_live(symbol)
            now = pd.Timestamp.now(tz="UTC")
            df = lfeed.stitch_day(backfill.refresh(now.to_pydatetime()),
                                  tap.rows_df())
            stale = tap.staleness_s()
            badge_style = {"color": "#d62728" if stale > 15 else "#2ca02c"}
            hours = backfill.published_hours()
            status = html.Span([
                html.B("LIVE ", style=badge_style),
                f"tap {'∞' if stale == float('inf') else f'{stale:.0f}s'} | "
                f"backfill→{(f'{hours[-1]:02d}h' if hours else '—')} | "
                f"{len(df):,} ticks | feed {len(feed.entries)}",
            ], style=badge_style if stale > 15 else None)
            if df.empty:
                return (go.Figure(layout=dict(
                    title="LIVE: no data yet — is MT5 running? (backfill lags 1-2h)")),
                    _feed_table(feed.entries), status)
            events = lmarks.closed_candle_events(df, timeframe, params, now)
            feed.ingest(events, now)
            fig = build_figure(df, timeframe, show, params, events=events)
            return fig, _feed_table(feed.entries), status
        df = _ticks(symbol, start[:10], end[:10])
        return (build_figure(df, timeframe, show, params),
                "(feed active in LIVE mode)", "")

    return app


def main() -> int:
    p = argparse.ArgumentParser(description="Order-flow marking viewer")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--port", type=int, default=8050)
    args = p.parse_args()
    make_app(args.symbol).run(debug=False, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
