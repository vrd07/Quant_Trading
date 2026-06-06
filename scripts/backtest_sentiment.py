#!/usr/bin/env python3
"""Per-leg backtest of the Gold Sentiment Score → IC-weighted weights.

The GSS hand-sets its weights (Fundamental 30 / Technical 25 / Institutional 20 /
Retail 15 / News 10, market_sentiment.md §4). This harness asks the data which
legs actually predict gold, so the weights can be EARNED instead of guessed.

It reconstructs the three legs that have real multi-year free history, each with
the SAME deterministic scorer the live engine uses, point-in-time as of the trade
entry (no look-ahead), over the last ~2 years on a weekly (COT-release) cadence:

  • Fundamental  — FRED DFII10 / DTWEXBGS / CPIAUCSL / FEDFUNDS  (needs FRED_API_KEY)
  • Technical    — EMA/RSI/MACD/BB on GLD daily via the project Indicators lib
  • Institutional— COT net-long wow% (CFTC) + real ETF tonnes flow (State Street)

Retail (Myfxbook) and News (Alpha Vantage) have no multi-year history and are
left out — a full-GSS 2y backtest isn't possible without paid feeds.

For each leg it reports the information coefficient (Spearman of sub-score vs
forward gold return) and directional hit-rate, then proposes weights ∝ max(0, IC)
— a leg that doesn't predict gets ~0, a negative-IC leg is flagged (invert only
after walk-forward, never on one regime).

Price = GLD daily close (SPDR file, history to 2004; GLD direction == spot/XAUUSD
direction). Entry = first session after the Friday COT release.

Run:  set -a; . config/sentiment.env; set +a; python scripts/backtest_sentiment.py --years 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import urllib.parse
import urllib.request
import zipfile
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from src.data.indicators import Indicators                              # noqa: E402
from src.sentiment.feeds import _GLD_HIST_URL, _XL_NS, _flow_label      # noqa: E402
from src.sentiment.gss import (                                         # noqa: E402
    MAX_FUNDAMENTAL, MAX_INSTITUTIONAL, MAX_TECHNICAL,
    score_fundamental, score_institutional, score_technical,
)

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_CFTC = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_GOLD_CODE = "088691"
_FRED = "https://api.stlouisfed.org/fred/series/observations"

LEG_MAX = {"fundamental": MAX_FUNDAMENTAL, "technical": MAX_TECHNICAL,
           "institutional": MAX_INSTITUTIONAL}


# ── data loaders ─────────────────────────────────────────────────────────────
def load_spdr() -> List[Tuple[dt.date, float, float]]:
    """(date, gld_close, total_ounces) oldest→newest from the SPDR historical XLSX."""
    req = urllib.request.Request(_GLD_HIST_URL, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    si = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = ["".join(t.text or "" for t in s.iter(f"{_XL_NS}t"))
               for s in si.findall(f"{_XL_NS}si")]
    sheets = [n for n in zf.namelist()
              if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
    root = ET.fromstring(max((zf.read(n) for n in sheets), key=len))
    out: List[Tuple[dt.date, float, float]] = []
    for row in root.iter(f"{_XL_NS}row"):
        cells = {}
        for c in row.findall(f"{_XL_NS}c"):
            col = c.get("r", "").rstrip("0123456789")
            v = c.find(f"{_XL_NS}v")
            if v is None:
                continue
            cells[col] = strings[int(v.text)] if c.get("t") == "s" else v.text
        a, b, i = cells.get("A"), cells.get("B"), cells.get("I")
        if not a or b in (None, "US Holiday"):
            continue
        try:
            d = dt.datetime.strptime(a, "%d-%b-%Y").date()
            out.append((d, float(b), float(i)))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def load_cot(weeks: int = 200) -> List[Tuple[dt.date, float]]:
    q = urllib.parse.urlencode({
        "cftc_contract_market_code": _GOLD_CODE,
        "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": str(weeks),
        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                   "noncomm_positions_short_all",
    })
    with urllib.request.urlopen(f"{_CFTC}?{q}", timeout=30) as r:
        data = json.load(r)
    rows = []
    for d in data:
        try:
            day = dt.date.fromisoformat(d["report_date_as_yyyy_mm_dd"][:10])
            net = float(d["noncomm_positions_long_all"]) - float(d["noncomm_positions_short_all"])
            rows.append((day, net))
        except (KeyError, ValueError, TypeError):
            continue
    rows.sort(key=lambda x: x[0])
    return rows


def load_fred(series_id: str, key: str, start: dt.date) -> List[Tuple[dt.date, float]]:
    """(date, value) oldest→newest for a FRED series since `start`. [] on error."""
    try:
        q = urllib.parse.urlencode({
            "series_id": series_id, "api_key": key, "file_type": "json",
            "observation_start": start.isoformat(), "sort_order": "asc",
        })
        with urllib.request.urlopen(f"{_FRED}?{q}", timeout=30) as r:
            data = json.load(r)
        out = []
        for o in data.get("observations", []):
            v = o.get("value")
            if v in (None, ".", ""):
                continue
            try:
                out.append((dt.date.fromisoformat(o["date"]), float(v)))
            except (ValueError, KeyError):
                continue
        return out
    except Exception:
        return []


# ── as-of helpers (point-in-time, no look-ahead) ─────────────────────────────
def asof_idx(series, target: dt.date, lag_days: int = 0) -> Optional[int]:
    """Index of the newest row dated ≤ target-lag (publication lag for releases)."""
    cut = target - dt.timedelta(days=lag_days)
    found = None
    for i, row in enumerate(series):
        if row[0] <= cut:
            found = i
        else:
            break
    return found


def idx_on_or_after(series, target: dt.date) -> Optional[int]:
    for i, row in enumerate(series):
        if row[0] >= target:
            return i
    return None


def trend_falling(series, idx: int, look: int = 3) -> Optional[bool]:
    if idx is None or idx < look:
        return None
    return series[idx][1] < series[idx - look][1]


# ── leg reconstructors (use the LIVE scorers) ────────────────────────────────
def technical_asof(tech_ind: pd.DataFrame, entry_date: dt.date) -> Optional[float]:
    """score_technical from precomputed causal indicators, as of entry_date.
    Mirrors src/sentiment/technical.compute_technical's derivation exactly."""
    sub = tech_ind[tech_ind.index <= pd.Timestamp(entry_date)]
    if len(sub) < 200:
        return None
    r = sub.iloc[-1]
    close, ema50, ema200 = r["close"], r["ema50"], r["ema200"]
    if close > ema50 > ema200:
        trend = "bull_aligned"
    elif close < ema50 < ema200:
        trend = "bear_aligned"
    elif close > ema50:
        trend = "recovering"
    else:
        trend = "chop"
    if close > r["bb_up"]:
        bb = "upper_walk"
    elif close < r["bb_lo"]:
        bb = "lower_breach"
    else:
        bb = "inside"
    return score_technical(trend=trend, rsi_14=r["rsi"],
                           macd_bullish=bool(r["macd"] > r["sig"]), bb_state=bb)


