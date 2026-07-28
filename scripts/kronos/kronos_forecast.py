"""Rolling Kronos-base forecasts -> prediction cache (.npz). The ONLY torch file.
Run with: ./venv_kronos/bin/python scripts/kronos/kronos_forecast.py --symbol XAUUSD ...
torch/Kronos imports are LAZY (inside run_forecast) so the pure helper stays importable
under the production venv for testing."""
import sys, pathlib, argparse, datetime as dt
import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))            # for research_fx_majors.resample
sys.path.insert(0, str(REPO / "scripts" / "kronos"))  # for kronos_cache


def build_eval_indices(n_bars, ctx, horizon, stride):
    """Indices t of the last OBSERVED bar. obs=bars[t-ctx+1:t+1], future=bars[t+1:t+1+horizon]."""
    start = ctx - 1
    end = n_bars - horizon            # need `horizon` bars strictly after t
    return list(range(start, end, stride))


def _load_15m(symbol):
    from research_fx_majors import resample
    df = pd.read_csv(REPO / f"data/historical/{symbol}_5m_real.csv",
                     parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    b = resample(df, "15min").dropna()
    b["amount"] = b["close"] * b["volume"]
    return b


def _git_commit():
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=REPO).decode().strip()
    except Exception:
        return "unknown"


def run_forecast(symbol, stride, n_paths, horizon, max_windows, ctx, device, out):
    sys.path.insert(0, str(REPO / "vendor" / "Kronos"))  # expose `model` BEFORE importing it
    import torch
    from model import Kronos, KronosTokenizer, KronosPredictor
    from kronos_cache import save_cache, ARRAY_FIELDS

    if device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    mdl = Kronos.from_pretrained("NeoQuasar/Kronos-base")
    predictor = KronosPredictor(mdl, tok, device=device, max_context=ctx)

    bars = _load_15m(symbol)
    closes = bars["close"].to_numpy()
    idxs = build_eval_indices(len(bars), ctx, horizon, stride)
    if max_windows:
        idxs = idxs[:max_windows]

    cols = ["open", "high", "low", "close", "volume", "amount"]
    acc = {f: [] for f in ARRAY_FIELDS}
    from tqdm import tqdm
    for t in tqdm(idxs, desc=f"{symbol} forecasts"):
        obs = bars.iloc[t - ctx + 1: t + 1]
        x_ts = pd.Series(obs.index)
        y_ts = pd.Series(bars.index[t + 1: t + 1 + horizon])
        pred = predictor.predict(df=obs[cols], x_timestamp=x_ts, y_timestamp=y_ts,
                                 pred_len=horizon, T=1.0, top_p=0.9, sample_count=n_paths,
                                 verbose=False)
        pc = pred["close"].to_numpy()          # mean predicted closes, length=horizon
        c0 = closes[t]
        acc["timestamp"].append(int(bars.index[t].timestamp()))
        acc["year"].append(bars.index[t].year)
        acc["last_close"].append(c0)
        acc["pred_disp_h4"].append(float("nan"))  # per-path std not exposed by predict(); nan
        for k in range(1, horizon + 1):
            acc[f"pred_ret_h{k}"].append(pc[k - 1] / c0 - 1.0)
            acc[f"real_ret_h{k}"].append(closes[t + k] / c0 - 1.0)

    meta = {"symbol": symbol, "model_id": "NeoQuasar/Kronos-base", "stride": stride,
            "n_paths": n_paths, "horizon": horizon, "ctx": ctx,
            "temperature": 1.0, "top_p": 0.9,
            "top_k": 0, "git_commit": _git_commit(), "created_at": dt.datetime.utcnow().isoformat()}
    save_cache(out, {f: np.asarray(v) for f, v in acc.items()}, meta)
    print(f"wrote {out}: {len(idxs)} windows")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--stride", type=int, default=12)
    ap.add_argument("--n-paths", type=int, default=15)
    ap.add_argument("--horizon", type=int, default=4)
    ap.add_argument("--max-windows", type=int, default=4000)
    ap.add_argument("--ctx", type=int, default=512)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = a.out or f"data/kronos_cache_{a.symbol}.npz"
    run_forecast(a.symbol, a.stride, a.n_paths, a.horizon, a.max_windows, a.ctx, a.device, out)


if __name__ == "__main__":
    main()
