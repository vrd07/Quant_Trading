#!/usr/bin/env python3
"""
Weekly health + performance report (Saturday review).

Gathers three things into ONE concise markdown report:
  1. ML regime-classifier health (per-symbol: model, confidence, samples, staleness)
  2. This week's trades from the ACTIVE config's journal (Mon-Sat), with a
     self-verification line so the numbers can be trusted against the raw CSV
  3. Week-over-week + multi-week trend — "are ML and strategies improving?"

It also APPENDS a one-row snapshot to data/logs/weekly_metrics_history.csv so
each Saturday can compare against prior weeks (the trend that answers
"are we improving?"). Designed to be run unattended by the Saturday scheduler.

Usage:
    python scripts/weekly_report.py                 # current week, active config
    python scripts/weekly_report.py --week-offset 1 # last completed week
    python scripts/weekly_report.py --no-history     # don't append to history
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
# docs/weekly/ is git-tracked (reports/ is gitignored) so the Saturday PR can
# commit the report into history. The metrics-history CSV stays under data/logs
# (gitignored, local runtime state) — only the rendered report is committed.
REPORT_DIR = PROJECT_ROOT / "docs" / "weekly"
HISTORY_CSV = LOG_DIR / "weekly_metrics_history.csv"

# Staleness / viability thresholds (mirror check_regime_health.py).
STALE_HOURS = 36.0
ML_VIABLE_MIN_BARS = 12_000


# ── config / journal resolution ──────────────────────────────────────────
def active_config_stem() -> str:
    marker = PROJECT_ROOT / "config" / "ACTIVE_CONFIG"
    try:
        path = marker.read_text().strip().splitlines()[0].strip()
        return Path(path).stem  # e.g. config_live_1000
    except (OSError, IndexError):
        return "config_live_1000"


def find_journal(stem: str) -> Path | None:
    """The per-config namespaced journal; fall back to the legacy one."""
    cand = LOG_DIR / f"trade_journal_{stem}.csv"
    if cand.exists():
        return cand
    legacy = LOG_DIR / "trade_journal.csv"
    return legacy if legacy.exists() else None


def week_bounds(offset: int = 0):
    """(start, end) for Mon 00:00 -> Sat 23:59:59 UTC of the requested week."""
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=offset)
    start = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=5, hours=23, minutes=59, seconds=59)
    return start, end


# ── journal metrics ────────────────────────────────────────────────────────
def load_window(journal: Path, start, end) -> pd.DataFrame:
    df = pd.read_csv(journal)
    if df.empty:
        return df
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True, errors="coerce")
    df = df.dropna(subset=["exit_time"])
    return df[(df["exit_time"] >= start) & (df["exit_time"] <= end)].copy()


def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(trades=0, wins=0, losses=0, win_rate=0.0, net=0.0,
                    pf=0.0, expectancy=0.0, sum_r=0.0, manual_net=0.0,
                    bot_net=0.0, best=0.0, worst=0.0)
    pnl = df["realized_pnl"].astype(float)
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    gross_win = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    is_manual = df["strategy"].astype(str).str.lower().eq("manual")
    r = df.copy()
    r["initial_risk"] = pd.to_numeric(r["initial_risk"], errors="coerce")
    valid_r = r[r["initial_risk"].fillna(0) > 0]
    sum_r = float((valid_r["realized_pnl"].astype(float) / valid_r["initial_risk"]).sum()) if len(valid_r) else 0.0
    n = len(df)
    return dict(
        trades=n, wins=wins, losses=losses,
        win_rate=round(100.0 * wins / n, 1) if n else 0.0,
        net=round(float(pnl.sum()), 2),
        pf=round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        expectancy=round(float(pnl.mean()), 2),
        sum_r=round(sum_r, 2),
        manual_net=round(float(pnl[is_manual].sum()), 2),
        bot_net=round(float(pnl[~is_manual].sum()), 2),
        best=round(float(pnl.max()), 2),
        worst=round(float(pnl.min()), 2),
    )


def by_strategy(df: pd.DataFrame) -> list[tuple]:
    if df.empty:
        return []
    out = []
    for name, grp in df.groupby("strategy"):
        pnl = grp["realized_pnl"].astype(float)
        out.append((str(name), len(grp), int((pnl > 0).sum()), round(float(pnl.sum()), 2)))
    return sorted(out, key=lambda x: x[3])  # worst-first


# ── ML health ────────────────────────────────────────────────────────────
def ml_health() -> list[dict]:
    rows = []
    now = datetime.now(timezone.utc)
    for p in sorted(DATA_DIR.glob("config_override_*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sym = d.get("symbol", p.stem.replace("config_override_", ""))
        gen = d.get("generated_at")
        try:
            age_h = (now - datetime.fromisoformat(gen)).total_seconds() / 3600.0 if gen else None
        except ValueError:
            age_h = None
        clf = d.get("classifier", "?")
        m = re.search(r"n=(\d+)", clf)
        samples = int(m.group(1)) if m else 0
        hist = DATA_DIR / "historical" / f"{sym}_5m_real.csv"
        bars = (sum(1 for _ in hist.open()) - 1) if hist.exists() else 0
        is_ml = "RandomForest" in clf or "GradientBoost" in clf
        rows.append(dict(
            symbol=sym, regime=d.get("regime", "?"),
            confidence=round(float(d.get("confidence", 0)) * 100, 0),
            classifier=clf, samples=samples, bars=bars,
            age_h=round(age_h, 1) if age_h is not None else None,
            stale=(age_h is not None and age_h > STALE_HOURS),
            degraded=(not is_ml and bars >= ML_VIABLE_MIN_BARS),
            is_ml=is_ml,
            perf=d.get("performance_scores", {}),
        ))
    return rows


def primary_ml(rows: list[dict], symbols=("XAUUSD",)) -> dict | None:
    for s in symbols:
        for r in rows:
            if r["symbol"] == s:
                return r
    return rows[0] if rows else None


# ── history (the improvement trend) ────────────────────────────────────────
def append_history(week_start, cfg_stem, m, pml):
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "week_start": week_start.date().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": cfg_stem,
        "trades": m["trades"], "net": m["net"], "win_rate": m["win_rate"],
        "pf": m["pf"] if m["pf"] != float("inf") else "",
        "sum_r": m["sum_r"], "manual_net": m["manual_net"], "bot_net": m["bot_net"],
        "xau_ml_conf": pml["confidence"] if pml else "",
        "xau_ml_samples": pml["samples"] if pml else "",
        "xau_classifier": pml["classifier"] if pml else "",
    }
    hist = pd.read_csv(HISTORY_CSV) if HISTORY_CSV.exists() else pd.DataFrame()
    # idempotent: replace any existing snapshot for this (week_start, config)
    if not hist.empty:
        hist = hist[~((hist["week_start"] == row["week_start"]) & (hist["config"] == cfg_stem))]
    hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True)
    hist = hist.sort_values("week_start").reset_index(drop=True)
    hist.to_csv(HISTORY_CSV, index=False)
    return hist


def trend_block(hist: pd.DataFrame, cfg_stem: str, weeks: int = 6) -> list[str]:
    if hist.empty:
        return ["  (no history yet — this is the first snapshot)"]
    h = hist[hist["config"] == cfg_stem].tail(weeks)
    lines = [f"  {'Week':<12}{'Trades':>7}{'Net':>10}{'ΣR':>8}{'ML conf':>9}{'ML n':>7}"]
    for _, r in h.iterrows():
        conf = f"{r['xau_ml_conf']:.0f}%" if pd.notna(r.get("xau_ml_conf")) and r.get("xau_ml_conf") != "" else "—"
        n = int(r["xau_ml_samples"]) if pd.notna(r.get("xau_ml_samples")) and r.get("xau_ml_samples") != "" else 0
        lines.append(f"  {r['week_start']:<12}{int(r['trades']):>7}{float(r['net']):>+10.2f}"
                     f"{float(r['sum_r']):>+8.2f}{conf:>9}{n:>7}")
    return lines


# ── report ───────────────────────────────────────────────────────────────
def build_report(offset: int, write_history: bool) -> str:
    cfg_stem = active_config_stem()
    start, end = week_bounds(offset)
    journal = find_journal(cfg_stem)
    L = []
    w = L.append

    w(f"# Weekly Report — {start.date()} → {end.date()}")
    w(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · config `{cfg_stem}`_\n")

    # --- gather ---
    mlrows = ml_health()
    pml = primary_ml(mlrows)
    if journal is None:
        w("> ⚠️ No journal file found — cannot report trades.\n")
        dfw = pd.DataFrame()
        m = metrics(dfw)
    else:
        dfw = load_window(journal, start, end)
        m = metrics(dfw)

    # --- STATUS verdict ---
    flags = []
    if any(r["stale"] for r in mlrows):
        n_stale = sum(r["stale"] for r in mlrows)
        flags.append(f"ML overrides STALE on {n_stale} symbol(s) (>{STALE_HOURS:.0f}h) — nightly classifier not refreshing")
    if any(r["degraded"] for r in mlrows):
        degs = ", ".join(r["symbol"] for r in mlrows if r["degraded"])
        flags.append(f"ML degraded to rule-based despite enough data: {degs}")
    if m["manual_net"] < 0:
        flags.append(f"Manual trades net {m['manual_net']:+.2f} — discretionary clicks losing money")
    if m["trades"] == 0:
        flags.append("Zero trades this week — verify bot is running and signals firing")
    if isinstance(m["pf"], float) and m["pf"] != float("inf") and m["pf"] < 1.0 and m["trades"] > 0:
        flags.append(f"Profit factor {m['pf']:.2f} < 1.0 — week is net-losing")

    status = "🔴 ACTION NEEDED" if flags else "🟢 OK"
    w(f"## Status: {status}")
    if flags:
        for f in flags:
            w(f"- ⚠️ {f}")
    else:
        w("- All systems nominal.")
    w("")

    # --- ML HEALTH ---
    w("## ML Regime Classifier")
    w("```")
    for r in mlrows:
        tag = "ML " if r["is_ml"] else "rule"
        age = f"{r['age_h']:.0f}h" if r["age_h"] is not None else "?"
        mark = "STALE" if r["stale"] else ("DEGRADED" if r["degraded"] else "ok")
        w(f"  {r['symbol']:<9}{r['regime']:<8}conf={r['confidence']:.0f}%  "
          f"{tag} n={r['samples']:<4} bars={r['bars']:<7} age={age:<6} [{mark}]")
    w("```")
    if pml and pml["perf"]:
        scores = ", ".join(f"{k}={v:+.4f}" for k, v in pml["perf"].items())
        w(f"- {pml['symbol']} per-strategy performance scores: {scores}")
    w("")

    # --- THIS WEEK ---
    w("## Trades This Week (Mon–Sat)")
    pf_str = "∞" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    w(f"- **{m['trades']} trades** · Net **{m['net']:+.2f}** · Win {m['win_rate']:.0f}% "
      f"({m['wins']}W/{m['losses']}L) · PF {pf_str} · ΣR {m['sum_r']:+.2f}")
    if m["trades"]:
        w(f"- Bot {m['bot_net']:+.2f} vs Manual {m['manual_net']:+.2f} · "
          f"Best {m['best']:+.2f} / Worst {m['worst']:+.2f}")
        w(f"- **Verification:** counted {m['trades']} closed trades in "
          f"`{journal.name}` with exit_time in [{start.date()} … {end.date()}].")
        w("\n  | Strategy | Trades | Wins | Net |")
        w("  |---|---:|---:|---:|")
        for name, n, wn, net in by_strategy(dfw):
            w(f"  | {name} | {n} | {wn} | {net:+.2f} |")
    w("")

    # --- history + trend ---
    if write_history:
        hist = append_history(start, cfg_stem, m, pml)
    else:
        hist = pd.read_csv(HISTORY_CSV) if HISTORY_CSV.exists() else pd.DataFrame()

    w("## Are We Improving? (trend)")
    w("```")
    for line in trend_block(hist, cfg_stem):
        w(line)
    w("```")
    # improvement read
    h = hist[hist["config"] == cfg_stem] if not hist.empty else hist
    if len(h) >= 2:
        prev, cur = h.iloc[-2], h.iloc[-1]
        dnet = float(cur["net"]) - float(prev["net"])
        dr = float(cur["sum_r"]) - float(prev["sum_r"])
        verdict = "improving ✅" if dr > 0 else ("flat ➖" if dr == 0 else "declining ❌")
        w(f"- Week-over-week: Net Δ{dnet:+.2f}, ΣR Δ{dr:+.2f} → **{verdict}**")
        if pml and str(prev.get("xau_ml_samples", "")) not in ("", "nan"):
            try:
                dn = pml["samples"] - int(float(prev["xau_ml_samples"]))
                w(f"- ML training data Δ{dn:+d} samples "
                  f"({'growing ✅' if dn > 0 else 'not growing ⚠️'})")
            except (ValueError, TypeError):
                pass
    else:
        w("- Need ≥2 weekly snapshots to judge a trend; baseline recorded.")
    w("")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Weekly health + performance report")
    ap.add_argument("--week-offset", type=int, default=0,
                    help="0=current week, 1=last completed week")
    ap.add_argument("--no-history", action="store_true",
                    help="do not append a snapshot to weekly_metrics_history.csv")
    args = ap.parse_args()

    report = build_report(args.week_offset, write_history=not args.no_history)
    start, _ = week_bounds(args.week_offset)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"weekly_report_{start.date()}.md"
    out.write_text(report)
    print(report)
    print(f"\n[report saved to {out.relative_to(PROJECT_ROOT)}]")


if __name__ == "__main__":
    main()
