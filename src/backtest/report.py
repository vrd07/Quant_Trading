"""
Report generator — backtest.md §9.

Emits the canonical report tree:

    reports/backtest_<YYYY-MM-DD>_<git-sha>/
        summary.md
        per_strategy/<strategy>.md
        ensemble.md                          (only when Phase 2 ran)
        walk_forward_metrics.parquet         (csv fallback if no parquet engine)
        trade_log.parquet                    (csv fallback)
        equity_curves.png
        failures.log

`summary.md` is the artifact that gates merges (per spec). Anything else here
is supporting material so a human reviewer can drill in.

Usage:
    ctx = ReportContext.create(output_root=Path("reports"))
    write_summary_md(ctx, results_by_strategy)
    write_per_strategy_md(ctx, "kalman_regime", result)
    write_ensemble_md(ctx, ensemble_result)
    write_trade_log(ctx, all_trades)
    write_equity_curves_png(ctx, results_by_strategy, ensemble=ensemble_result)
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import pandas as pd

from .backtest_engine import BacktestResult
from .ensemble_engine import EnsembleResult
from .tiered_retune import Gates
from .walk_forward_driver import WalkForwardDriverResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class ReportContext:
    """Where the report is written + which run it represents."""
    out_dir: Path
    timestamp: datetime
    git_sha: str

    @classmethod
    def create(
        cls,
        output_root: Path = Path("reports"),
        timestamp: Optional[datetime] = None,
        git_sha: Optional[str] = None,
    ) -> "ReportContext":
        """Make a fresh report directory `reports/backtest_<date>_<sha>/`."""
        ts = timestamp or datetime.utcnow()
        sha = git_sha or _short_git_sha()
        out_dir = Path(output_root) / f"backtest_{ts:%Y-%m-%d}_{sha}"
        (out_dir / "per_strategy").mkdir(parents=True, exist_ok=True)
        return cls(out_dir=out_dir, timestamp=ts, git_sha=sha)


def _short_git_sha() -> str:
    """Best-effort short SHA. Returns 'nosha' outside a repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip() or "nosha"
    except Exception:
        return "nosha"


# ---------------------------------------------------------------------------
# §1 gate evaluation (re-uses Gates from tiered_retune)
# ---------------------------------------------------------------------------

def _gate_status(result: BacktestResult, gates: Gates,
                 oos_days: int = 365) -> Dict[str, bool]:
    """Evaluate G1..G6 directly from a BacktestResult."""
    return gates.evaluate(result, oos_days)


def _gates_pass_count(status: Dict[str, bool]) -> str:
    n_pass = sum(1 for v in status.values() if v)
    return f"{n_pass}/{len(status)}"


# ---------------------------------------------------------------------------
# summary.md (the merge gate)
# ---------------------------------------------------------------------------

def write_summary_md(
    ctx: ReportContext,
    results: Mapping[str, BacktestResult],
    *,
    gates: Optional[Gates] = None,
    config_path: Optional[str] = None,
) -> Path:
    """Top-level summary — one row per strategy, gates G1..G6 spelled out."""
    gates = gates or Gates()
    path = ctx.out_dir / "summary.md"
    rows: List[str] = []
    for name, r in results.items():
        st = _gate_status(r, gates)
        rows.append(
            f"| {name} | {r.total_trades} | {r.win_rate:.1f}% | {r.profit_factor:.2f} | "
            f"{r.sharpe_ratio:.2f} | {abs(r.max_drawdown_pct):.2f}% | "
            f"{r.daily_win_rate:.0%} | {r.worst_day_r:.2f}R | {_gates_pass_count(st)} |"
        )

    md: List[str] = [
        f"# Backtest Summary — {ctx.timestamp:%Y-%m-%d %H:%M}Z",
        "",
        f"- **Run SHA:** `{ctx.git_sha}`",
        f"- **Config:** `{config_path or 'unknown'}`",
        f"- **Strategies graded:** {len(results)}",
        "",
        "## Gate breakdown (backtest.md §1)",
        "",
        "| Strategy | Trades | WinRate | PF | Sharpe | MaxDD | DailyWR | WorstR | Gates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|:-:|",
        *rows,
        "",
        "## Pass/fail",
        "",
    ]

    for name, r in results.items():
        st = _gate_status(r, gates)
        all_pass = all(st.values())
        verdict = "✅ PASS" if all_pass else "❌ FAIL"
        md.append(f"- **{name}** — {verdict}")
        for gate, ok in st.items():
            md.append(f"  - {'✅' if ok else '❌'} {gate}")

    path.write_text("\n".join(md))
    log.info(f"Wrote {path}")
    return path


# ---------------------------------------------------------------------------
# per_strategy/<name>.md
# ---------------------------------------------------------------------------

