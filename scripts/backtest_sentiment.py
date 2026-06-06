#!/usr/bin/env python3
"""Backtest the REAL institutional sentiment signal against forward gold moves.

The institutional GSS leg is the one just made real: COT net-long week-over-week
(CFTC) + ETF 3-session flow from State Street's actual GLD tonnes-in-trust (not a
price proxy). Both have multi-year free history, so unlike the retail/news legs
they can actually be backtested — exactly what the project's deploy gate demands
("prove the GSS edge in backtest BEFORE it influences risk").

What this measures (market_sentiment.md gate: "GSS predicted direction in 60%+ of
backtested weeks, 2024-2026"):
  • weekly cadence anchored to the Tuesday COT report date;
  • signal = score_institutional(cot_wow%, etf_flow) - neutral(10), so >0 bull /
    <0 bear — using the SAME deterministic scorer the live engine uses;
  • forward gold return measured from the first session AFTER the Friday COT
    release (report_date + 4d) → no look-ahead;
  • price = GLD daily close from the same SPDR file (history to 2004; GLD
    direction == spot-gold/XAUUSD direction). XAUUSD CSV only reaches 2025-01.

Honest benchmark: gold's base up-week rate is printed alongside the hit rate — a
60% hit rate is only edge if it beats always-long in a bull regime.

Run:  python scripts/backtest_sentiment.py --years 2
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
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sentiment.feeds import _GLD_HIST_URL, _XL_NS, _flow_label  # noqa: E402
from src.sentiment.gss import score_institutional                   # noqa: E402

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_CFTC = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_GOLD_CODE = "088691"


# ── data loaders ─────────────────────────────────────────────────────────────
def load_spdr() -> List[Tuple[dt.date, float, float]]:
    """(date, gld_close, total_ounces) oldest→newest from the SPDR historical
    XLSX, skipping holidays. Cols A=Date, B=Closing Price, I=Total Ounces."""
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
    """(report_date, noncommercial_net_long) oldest→newest from CFTC."""
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


# ── helpers ──────────────────────────────────────────────────────────────────
def idx_on_or_after(series, target: dt.date) -> Optional[int]:
    for i, row in enumerate(series):
        if row[0] >= target:
            return i
    return None


def idx_on_or_before(series, target: dt.date) -> Optional[int]:
    found = None
    for i, row in enumerate(series):
        if row[0] <= target:
            found = i
        else:
            break
    return found


def etf_flow_asof(spdr, report_date: dt.date) -> Optional[str]:
    """3-session ounces flow label as of report_date (same logic as live)."""
    j = idx_on_or_before(spdr, report_date)
    if j is None or j < 3:
        return None
    ounces = [spdr[j - 3][2], spdr[j - 2][2], spdr[j - 1][2], spdr[j][2]]
    return _flow_label(ounces)


def cot_wow_pct(net_now: float, net_prev: float) -> Optional[float]:
    if net_prev == 0:
        return None
    return round((net_now - net_prev) / abs(net_prev) * 100, 2)


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


# ── backtest ─────────────────────────────────────────────────────────────────
def run(years: float, horizons_w: List[int]):
    spdr = load_spdr()
    cot = load_cot()
    cutoff = dt.date.today() - dt.timedelta(days=int(years * 365))
    px = [(d, c) for (d, c, _o) in spdr]   # (date, gld_close)

    samples = []   # dicts per week
    for k in range(1, len(cot)):
        date, net = cot[k]
        if date < cutoff:
            continue
        wow = cot_wow_pct(net, cot[k - 1][1])
        flow = etf_flow_asof(spdr, date)
        if wow is None and flow is None:
            continue
        inst = score_institutional(cot_net_long_wow_pct=wow, etf_flow_3d=flow)
        if inst is None:
            continue
        raw = inst - 10.0                          # signed: >0 bull, <0 bear
        # entry = first session AFTER the Friday release (report_date + 4d)
        ei = idx_on_or_after(px, date + dt.timedelta(days=4))
        if ei is None:
            continue
        entry_d, entry_p = px[ei]
        fwd = {}
        for h in horizons_w:
            xi = idx_on_or_after(px, entry_d + dt.timedelta(days=7 * h))
            fwd[h] = (px[xi][1] / entry_p - 1.0) * 100 if xi is not None else None
        samples.append({"date": date, "wow": wow, "flow": flow, "inst": inst,
                        "raw": raw, "entry_d": entry_d, "entry_p": entry_p, "fwd": fwd})

    _report(samples, horizons_w, years, px, cutoff)


def _spearman(xs, ys) -> Optional[float]:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 5:
        return None

    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else None


def _report(samples, horizons_w, years, px, cutoff):
    print("=" * 72)
    print(f"INSTITUTIONAL SENTIMENT BACKTEST — last {years:g}y  ({cutoff} → today)")
    print("  signal = score_institutional(COT wow%, real ETF tonnes flow) − 10")
    print("  price  = GLD daily close (SPDR XLSX);  forward from COT-release+1 session")
    print("=" * 72)
    if not samples:
        print("No samples — insufficient overlapping history.")
        return
    print(f"Weekly samples: {len(samples)}   "
          f"({samples[0]['date']} → {samples[-1]['date']})")

    # base up-week rate over the same window (honest benchmark, 1w)
    print("\n-- forward-direction edge by horizon --")
    print(f"{'horizon':>8} {'n':>4} {'base_up%':>9} {'hit%':>7} {'IC':>6} "
          f"{'bull_ret%':>10} {'bear_ret%':>10} {'L/S_ret%':>9} {'BH_ret%':>8}")
    for h in horizons_w:
        rows = [s for s in samples if s["fwd"][h] is not None]
        # base rate: fraction of all measured weeks whose fwd move was up
        ups = [r for r in rows if r["fwd"][h] > 0]
        base = 100.0 * len(ups) / len(rows) if rows else 0.0
        # directional hit-rate on non-neutral signals
        nz = [r for r in rows if r["raw"] != 0 and r["fwd"][h] != 0]
        hits = [r for r in nz if sign(r["raw"]) == sign(r["fwd"][h])]
        hit = 100.0 * len(hits) / len(nz) if nz else float("nan")
        ic = _spearman([r["inst"] for r in rows], [r["fwd"][h] for r in rows])
        bull = [r["fwd"][h] for r in rows if r["raw"] > 0]
        bear = [r["fwd"][h] for r in rows if r["raw"] < 0]
        bull_m = sum(bull) / len(bull) if bull else float("nan")
        bear_m = sum(bear) / len(bear) if bear else float("nan")
        # long-when-bull / short-when-bear equity (sum of signed weekly fwd, non-overlapping≈approx)
        ls = sum((1 if r["raw"] > 0 else -1) * r["fwd"][h] for r in nz)
        bh = sum(r["fwd"][h] for r in rows)
        print(f"{str(h)+'w':>8} {len(rows):>4} {base:>8.1f}% {hit:>6.1f}% "
              f"{(ic if ic is not None else float('nan')):>6} "
              f"{bull_m:>9.2f} {bear_m:>9.2f} {ls:>8.1f} {bh:>7.1f}")

    # leg decomposition at 1w: which leg carries the edge?
    h = horizons_w[0]
    print(f"\n-- {h}w hit-rate by signal bucket (sign of raw) --")
    rows = [s for s in samples if s["fwd"][h] is not None and s["fwd"][h] != 0]
    for label, pred in (("bullish (raw>0)", lambda r: r["raw"] > 0),
                        ("neutral (raw==0)", lambda r: r["raw"] == 0),
                        ("bearish (raw<0)", lambda r: r["raw"] < 0)):
        b = [r for r in rows if pred(r)]
        if not b:
            print(f"  {label:<18} n=0")
            continue
        up = 100.0 * sum(1 for r in b if r["fwd"][h] > 0) / len(b)
        mean = sum(r["fwd"][h] for r in b) / len(b)
        print(f"  {label:<18} n={len(b):>3}  up={up:>5.1f}%  mean_fwd={mean:>6.2f}%")

    print("\nNote: institutional is 20/100 of the full GSS. Retail (Myfxbook) and")
    print("News have no multi-year free history, so a full-GSS 2y backtest isn't")
    print("possible without paid feeds — this isolates the leg just made real.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--horizons", default="1,2,4", help="forward weeks, comma-sep")
    a = ap.parse_args()
    run(a.years, [int(x) for x in a.horizons.split(",")])