def fundamental_asof(fred: Dict[str, list], entry_date: dt.date) -> Optional[float]:
    dfii = fred.get("DFII10", [])
    dxy = fred.get("DTWEXBGS", [])
    cpi = fred.get("CPIAUCSL", [])
    ff = fred.get("FEDFUNDS", [])
    if not dfii and not cpi:
        return None
    i_y = asof_idx(dfii, entry_date, 1)
    real_y = dfii[i_y][1] if i_y is not None else None
    y_fall = trend_falling(dfii, i_y)
    i_d = asof_idx(dxy, entry_date, 1)
    d_fall = trend_falling(dxy, i_d)
    # monthly series carry publication lag: CPI ~2wk after month end, funds prompt
    i_ff = asof_idx(ff, entry_date, 35)
    fed = None
    if i_ff is not None and i_ff >= 1:
        fed = ("dovish" if ff[i_ff][1] < ff[i_ff - 1][1]
               else "hawkish" if ff[i_ff][1] > ff[i_ff - 1][1] else "neutral")
    i_c = asof_idx(cpi, entry_date, 45)
    cpi_yoy = None
    if i_c is not None and i_c >= 12:
        cpi_yoy = round((cpi[i_c][1] / cpi[i_c - 12][1] - 1.0) * 100, 2)
    return score_fundamental(fed_policy=fed, real_yield_10y=real_y,
                             real_yield_falling=y_fall, dxy_falling=d_fall,
                             dxy_level=None, cpi_yoy=cpi_yoy, fiscal_stress=None)


