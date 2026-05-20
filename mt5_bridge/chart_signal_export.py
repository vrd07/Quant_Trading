#!/usr/bin/env python3
"""
Chart signal exporter — writes PLANNED trade levels to the MT5 Common Files
directory so the GoldenChart_PlanLevels.mq5 indicator can draw entry/SL/TP
lines on the chart *before* (or independent of) the order actually existing
in MT5.

This is a one-way, fire-and-forget channel separate from the command bridge:
the bot appends a signal's intended levels, the indicator polls the file and
draws them. A short TTL keeps stale plans from lingering on the chart.

File format (CSV, header row, written atomically):

    symbol,side,entry,sl,tp,label,expires_epoch
    XAUUSD,BUY,4609.5,4559.2,4630.0,kalman_regime,1747000000

Usage:
    exporter = ChartSignalExporter(ttl_minutes=30)
    exporter.add_signal("XAUUSD", "BUY", 4609.5, 4559.2, 4630.0, "kalman_regime")
"""

import os
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

FILENAME = "mt5_chart_signals.csv"
HEADER = "symbol,side,entry,sl,tp,label,expires_epoch"


class ChartSignalExporter:
    """Writes planned trade levels to a CSV the MT5 indicator polls."""

    def __init__(self, data_dir=None, ttl_minutes: int = 30, filename: str = FILENAME):
        self.ttl = timedelta(minutes=max(1, ttl_minutes))
        self.data_dir = (
            Path(data_dir).expanduser() if data_dir else self._default_mt5_path()
        )
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.path = self.data_dir / filename
        self.tmp = self.data_dir / (filename + ".tmp")
        # key (symbol, side, label) -> dict(entry, sl, tp, expires)
        self._signals = {}
        self._lock = threading.Lock()

    # -- public API ----------------------------------------------------
    def add_signal(self, symbol, side, entry, sl=0.0, tp=0.0,
                   label: str = "", ttl_minutes=None) -> None:
        """Record/refresh a planned signal and rewrite the file atomically."""
        if not symbol or entry in (None, 0, 0.0):
            return
        ttl = self.ttl if ttl_minutes is None else timedelta(minutes=max(1, ttl_minutes))
        expires = datetime.now(timezone.utc) + ttl
        key = (str(symbol), str(side).upper(), str(label))
        with self._lock:
            self._signals[key] = {
                "symbol": str(symbol),
                "side": str(side).upper(),
                "entry": float(entry or 0.0),
                "sl": float(sl or 0.0),
                "tp": float(tp or 0.0),
                "label": str(label).replace(",", " "),
                "expires": expires,
            }
            self._prune()
            self._write()

    def clear(self) -> None:
        """Drop all planned signals and truncate the file."""
        with self._lock:
            self._signals.clear()
            self._write()

    # -- internals -----------------------------------------------------
    def _prune(self) -> None:
        now = datetime.now(timezone.utc)
        dead = [k for k, v in self._signals.items() if v["expires"] <= now]
        for k in dead:
            del self._signals[k]

    def _write(self) -> None:
        lines = [HEADER]
        for v in self._signals.values():
            lines.append(
                "{symbol},{side},{entry},{sl},{tp},{label},{exp}".format(
                    symbol=v["symbol"],
                    side=v["side"],
                    entry=v["entry"],
                    sl=v["sl"],
                    tp=v["tp"],
                    label=v["label"] or v["side"],
                    exp=int(v["expires"].timestamp()),
                )
            )
        text = "\n".join(lines) + "\n"
        try:
            with open(self.tmp, "w", encoding="ascii", errors="replace") as fh:
                fh.write(text)
            os.replace(self.tmp, self.path)  # atomic on same filesystem
        except Exception:
            # Chart cosmetics must never break the trading loop.
            pass

    @staticmethod
    def _default_mt5_path() -> Path:
        """Auto-detect the MT5 Common/Files dir (mirrors MT5FileClient)."""
        home = Path.home()
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
            return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
        if sys.platform == "darwin":
            return (
                home / "Library" / "Application Support"
                / "net.metaquotes.wine.metatrader5" / "drive_c" / "users"
                / "user" / "AppData" / "Roaming" / "MetaQuotes"
                / "Terminal" / "Common" / "Files"
            )
        return (
            home / ".wine" / "drive_c" / "users" / "user" / "AppData"
            / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
        )


if __name__ == "__main__":
    # Smoke test: write one demo signal so you can verify the file path.
    exp = ChartSignalExporter(ttl_minutes=30)
    exp.add_signal("XAUUSD", "BUY", 4609.5, 4559.2, 4630.0, "demo")
    print(f"Wrote demo signal to: {exp.path}")
