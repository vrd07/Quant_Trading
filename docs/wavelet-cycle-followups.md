# Wavelet cycle work — open items

Parked 2026-08-09. Everything below is optional; nothing here is blocking live
trading, and no live strategy changed behaviour in this work.

## Where things stand

Three branches landed and are on `origin/main`:

| Commit | What |
|---|---|
| `5c2c26d` | Dynamic per-trade time stop, wired live + backtest |
| `33545ee` | Scale-invariance test — **falsifies the wavelet cycle premise** |
| `b8cdeea` | Root-cause of the dead MEAN_REVERT branch (docs only) |

**The wavelet strategy is closed, not paused.** A pre-registered test with a
passing positive control showed gold's cycle prominence (1.27/1.84/1.65 at
windows 96/192/384) is *below a pure random walk's* (1.30/1.87/1.69), while an
injected 30-bar cycle scores 21.8–76.0. `wavelet_cycle` stays `enabled: false`
at weight 0.00. The frozen OOS slice (2024-07→2025-01) was never spent and is
still clean for some future, different hypothesis.

⛔ **Do not reopen the wavelet strategy itself.** Do not repair the three known
measurement defects (dead `entry_dev_atr` axis, median-collapsed stops, 14-trade
validate sample) — they are measurement problems around a signal the data says
is absent. Do not loosen the MEAN_REVERT regime gate to "unblock" the branch:
phase and deviation sign are independent (0.47/0.53), so the entries it unlocks
are coin flips paying a spread.

Reproducers: `scripts/research_wavelet_scale.py` (add `--render-only` to rebuild
the report from the saved CSV without a ~10 min re-sweep) and
`scripts/research_wavelet_meanrevert_funnel.py`. Report:
`reports/wavelet_scale_invariance.md`. Design:
`docs/superpowers/specs/2026-08-09-wavelet-scale-invariance-design.md`.

---

## P1 — worth doing

### 1. `spectral.dominant_cycle` goes blind at `window >= 128`

The real defect found here, and it outlives the dead strategy. `spectral.py:151`
picks the estimator by sample size:

```python
chosen = "mesa" if (method == "mesa" or (method == "auto" and x.size < mesa_threshold)) else "fft"
```

With `mesa_threshold=128`, any window ≥ 128 silently switches to FFT — and the
FFT arm **failed to detect a textbook injected 30-bar sine** at windows 96 and
192 (prominence 0.63 and 2.47 against a 4.0 gate). It only found it at W=384.
Under FFT, gold / phase-surrogate / random-walk all returned bit-identical bin
values and 0.0% tradeable.

Likely cause: `fft_spectrum(x, segments=2)` halves the window before the
transform, so resolution is poor and the peak lands on a coarse bin.

Options: raise `fft_segments` to 1 for long windows, raise `mesa_threshold` so
MESA keeps running, or refuse to return a cycle when the estimator is
under-resolved rather than returning a bin artifact. Whatever the fix, keep
`scripts/research_wavelet_scale.py`'s `sine30` control as the acceptance test —
it must recover ≈30 at every window under both estimators.

Only matters if `src/cycles/` is kept (see P2 item 3).

### 2. Audit the shared harness's median-stop collapse — scope unknown

`scripts/backtest_kalman_2026_fixed.simulate` applies **one** `sl_pts`/`rr` to
every trade. On wavelet the real stop distances spanned 0.90–16.77pt (**18.6×**),
so that collapse made the simulated strategy materially different from the
designed one. **24 scripts import this harness**, several for shipped strategies.

Blast radius is probably small but is *not verified*:

- `research_squeeze_breakout.py`, `research_squeeze_htf_gate.py` — squeeze uses a
  **fixed 33pt SL**, so a single `sl_pts` is exact. Expected lossless; confirm
  they pass a constant rather than a median.
- `research_daily_swing_trend.py` — ATR-chandelier stops, so genuinely variable,
  but that strategy was **rejected** anyway (failed the every-year gate).
- kalman validators — kalman is budget-SL governed live; check what the research
  assumed.
- `research_stoch_pullback.py` / `research_bos_structure.py` (structural, highly
  variable stops) do **not** import this harness — different code path, no issue.

Deliverable: a one-line note per script saying "fixed stop, lossless" or
"variable stop, distorted by X×". Only chase a re-run if a *shipped* strategy
turns out to be in the second bucket.

---

## P2 — judgement calls

### 3. Delete `src/cycles/` and `wavelet_cycle`, or keep them inert?

~2,900 lines of well-tested, permanently disabled code. Precedent cuts toward
deletion: the six kill-list strategies were **deleted outright** on 2026-06-10
once proven edgeless, with git history as the archive.

Arguments to keep: the DWT / Goertzel / regime components are reusable and
carry measured, documented properties; the §8.1 hallucination gate and the
prominence-vs-power-law-background measure are genuinely hard-won.

Arguments to delete: nothing imports it outside its own tests and two research
scripts; dead code with green tests invites someone to "just enable it"; and
P1 item 1 is only worth fixing if the module stays.

If deleted, remove: `src/cycles/`, `wavelet_cycle_strategy.py`, its tests, the
`STRATEGY_WEIGHTS` entries (3 regimes), the 8 config blocks + session
whitelists + `trailing_stop.strategy_overrides.wavelet_cycle`, and
`required_core` in `test_regime_classifier.py`. Keep the reports and specs.

### 4. Sweep for other unreachable-but-green code paths

`TestMeanRevertEntries` passes on six hand-built `CycleState` fixtures while the
path it covers is reached on **0.04% of production bars**. The suite cannot see
that gap, because the fixtures manufacture the very state the branch needs.

Worth asking of other multi-branch strategies: does each branch actually fire on
real data? A cheap version is a per-branch signal counter over a replay for
`kalman_regime` (TREND vs RANGE), `confluence_gate` (COMBO A/B/C), and any
strategy with a regime switch. Related to `project_plumbing_null_trap`.

---

## P3 — minor

5. **Dynamic time-stop registration is in-memory.** After a restart an open
   position falls back to the configured ceiling. Safe direction, and moot while
   nothing enabled publishes `time_stop_minutes`. Persist via `StateManager` only
   if a strategy using it ever goes live.
6. **The rule now applies to every strategy.** `min(published, configured)` is
   live for all of them. Nothing publishes the key today, but any strategy that
   starts to will have it take effect immediately.
7. **Cosmetic:** merge commit `33545ee` has literal `\n` in its body (used
   `git commit -m` instead of `-F`). Fixing needs a force-push of pushed history
   — not worth it. Use `git commit -F` for multi-line messages in this repo.
8. **Macro-correlation proxy never runs live.** `set_proxy_series` wants inverted
   EURUSD, which is not a configured symbol; live it degrades to pass-through.
   Moot if item 3 resolves to delete.
