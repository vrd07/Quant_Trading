"""Stage-1 IC + Stage-2 strict-fill analysis of a Kronos prediction cache.
Pure numpy/pandas/scipy — MUST NOT import torch (tests run under production venv)."""
import numpy as np
from scipy.stats import spearmanr


def spearman_ic(pred, real):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real)
    if m.sum() < 10:
        return float("nan")
    return float(spearmanr(pred[m], real[m]).correlation)


def sign_hit_rate(pred, real):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real) & (pred != 0)
    if m.sum() == 0:
        return float("nan")
    return float((np.sign(pred[m]) == np.sign(real[m])).mean())


def ic_by_year(arrays, horizon):
    years = np.asarray(arrays["year"]).astype(int)
    pred = np.asarray(arrays[f"pred_ret_h{horizon}"], float)
    real = np.asarray(arrays[f"real_ret_h{horizon}"], float)
    out = {}
    for y in sorted(set(years.tolist())):
        s = years == y
        out[int(y)] = spearman_ic(pred[s], real[s])
    return out


def strict_fill_sim(pred, real, cost_bps, threshold):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real) & (np.abs(pred) >= threshold)
    if m.sum() == 0:
        return {"pf": float("nan"), "ret": 0.0, "dd": 0.0, "wr": float("nan"), "n": 0}
    net = np.sign(pred[m]) * real[m] - cost_bps / 1e4  # round-trip cost in return units
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    equity = np.cumsum(net)
    dd = float((np.maximum.accumulate(equity) - equity).max()) if equity.size else 0.0
    return {"pf": pf, "ret": float(net.sum()), "dd": dd,
            "wr": float((net > 0).mean()), "n": int(m.sum())}


def verdict(ic_by_year_map, sim_by_year_pf, recent_year, ic_floor=0.03, pf_floor=1.1):
    reasons = []
    ric = ic_by_year_map.get(recent_year, float("nan"))
    rpf = sim_by_year_pf.get(recent_year, float("nan"))
    # Leg 1: recent-year IC must be material and right-signed (positive = usable long/short sign).
    if not np.isfinite(ric) or abs(ric) < ic_floor:
        reasons.append(f"{recent_year} IC {ric:.3f} below floor {ic_floor} (likely no signal / drift)")
        return "RED", reasons
    reasons.append(f"{recent_year} IC {ric:.3f} >= floor {ic_floor}")
    # Leg 2: recent-year after-cost PF must clear the floor.
    if not np.isfinite(rpf) or rpf < pf_floor:
        reasons.append(f"{recent_year} strict-fill PF {rpf:.2f} below floor {pf_floor}")
        return "RED", reasons
    reasons.append(f"{recent_year} strict-fill PF {rpf:.2f} >= floor {pf_floor}")
    # Leg 3: no other year may be outright negative-PF (edge must not be one-year-only).
    neg = [y for y, pf in sim_by_year_pf.items() if np.isfinite(pf) and pf < 1.0 and y != recent_year]
    if neg:
        reasons.append(f"years with PF<1.0: {sorted(neg)} (not every-year positive)")
        return "RED", reasons
    return "GREEN", reasons


def build_report(symbol, meta, ic_tables, sim_tables, final_verdict, reasons):
    """ic_tables: {horizon: {year: ic}}; sim_tables: {horizon: {year: sim_dict}}."""
    lines = [f"# Kronos IC Smell-Test — {symbol}", "",
             f"**Verdict: {final_verdict}**", "",
             "Reasons:", *[f"- {r}" for r in reasons], "",
             f"Model: {meta.get('model_id')} | ctx {meta.get('ctx') or '?'} | "
             f"stride {meta.get('stride')} | paths {meta.get('n_paths')} | "
             f"commit {meta.get('git_commit')}", "",
             "## Stage 1 — Spearman IC (per horizon × year)", ""]
    years = sorted({y for t in ic_tables.values() for y in t})
    lines.append("| horizon | " + " | ".join(str(y) for y in years) + " |")
    lines.append("|" + "---|" * (len(years) + 1))
    for h in sorted(ic_tables):
        row = " | ".join(f"{ic_tables[h].get(y, float('nan')):.3f}" for y in years)
        lines.append(f"| h{h} | {row} |")
    lines += ["", "## Stage 2 — strict-fill toy sim (per horizon × year: PF)", ""]
    lines.append("| horizon | " + " | ".join(str(y) for y in years) + " |")
    lines.append("|" + "---|" * (len(years) + 1))
    for h in sorted(sim_tables):
        row = " | ".join(f"{sim_tables[h].get(y, {}).get('pf', float('nan')):.2f}" for y in years)
        lines.append(f"| h{h} | {row} |")
    return "\n".join(lines) + "\n"


def _sim_by_year(arrays, horizon, cost_bps, threshold):
    years = np.asarray(arrays["year"]).astype(int)
    pred = np.asarray(arrays[f"pred_ret_h{horizon}"], float)
    real = np.asarray(arrays[f"real_ret_h{horizon}"], float)
    return {int(y): strict_fill_sim(pred[years == y], real[years == y], cost_bps, threshold)
            for y in sorted(set(years.tolist()))}


def main(argv=None):
    import argparse
    from kronos_cache import load_cache
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--horizon", type=int, default=4)
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--threshold", type=float, default=0.001)
    ap.add_argument("--report-out", default="reports/kronos_ic_smelltest.md")
    a = ap.parse_args(argv)
    arrays, meta = load_cache(a.cache)
    recent = int(np.asarray(arrays["year"]).astype(int).max())
    ic_tables = {h: ic_by_year(arrays, h) for h in range(1, a.horizon + 1)}
    sim_tables = {h: _sim_by_year(arrays, h, a.cost_bps, a.threshold) for h in range(1, a.horizon + 1)}
    # verdict uses the best-IC horizon by |recent-year IC|
    best_h = max(ic_tables, key=lambda h: abs(ic_tables[h].get(recent, 0.0) or 0.0))
    sim_pf = {y: s["pf"] for y, s in sim_tables[best_h].items()}
    v, reasons = verdict(ic_tables[best_h], sim_pf, recent)
    report = build_report(meta.get("symbol", "?"), meta, ic_tables, sim_tables, v,
                          [f"(verdict horizon h{best_h})", *reasons])
    import pathlib
    pathlib.Path(a.report_out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.report_out).write_text(report)
    print(report)
    return v


if __name__ == "__main__":
    main()