def institutional_asof(spdr, cot, k: int) -> Optional[float]:
    net_prev = cot[k - 1][1]
    wow = round((cot[k][1] - net_prev) / abs(net_prev) * 100, 2) if net_prev else None
    j = asof_idx(spdr, cot[k][0], 0)
    flow = None
    if j is not None and j >= 3:
        flow = _flow_label([spdr[j - 3][2], spdr[j - 2][2], spdr[j - 1][2], spdr[j][2]])
    if wow is None and flow is None:
        return None
    return score_institutional(cot_net_long_wow_pct=wow, etf_flow_3d=flow)


# ── stats ────────────────────────────────────────────────────────────────────
def spearman(xs, ys) -> Optional[float]:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 8:
        return None

    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1
            i = j + 1
        return r
    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else None


def sign(x):
    return (x > 0) - (x < 0)


# ── main ─────────────────────────────────────────────────────────────────────
def build_tech_indicators(spdr) -> pd.DataFrame:
    """Causal EMA/RSI/MACD/BB on the full GLD daily close series, computed once."""
    df = pd.DataFrame({"close": [c for _d, c, _o in spdr]},
                      index=pd.to_datetime([d for d, _c, _o in spdr]))
    for col in ("open", "high", "low"):
        df[col] = df["close"]
    df["volume"] = 0
    up, _mid, lo = Indicators.bollinger_bands(df)
    macd_line, sig_line, _h = Indicators.macd(df)
    return pd.DataFrame({
        "close": df["close"], "ema50": Indicators.ema(df, 50),
        "ema200": Indicators.ema(df, 200), "rsi": Indicators.rsi(df, 14),
        "macd": macd_line, "sig": sig_line, "bb_up": up, "bb_lo": lo,
    })


def run(years: float, horizons_w: List[int]):
    spdr = load_spdr()
    cot = load_cot(weeks=int(years * 53) + 8)   # enough weekly rows for the window
    cutoff = dt.date.today() - dt.timedelta(days=int(years * 365))
    px = [(d, c) for (d, c, _o) in spdr]
    tech_ind = build_tech_indicators(spdr)

    key = os.environ.get("FRED_API_KEY")
    fred = {}
    if key:
        for sid in ("DFII10", "DTWEXBGS", "CPIAUCSL", "FEDFUNDS"):
            fred[sid] = load_fred(sid, key, cutoff - dt.timedelta(days=500))
    has_fund = bool(key and fred.get("DFII10"))

    samples = []
    for k in range(1, len(cot)):
        date = cot[k][0]
        if date < cutoff:
            continue
        ei = idx_on_or_after(px, date + dt.timedelta(days=4))
        if ei is None:
            continue
        entry_d, entry_p = px[ei]
        legs = {
            "institutional": institutional_asof(spdr, cot, k),
            "technical": technical_asof(tech_ind, entry_d),
            "fundamental": fundamental_asof(fred, entry_d) if has_fund else None,
        }
        fwd = {}
        for h in horizons_w:
            xi = idx_on_or_after(px, entry_d + dt.timedelta(days=7 * h))
            fwd[h] = (px[xi][1] / entry_p - 1.0) * 100 if xi is not None else None
        samples.append({"date": date, "entry_d": entry_d, "legs": legs, "fwd": fwd})

    _report(samples, horizons_w, years, cutoff, has_fund)