def write_per_strategy_md(
    ctx: ReportContext,
    strategy: str,
    result: BacktestResult,
    *,
    gates: Optional[Gates] = None,
    walk_forward: Optional[WalkForwardDriverResult] = None,
) -> Path:
    """Full metrics for one strategy + walk-forward summary if available."""
    gates = gates or Gates()
    path = ctx.out_dir / "per_strategy" / f"{strategy}.md"
    st = _gate_status(result, gates)
    md: List[str] = [
        f"# {strategy}",
        "",
        f"Run: `{ctx.git_sha}` · {ctx.timestamp:%Y-%m-%d %H:%M}Z",
        "",
        "## Aggregate",
        "",
        f"- Trades: **{result.total_trades}** "
        f"({result.winning_trades}W / {result.losing_trades}L, "
        f"{result.win_rate:.1f}% win-rate)",
        f"- Total return: **${result.total_return:,.2f}** "
        f"({result.total_return_pct:+.2f}%)",
        f"- Sharpe / Sortino: **{result.sharpe_ratio:.2f}** / {result.sortino_ratio:.2f}",
        f"- Profit factor: **{result.profit_factor:.2f}**",
        f"- Max drawdown: **{abs(result.max_drawdown_pct):.2f}%** (${result.max_drawdown:,.2f})",
        f"- Expectancy / trade: **${result.expectancy:.2f}**",
        f"- Avg win / loss: ${result.avg_win:,.2f} / ${result.avg_loss:,.2f}",
        f"- Largest win / loss: ${result.largest_win:,.2f} / ${result.largest_loss:,.2f}",
        "",
        "## Daily metrics (G1, G2)",
        "",
        f"- Daily win-rate: **{result.daily_win_rate:.0%}** (G1 ≥ 70%)",
        f"- Worst day: **{result.worst_day_r:.2f}R** (G2 ≥ -2.0R)",
        f"- Trading days: {result.trading_days}",
        "",
        "## Gate status",
        "",
    ]
    for gate, ok in st.items():
        md.append(f"- {'✅' if ok else '❌'} {gate}")

    if walk_forward is not None and walk_forward.windows:
        md += [
            "",
            "## Walk-forward (backtest.md §5.1)",
            "",
            f"- Windows: {walk_forward.n_windows}",
            f"- TieredRetune passed: {walk_forward.n_passed}/{walk_forward.n_windows}",
            f"- OOS green %: {walk_forward.oos_profitable_window_pct:.0%}",
            f"- Worst day across all OOS slices: {walk_forward.worst_oos_day_r:.2f}R",
            f"- Avg OOS PF: {walk_forward.avg_oos_pf:.2f}",
            f"- Avg OOS Sharpe: {walk_forward.avg_oos_sharpe:.2f}",
        ]
        # Param stability snapshot
        stability = walk_forward.parameter_stability()
        if stability:
            md += ["", "### Parameter stability (§5.3)", ""]
            for k, frac in sorted(stability.items()):
                ok = frac >= 0.80
                md.append(f"- {'✅' if ok else '⚠️'} `{k}`: {frac:.0%} of windows within ±20% of median")

    path.write_text("\n".join(md))
    log.info(f"Wrote {path}")
    return path


# ---------------------------------------------------------------------------
# ensemble.md
# ---------------------------------------------------------------------------

def write_ensemble_md(ctx: ReportContext, result: EnsembleResult,
                      *, gates: Optional[Gates] = None) -> Path:
    """Phase 2 ensemble result — the production gate."""
    gates = gates or Gates()
    path = ctx.out_dir / "ensemble.md"
    agg = result.aggregate
    st = _gate_status(agg, gates)

    md: List[str] = [
        "# Ensemble (Phase 2 — production gate)",
        "",
        f"Run: `{ctx.git_sha}` · {ctx.timestamp:%Y-%m-%d %H:%M}Z",
        "",
        "## Aggregate",
        "",
        f"- Total return: **${agg.total_return:,.2f}** ({agg.total_return_pct:+.2f}%)",
        f"- Sharpe: **{agg.sharpe_ratio:.2f}** | Sortino: {agg.sortino_ratio:.2f}",
        f"- Profit factor: **{agg.profit_factor:.2f}**",
        f"- Daily win-rate: **{agg.daily_win_rate:.0%}** (G1)",
        f"- Worst day: **{agg.worst_day_r:.2f}R** (G2)",
        f"- Max drawdown: **{abs(agg.max_drawdown_pct):.2f}%**",
        f"- Trades: {agg.total_trades} ({agg.winning_trades}W / {agg.losing_trades}L, "
        f"{agg.win_rate:.1f}%)",
        "",
        "## Gate status",
        "",
    ]
    for gate, ok in st.items():
        md.append(f"- {'✅' if ok else '❌'} {gate}")

    md += [
        "",
        "## Per-strategy attribution (§6 Phase 3 ablation candidate)",
        "",
        "| Strategy | Trades | Wins | WinRate | P&L |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, attr in sorted(result.per_strategy.items(),
                             key=lambda kv: kv[1].gross_pnl, reverse=True):
        md.append(f"| {name} | {attr.trades} | {attr.wins} | "
                  f"{attr.win_rate:.1%} | ${attr.gross_pnl:+,.2f} |")

    path.write_text("\n".join(md))
    log.info(f"Wrote {path}")
    return path


