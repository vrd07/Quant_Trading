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


def build_figure(df, timeframe, show, p) -> go.Figure:
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
        dcc.DatePickerRange(id="dates", start_date=start_default, end_date=end_default,
                            display_format="YYYY-MM-DD"),
        dcc.RadioItems(["1min", "5min", "15min"], "5min", id="timeframe", inline=True),
        dcc.Checklist(["divergence", "absorption", "imbalance", "sweep", "withdrawal"],
                      ["divergence", "absorption", "imbalance", "sweep"], id="show"),
        slider("lookback", 5, 60, 1, 20, "divergence lookback (bars)"),
        slider("band_pts", 0.1, 2.0, 0.1, 0.5, "absorption band (pts)"),
        slider("flow_pctile", 50, 99, 1, 90, "absorption flow pctile"),
        slider("ratio", 2.0, 6.0, 0.5, 3.0, "imbalance ratio"),
        slider("burst_pctile", 80, 99.5, 0.5, 95, "sweep burst pctile"),
        slider("spread_pctile", 80, 99.5, 0.5, 95, "withdrawal spread pctile"),
        slider("price_bin", 0.25, 2.0, 0.25, 0.5, "price bin (pts)"),
    ], style={"width": "270px", "padding": "10px", "flexShrink": "0"})

    app.layout = html.Div([
        controls,
        html.Div(dcc.Loading(dcc.Graph(id="chart")), style={"flexGrow": "1"}),
    ], style={"display": "flex"})

    @app.callback(
        Output("chart", "figure"),
        Input("dates", "start_date"), Input("dates", "end_date"),
        Input("timeframe", "value"), Input("show", "value"),
        Input("lookback", "value"), Input("band_pts", "value"),
        Input("flow_pctile", "value"), Input("ratio", "value"),
        Input("burst_pctile", "value"), Input("spread_pctile", "value"),
        Input("price_bin", "value"))
    def update(start, end, timeframe, show, lookback, band_pts, flow_pctile,
               ratio, burst_pctile, spread_pctile, price_bin):
        df = _ticks(symbol, start[:10], end[:10])
        params = dict(lookback=lookback, band_pts=band_pts, flow_pctile=flow_pctile,
                      ratio=ratio, burst_pctile=burst_pctile,
                      spread_pctile=spread_pctile, price_bin=price_bin)
        return build_figure(df, timeframe, show or [], params)

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