def _report(samples, horizons_w, years, cutoff, has_fund):
    print("=" * 78)
    print(f"GSS PER-LEG BACKTEST — last {years:g}y  ({cutoff} → today)   "
          f"{len(samples)} weekly samples")
    print("  sub-scores from the LIVE scorers, as-of entry (COT-release+1 session)")
    print("  forward gold return = GLD daily close; IC = Spearman(sub-score, fwd ret)")
    print("=" * 78)
    if not samples:
        print("No samples.")
        return
    legs = ["fundamental", "technical", "institutional"]

    for h in horizons_w:
        print(f"\n── horizon {h}w ──   ('hit' = sign(sub−mid) matches gold; "
              f"base up-rate shown)")
        rows = [s for s in samples if s["fwd"][h] is not None]
        ups = sum(1 for r in rows if r["fwd"][h] > 0)
        base = 100.0 * ups / len(rows) if rows else 0.0
        print(f"   gold base up-week rate: {base:.1f}%   (n={len(rows)})")
        print(f"   {'leg':<14}{'n':>4}{'IC':>8}{'hit%':>8}{'bull_ret':>10}"
              f"{'bear_ret':>10}")
        ic_by_leg = {}
        for leg in legs:
            mid = LEG_MAX[leg] / 2.0
            r = [(s["legs"][leg], s["fwd"][h]) for s in rows if s["legs"][leg] is not None]
            if not r:
                print(f"   {leg:<14}{'—':>4}{'(no data — leg not reconstructed)':>40}")
                ic_by_leg[leg] = None
                continue
            ic = spearman([a for a, _ in r], [b for _, b in r])
            ic_by_leg[leg] = ic
            nz = [(a, b) for a, b in r if a != mid and b != 0]
            hit = 100.0 * sum(1 for a, b in nz if sign(a - mid) == sign(b)) / len(nz) if nz else float("nan")
            bull = [b for a, b in r if a > mid]
            bear = [b for a, b in r if a < mid]
            bm = sum(bull) / len(bull) if bull else float("nan")
            br = sum(bear) / len(bear) if bear else float("nan")
            print(f"   {leg:<14}{len(r):>4}{(ic if ic is not None else float('nan')):>8}"
                  f"{hit:>7.1f}%{bm:>10.2f}{br:>10.2f}")

        # composite: current hand-set weighting (sum of sub-scores) vs IC-weighted
        comp_cur = [(sum(s["legs"][l] for l in legs if s["legs"][l] is not None),
                     s["fwd"][h]) for s in rows
                    if any(s["legs"][l] is not None for l in legs)]
        ic_cur = spearman([a for a, _ in comp_cur], [b for _, b in comp_cur])
        # IC-weighted: each leg normalized to [-1,1], times max(0,IC); skip None legs
        comp_icw = []
        for s in rows:
            tot, used = 0.0, False
            for leg in legs:
                v, ic = s["legs"][leg], ic_by_leg.get(leg)
                if v is None or ic is None or ic <= 0:
                    continue
                tot += (v / LEG_MAX[leg] * 2 - 1) * ic
                used = True
            if used:
                comp_icw.append((tot, s["fwd"][h]))
        ic_icw = spearman([a for a, _ in comp_icw], [b for _, b in comp_icw]) if comp_icw else None
        print(f"   {'COMPOSITE cur':<14}{len(comp_cur):>4}{(ic_cur if ic_cur is not None else float('nan')):>8}"
              f"   (hand-set 30/25/20 sum)")
        print(f"   {'COMPOSITE icw':<14}{len(comp_icw):>4}{(ic_icw if ic_icw is not None else float('nan')):>8}"
              f"   (IC-weighted, neg-IC legs dropped)")

    # weight recommendation off the primary horizon
    h = horizons_w[min(1, len(horizons_w) - 1)]   # default 2w if present
    rows = [s for s in samples if s["fwd"][h] is not None]
    print(f"\n── RECOMMENDED WEIGHTS (∝ max(0, IC) @ {h}w, 75-pt reconstructable budget) ──")
    ics = {}
    for leg in legs:
        r = [(s["legs"][leg], s["fwd"][h]) for s in rows if s["legs"][leg] is not None]
        ics[leg] = spearman([a for a, _ in r], [b for _, b in r]) if r else None
    pos = {l: max(0.0, ics[l]) for l in legs if ics[l] is not None}
    tot = sum(pos.values()) or 1.0
    budget = sum(LEG_MAX[l] for l in legs)  # 75
    print(f"   {'leg':<14}{'IC':>8}{'current':>9}{'earned':>9}")
    for leg in legs:
        ic = ics[leg]
        earned = budget * max(0.0, ic) / tot if ic is not None else 0.0
        flag = "" if (ic is None or ic > 0) else "  ← negative IC: drop/invert (needs walk-forward)"
        cur = LEG_MAX[leg]
        print(f"   {leg:<14}{(ic if ic is not None else float('nan')):>8}{cur:>8}p{earned:>8.1f}p{flag}")
    if not has_fund:
        print("\n   ⚠ fundamental NOT reconstructed (FRED_API_KEY unset). Re-run with:")
        print("     set -a; . config/sentiment.env; set +a; python scripts/backtest_sentiment.py")
    print("\nRetail (15) + News (10) have no multi-year free history → not measurable")
    print("here. Earned weights are a TEMPLATE: validate walk-forward before shipping.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--horizons", default="1,2,4")
    a = ap.parse_args()
    run(a.years, [int(x) for x in a.horizons.split(",")])
