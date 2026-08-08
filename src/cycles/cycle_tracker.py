"""Orchestrates DWT -> spectral estimate -> Goertzel, and applies the §4.3 gates.

This is the only object in src/cycles that carries state (the rolling history of
cycle-length estimates needed for the stability gate). The state is explicit and
resettable so a replay produces identical output every run.

DEPARTURE FROM SPEC: `min_prominence` defaults to 4.0, not the spec §4.3 value of
2.0. At 2.0 the §8.1 hallucination gate failed outright -- pink noise passed as
"tradeable" on 61% of bars and ARMA(2,1) on 41%, against a 30% ceiling. The
prominence measure was rebuilt first (see spectral.dominant_cycle: scored against
the spectrum's own power-law background rather than the band mean, which is what
made a pure random walk score >= 2.0 on 40 of 40 trials). That alone was not
enough, so the threshold was raised per the escalation order in the plan.
Measured tradeable rates:

    threshold   pink    arma    clean 32-bar cycle   real 15m XAUUSD
    2.0         61.3%   41.3%   99%                  24.7%
    3.5         29.8%   12.1%   99%                   5.3%
    4.0         24.0%    8.0%   99%                   2.8%

4.0 was chosen over 3.5 because 29.8% is a five-seed mean sitting a hair under
the 30% ceiling. Rejecting noise cost nothing on a genuine cycle (99% either
way), but note the real-data column: on actual gold this engine considers itself
able to see a cycle on under 3% of bars. Two caveats for anyone reading a
downstream backtest: that is a thin base of trades, and the median detected
period is 44 bars against a 48-bar cap, meaning the "cycle" it usually locks onto
is the low-frequency end of the search band.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.cycles.causal_dwt import CausalDWT
from src.cycles.goertzel import WINDOW_CYCLES, CyclePhase, RoofingFilter, goertzel
from src.cycles.spectral import dominant_cycle


@dataclass(frozen=True)
class CycleState:
    period: float | None
    prominence: float
    power_fraction: float
    stable: bool
    tradeable: bool
    phase: CyclePhase | None
    trend: float           # causal DWT trend at the current bar
    deviation: float       # close[t - group_delay] - trend[t]  (spec §3.2)
    raw_deviation: float   # close[t] - trend[t], for research A/B only
    group_delay: float
    bandpass: float        # roofing-filter oscillator value at the current bar
    reason: str            # "ok" | "insufficient_history" | "no_cycle"
                           # | "low_prominence" | "unstable"


# Spec §2 multi-asset configuration matrix.
_PRESETS = {
    "gold": dict(wavelet="db4", level=2, window=96, min_period=8, top_k=2),
    "btc": dict(wavelet="sym8", level=3, window=168, min_period=6, top_k=3),
}


class CycleTracker:
    def __init__(self, *, wavelet: str = "db4", level: int = 2, window: int = 96,
                 min_period: float = 8.0, max_period: float | None = None,
                 top_k: int = 2, min_prominence: float = 4.0,
                 stability_tol: float = 0.20, stability_windows: int = 5,
                 mesa_threshold: int = 128) -> None:
        self.wavelet = wavelet
        self.level = level
        self.window = window
        self.min_period = float(min_period)
        # Nothing longer than half the window is resolvable from that window.
        self.max_period = float(max_period) if max_period else window / 2.0
        self.top_k = top_k
        self.min_prominence = min_prominence
        self.stability_tol = stability_tol
        self.stability_windows = stability_windows
        self.mesa_threshold = mesa_threshold

        self.dwt = CausalDWT(wavelet=wavelet, level=level)
        self.history: deque[float] = deque(maxlen=stability_windows)

    @property
    def required_history(self) -> int:
        """Bars needed before `update` can return anything.

        Two consumers with different appetites read the same detail buffer. The
        spectral estimate wants exactly `window` samples (spec §2). The Goertzel
        probe wants WINDOW_CYCLES periods, which at the longest resolvable period
        is 3 * max_period -- 144 bars for gold against a 96-bar window. Budget for
        the larger of the two, or the phase probe silently returns a zero reading
        on exactly the long cycles the estimator is most confident about.
        """
        phase_span = int(np.ceil(WINDOW_CYCLES * self.max_period))
        return max(self.window, phase_span) + self.dwt.warmup

    @classmethod
    def for_asset(cls, asset: str, **overrides) -> "CycleTracker":
        key = asset.lower()
        if key not in _PRESETS:
            raise ValueError(f"unknown asset {asset!r}; have {sorted(_PRESETS)}")
        return cls(**{**_PRESETS[key], **overrides})

    def reset(self) -> None:
        self.history.clear()

    def _blank(self, reason: str, trend: float = float("nan")) -> CycleState:
        return CycleState(period=None, prominence=0.0, power_fraction=0.0, stable=False,
                          tradeable=False, phase=None, trend=trend,
                          deviation=float("nan"), raw_deviation=float("nan"),
                          group_delay=float("nan"), bandpass=float("nan"), reason=reason)

    def _is_stable(self) -> bool:
        """Spec §4.3: reject if the estimate has shifted >20% window-to-window."""
        if len(self.history) < self.stability_windows:
            return False
        vals = np.asarray(self.history, dtype=float)
        prev, curr = vals[:-1], vals[1:]
        return bool(np.all(np.abs(curr - prev) / np.maximum(prev, 1e-9) <= self.stability_tol))

    def update(self, close: pd.Series) -> CycleState:
        """Evaluate the last bar of `close`. Reads nothing newer than that bar."""
        need = self.required_history
        if len(close) < need:
            return self._blank("insufficient_history")

        x = close.to_numpy(dtype=float)[-need:]
        dwt_out = self.dwt.transform(x)
        trend = float(dwt_out.trend[-1])
        if not np.isfinite(trend):
            return self._blank("insufficient_history")

        # Drop the warmup head so downstream probes never see a NaN; goertzel
        # rejects any window containing one.
        detail_full = dwt_out.detail[self.dwt.warmup:]
        # Spectral estimate runs on the DETAIL (trend removed), so a drift cannot
        # masquerade as a very long cycle.
        detail = detail_full[-self.window:]
        est = dominant_cycle(detail, min_period=self.min_period,
                             max_period=self.max_period, method="auto",
                             mesa_threshold=self.mesa_threshold, top_k=self.top_k)
        if est.period is None:
            self.history.clear()
            return self._blank("no_cycle", trend=trend)

        self.history.append(est.period)
        stable = self._is_stable()

        tau = self.dwt.group_delay(est.period)
        tau_bars = int(round(tau))
        prices = close.to_numpy(dtype=float)
        raw_dev = float(prices[-1]) - trend
        dev = float(prices[-1 - tau_bars]) - trend if len(prices) > tau_bars else float("nan")

        phase = goertzel(detail_full, est.period)
        band = RoofingFilter(hp_period=2.0 * est.period,
                             ss_period=max(2.5, est.period / 2.0)).apply(detail_full)
        bandpass = float(band[-1])

        if est.prominence < self.min_prominence:
            reason = "low_prominence"
        elif not stable:
            reason = "unstable"
        else:
            reason = "ok"

        return CycleState(
            period=est.period, prominence=est.prominence,
            power_fraction=est.power_fraction, stable=stable,
            tradeable=(reason == "ok"), phase=phase, trend=trend,
            deviation=dev, raw_deviation=raw_dev, group_delay=tau,
            bandpass=bandpass, reason=reason,
        )