# ---------------------------------------------------------------------------
# Parquet / CSV (with fallback)
# ---------------------------------------------------------------------------

def _write_table(df: pd.DataFrame, path: Path) -> Path:
    """Write parquet, fall back to CSV if no engine. Returns the actual path."""
    try:
        df.to_parquet(path, index=False)
        return path
    except Exception as e:
        log.warning(f"parquet write failed ({e}); falling back to CSV")
        csv_path = path.with_suffix(".csv")
        df.to_csv(csv_path, index=False)
        return csv_path


def write_walk_forward_metrics(
    ctx: ReportContext,
    walk_forward: WalkForwardDriverResult,
) -> Optional[Path]:
    """Per-window metrics table — replayable for re-analysis."""
    if not walk_forward.windows:
        return None
    rows = []
    for w in walk_forward.windows:
        oos = w.oos_result
        rows.append({
            "window_idx": w.spec.idx,
            "is_start": str(w.spec.is_start),
            "is_end": str(w.spec.is_end),
            "oos_start": str(w.spec.oos_start),
            "oos_end": str(w.spec.oos_end),
            "tier": w.retune.tier,
            "passed": w.retune.passed,
            "n_combos": w.retune.n_combos_evaluated,
            "oos_total_return": float(oos.total_return) if oos else None,
            "oos_sharpe": float(oos.sharpe_ratio) if oos else None,
            "oos_pf": float(oos.profit_factor) if oos else None,
            "oos_daily_win_rate": float(oos.daily_win_rate) if oos else None,
            "oos_worst_day_r": float(oos.worst_day_r) if oos else None,
            "oos_max_dd_pct": float(oos.max_drawdown_pct) if oos else None,
            "oos_total_trades": int(oos.total_trades) if oos else None,
            "winning_params_json": str(w.retune.winning_params),
        })
    return _write_table(pd.DataFrame(rows), ctx.out_dir / "walk_forward_metrics.parquet")


def write_trade_log(
    ctx: ReportContext,
    trades: List[Dict],
) -> Optional[Path]:
    """Every simulated trade, full enough to replay."""
    if not trades:
        return None
    return _write_table(pd.DataFrame(trades), ctx.out_dir / "trade_log.parquet")


# ---------------------------------------------------------------------------
# equity_curves.png
# ---------------------------------------------------------------------------

def write_equity_curves_png(
    ctx: ReportContext,
    results: Mapping[str, BacktestResult],
    *,
    ensemble: Optional[EnsembleResult] = None,
) -> Optional[Path]:
    """One curve per strategy, plus the ensemble curve overlaid in bold."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        log.warning(f"matplotlib unavailable ({e}); skipping equity_curves.png")
        return None

    fig, ax = plt.subplots(figsize=(11, 6))
    drew_anything = False
    for name, r in results.items():
        if r.equity_curve is None or len(r.equity_curve) < 2:
            continue
        ax.plot(r.equity_curve.index, r.equity_curve.values, label=name, alpha=0.7, linewidth=1.0)
        drew_anything = True
    if ensemble is not None:
        ec = ensemble.aggregate.equity_curve
        if ec is not None and len(ec) >= 2:
            ax.plot(ec.index, ec.values, label="ENSEMBLE", linewidth=2.4, color="black")
            drew_anything = True
    if not drew_anything:
        plt.close(fig)
        return None
    ax.set_title(f"Equity curves — {ctx.timestamp:%Y-%m-%d} ({ctx.git_sha})")
    ax.set_xlabel("Time")
    ax.set_ylabel("Equity ($)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    path = ctx.out_dir / "equity_curves.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    log.info(f"Wrote {path}")
    return path


# ---------------------------------------------------------------------------
# failures.log
# ---------------------------------------------------------------------------

def write_failures_log(
    ctx: ReportContext,
    results: Mapping[str, BacktestResult],
    *,
    gates: Optional[Gates] = None,
) -> Path:
    """Append-only one-liner per failed strategy — same format auto-retune writes."""
    gates = gates or Gates()
    path = ctx.out_dir / "failures.log"
    lines: List[str] = []
    ts = ctx.timestamp.strftime("%Y-%m-%d")
    for name, r in results.items():
        st = _gate_status(r, gates)
        failed = [g for g, ok in st.items() if not ok]
        if not failed:
            continue
        lines.append(
            f"{ts}  {name:<28}  failed: {','.join(failed)}  "
            f"PF={r.profit_factor:.2f} Sharpe={r.sharpe_ratio:.2f} "
            f"DWR={r.daily_win_rate:.0%} WorstR={r.worst_day_r:.2f}"
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    log.info(f"Wrote {path} ({len(lines)} failures)")
    return path
