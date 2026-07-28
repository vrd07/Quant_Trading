"""Feasibility gate: load Kronos-base + tokenizer, run one tiny forecast, print device/shapes.
Run with the isolated interpreter: ./venv_kronos/bin/python scripts/kronos/check_env.py

API note (verified against vendor/Kronos/model/kronos.py on the cloned commit):
KronosPredictor.predict(self, df, x_timestamp, y_timestamp, pred_len,
                         T=1.0, top_k=0, top_p=0.9, sample_count=1, verbose=True)
-> returns a pd.DataFrame indexed by y_timestamp with columns
   ['open', 'high', 'low', 'close', 'volume', 'amount'] (shape (pred_len, 6)).
This matches the parameter names used below; no adaptation was needed.
"""
import sys, pathlib
import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "vendor" / "Kronos"))  # exposes `model` package

import torch
from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402

device = "mps" if torch.backends.mps.is_available() else "cpu"
print(f"torch {torch.__version__} | device {device}")

tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
mdl = Kronos.from_pretrained("NeoQuasar/Kronos-base")
predictor = KronosPredictor(mdl, tok, device=device, max_context=512)

# 256 synthetic bars in, forecast 4 out
n = 256
idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
base = 2000 + np.cumsum(np.random.randn(n))
df = pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                   "close": base, "volume": np.abs(np.random.randn(n)) * 100})
df["amount"] = df["close"] * df["volume"]
y_ts = pd.date_range(idx[-1], periods=5, freq="15min", tz="UTC")[1:]

pred = predictor.predict(df=df, x_timestamp=pd.Series(idx), y_timestamp=pd.Series(y_ts),
                         pred_len=4, T=1.0, top_p=0.9, sample_count=5)
print("prediction shape:", pred.shape)
print(pred[["open", "high", "low", "close"]].to_string())
print("OK: Kronos-base runs on this machine.")
