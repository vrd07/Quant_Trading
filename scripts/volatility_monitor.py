#!/usr/bin/env python3
"""
Real-time London/NY-open volatility monitor — "Beast mode" scalp alerts.

Polls the EA status file (mt5_status.json) once a second, builds 1m mid-price
bars per symbol, and fires an alert when a symbol's volatility expansion +
momentum burst + spread all line up inside a session-open window. Detection
logic lives in src/monitoring/volatility_monitor.py (pure, unit-tested).

IMPORTANT — data path: this reads ONLY the status file the EA rewrites every
second. It never touches the command/response channel, so it is safe to run
alongside the live bot (the bridge command channel is single-owner). For the
non-chart symbols to appear in the status quotes, set the EA input
WatchSymbols (e.g. "USDJPYs,GBPUSDs,AUDUSDs") and recompile/reattach —
otherwise only the chart symbol (+ open-position symbols) can be monitored.

Usage:
    python scripts/volatility_monitor.py                       # symbols from ACTIVE_CONFIG
    python scripts/volatility_monitor.py --symbols XAUUSD,USDJPY
    python scripts/volatility_monitor.py --all-hours           # ignore session windows (testing)
    python scripts/volatility_monitor.py --telegram            # also push alerts to Telegram
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mt5_bridge"))

from src.monitoring.volatility_monitor import (  # noqa: E402
    BEAST,
    HOT,
    OFF_SESSION,
    QUIET,
    WARMING,
    AlertGovernor,
    BeastConfig,
    MinuteBarBuilder,
    SessionWindow,
    evaluate,
    utcnow,
)

ALERT_LOG = REPO_ROOT / "data" / "volatility_alerts.jsonl"

C_RESET, C_RED, C_YEL, C_CYN, C_DIM, C_BOLD = (
    "\033[0m", "\033[91m", "\033[93m", "\033[96m", "\033[2m", "\033[1m",
)
STATE_COLOR = {BEAST: C_RED + C_BOLD, HOT: C_YEL, QUIET: C_DIM, WARMING: C_CYN, OFF_SESSION: C_DIM}


def resolve_config(path_arg: str | None) -> Path:
    if path_arg:
        return Path(path_arg)
    marker = REPO_ROOT / "config" / "ACTIVE_CONFIG"
    rel = marker.read_text().strip()
    return REPO_ROOT / rel


def load_symbols(config_path: Path) -> dict[str, dict]:
    """Enabled symbols from the live config -> {name: {max_spread: ...}}."""
    import yaml

    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    out: dict[str, dict] = {}
    for name, block in (cfg.get("symbols") or {}).items():
        if block and block.get("enabled"):
            out[name] = {"max_spread": block.get("max_spread")}
    return out


def match_quote_key(symbol: str, quote_keys) -> str | None:
    """Map config symbol (XAUUSD) to broker quote key (XAUUSDs) by prefix."""
    if symbol in quote_keys:
        return symbol
    candidates = [k for k in quote_keys if k.startswith(symbol)]
    return min(candidates, key=len) if candidates else None


def notify_macos(title: str, body: str) -> None:
    try:
        script = f'display notification "{body}" with title "{title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception:
        pass


def fire_alert(symbol: str, verdict, price: float, spread: float, use_telegram: bool) -> None:
    ts = utcnow().strftime("%H:%M:%S")
    reasons = "; ".join(verdict.reasons)
    line = (f"\a{C_RED}{C_BOLD}🔥 BEAST MODE {verdict.direction} {symbol} @ {price:.5g} "
            f"[{verdict.session}] {reasons}{C_RESET}")
    print(line)

    notify_macos(f"🔥 BEAST {verdict.direction} {symbol}",
                 f"{verdict.session} @ {price:.5g} — {reasons}")

    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG, "a") as f:
        f.write(json.dumps({
            "ts": utcnow().isoformat(),
            "symbol": symbol,
            "direction": verdict.direction,
            "session": verdict.session,
            "price": price,
            "spread": spread,
            "range_ratio": verdict.range_ratio,
            "momentum_atr": verdict.momentum_atr,
            "spread_frac": verdict.spread_frac,
        }) + "\n")

    if use_telegram:
        try:
            from src.sentiment.notify import notify_text
            arrow = "🟢" if verdict.direction == "BUY" else "🔴"
            sent = notify_text(
                f"{arrow} <b>BEAST MODE — {verdict.direction} {symbol}</b> [{verdict.session}]\n"
                f"price {price:.5g} · {reasons}\n"
                f"<i>{ts} UTC — scalp alert, not auto-executed.</i>")
            if not sent:
                print(f"{C_DIM}(telegram not configured or send failed){C_RESET}")
        except Exception as e:
            print(f"{C_DIM}(telegram error: {e}){C_RESET}")


def render(rows: list[dict], stale: bool, sessions, now) -> None:
    sys.stdout.write("\033[2J\033[H")
    sess = next((s.name for s in sessions if s.contains(now)), None)
    hdr = f"{C_BOLD}⚡ VOLATILITY MONITOR{C_RESET}  {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    hdr += f"  session: {sess or '—'}"
    if stale:
        hdr += f"  {C_RED}{C_BOLD}[STALE FEED — EA not updating]{C_RESET}"
    print(hdr)
    print(f"{C_DIM}{'SYMBOL':<10}{'PRICE':>12}{'SPREAD':>10}{'RANGE×':>8}{'MOM(ATR)':>10}"
          f"{'SPR/ATR':>9}  STATE{C_RESET}")
    for r in rows:
        v = r["verdict"]
        color = STATE_COLOR.get(v.state, "") if v else C_DIM
        state = v.state if v else ("COLLECTING" if r.get("price") is not None else "NO FEED")
        if v and v.state == BEAST:
            state = f"🔥 BEAST {v.direction}"
        elif v and v.state == HOT and v.direction:
            state = f"HOT ({v.direction} bias)"
        fmt = lambda x, p: f"{x:.{p}f}" if x is not None else "—"
        print(f"{r['symbol']:<10}{fmt(r.get('price'), 5):>12}{fmt(r.get('spread'), 5):>10}"
              f"{fmt(v.range_ratio if v else None, 2):>8}"
              f"{fmt(v.momentum_atr if v else None, 2):>10}"
              f"{fmt(v.spread_frac if v else None, 2):>9}  {color}{state}{C_RESET}")
    print(f"\n{C_DIM}beast = range expansion + momentum burst + tight spread inside "
          f"London/NY open · ctrl-c to quit{C_RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description="London/NY-open Beast-mode volatility monitor")
    ap.add_argument("--config", help="live config yaml (default: config/ACTIVE_CONFIG)")
    ap.add_argument("--symbols", help="comma-separated override, e.g. XAUUSD,USDJPY")
    ap.add_argument("--interval", type=float, default=1.0, help="poll seconds (default 1)")
    ap.add_argument("--all-hours", action="store_true", help="ignore session windows (testing)")
    ap.add_argument("--telegram", action="store_true", help="push alerts to Telegram too")
    ap.add_argument("--headless", action="store_true",
                    help="no live table — log alerts only (for start_live.sh background use)")
    ap.add_argument("--baseline-bars", type=int, default=30)
    ap.add_argument("--expansion", type=float, default=2.0, help="range expansion multiple")
    ap.add_argument("--momentum-mult", type=float, default=1.5, help="momentum threshold in ATRs")
    ap.add_argument("--momentum-bars", type=int, default=3)
    ap.add_argument("--cooldown", type=float, default=300.0, help="per-symbol alert cooldown sec")
    args = ap.parse_args()

    config_path = resolve_config(args.config)
    symbol_cfg = load_symbols(config_path)
    if args.symbols:
        names = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        symbol_cfg = {n: symbol_cfg.get(n, {"max_spread": None}) for n in names}
    if not symbol_cfg:
        print("No symbols to monitor.")
        return 1

    sessions = (SessionWindow("ALL", 0, 24 * 60),) if args.all_hours else BeastConfig().sessions
    cfg = BeastConfig(
        baseline_bars=args.baseline_bars,
        range_expansion_mult=args.expansion,
        momentum_bars=args.momentum_bars,
        momentum_atr_mult=args.momentum_mult,
        cooldown_sec=args.cooldown,
        sessions=sessions,
    )
    governor = AlertGovernor(cfg.cooldown_sec)

    if args.headless:
        # nohup pipes stdout to a log file — line-buffer so alerts land immediately
        sys.stdout.reconfigure(line_buffering=True)

    from mt5_file_client import MT5FileClient
    client = MT5FileClient()

    builders = {s: MinuteBarBuilder(max_bars=cfg.baseline_bars * 4) for s in symbol_cfg}
    verdicts: dict[str, object] = {s: None for s in symbol_cfg}
    last_px: dict[str, dict] = {}
    last_mtime = 0.0
    was_stale = False
    missing_warned: set[str] = set()

    print(f"Monitoring {', '.join(symbol_cfg)} from {config_path.name} "
          f"(sessions: {', '.join(s.name for s in sessions)})")

    while True:
        try:
            now = utcnow()
            try:
                mtime = client.status_file.stat().st_mtime
            except FileNotFoundError:
                mtime = 0.0
            stale = (time.time() - mtime) > max(10.0, 5 * args.interval)
            advanced = mtime > last_mtime

            if advanced and not stale:
                last_mtime = mtime
                try:
                    status = client.get_status()
                except Exception:
                    status = None
                quotes = (status or {}).get("quotes") or {}

                for sym in symbol_cfg:
                    key = match_quote_key(sym, quotes.keys())
                    if key is None:
                        if quotes and sym not in missing_warned:
                            missing_warned.add(sym)
                            print(f"{C_YEL}⚠ {sym} not in EA quotes feed — add it to the EA "
                                  f"WatchSymbols input (broker-suffixed, e.g. {sym}s){C_RESET}")
                            time.sleep(2)
                        continue
                    q = quotes[key]
                    bid, ask = float(q["bid"]), float(q["ask"])
                    if bid <= 0 or ask <= 0:
                        continue
                    mid, spread = (bid + ask) / 2.0, ask - bid
                    last_px[sym] = {"price": mid, "spread": spread}

                    completed = builders[sym].update(time.time(), mid)
                    if completed is not None:
                        v = evaluate(list(builders[sym].bars), spread, now, cfg,
                                     broker_max_spread=symbol_cfg[sym].get("max_spread"))
                        verdicts[sym] = v
                        if v.triggered and governor.should_fire(sym, v.direction, time.time()):
                            fire_alert(sym, v, mid, spread, args.telegram)
                            if not args.headless:
                                time.sleep(2)  # let the banner be seen before redraw

            if args.headless:
                if stale != was_stale:
                    state = "STALE — EA not updating" if stale else "recovered"
                    print(f"[{now.strftime('%H:%M:%S')} UTC] feed {state}")
                    was_stale = stale
            else:
                rows = [{"symbol": s, "verdict": verdicts[s], **last_px.get(s, {})}
                        for s in symbol_cfg]
                render(rows, stale, sessions, now)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nbye")
            return 0


if __name__ == "__main__":
    sys.exit(main())
