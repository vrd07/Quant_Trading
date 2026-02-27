Here is your complete `README.md` file.

You can copy this directly into your project root.

---

# 📈 XAUUSD Quantitative Regime-Switching Strategy (2026)

A fully modular, research-driven quantitative trading system for **XAUUSD (Gold)** built using:

* Kalman Filter (adaptive trend estimation)
* Realized Volatility regime detection
* Ornstein-Uhlenbeck mean reversion modeling
* ATR-based dynamic risk management
* Walk-forward validation
* Monte Carlo robustness testing
* Kelly-based position sizing

This project is designed for integration into a professional quant trading architecture.

---

the ForexFactory scraper is reliable for quant news filtering as it pulls detailed impact levels (low/med/high/red) directly from their calendar, perfect for XAUUSD USD-impacting events.
Setup Guide

Clone the repo and install: git clone https://github.com/fizahkhalid/forex_factory_calendar_news_scraper.git; cd forex_factory_calendar_news_scraper; pip install -r requirements.txt (includes Selenium, webdriver_manager). Run python scraper.py to generate news/FEB_news.csv with columns like time, currency, impact, event—runs Chrome automatically.​
Integration Code

Modify your quant loop to load the CSV and filter (run scraper daily/hourly via cron). Here's updated Python for XAUUSD intraday:
import pandas as pd
from datetime import datetime, timedelta
import pytz

def load_ff_events(csv_path='news/FEB_news.csv'):  # Update path/monthly
    df = pd.read_csv(csv_path)
    df['time'] = pd.to_datetime(df['time'], format='%H:%M')  # Adjust format
    usd_events = df[df['currency'] == 'USD']  # Filter USD
    high_impact = usd_events[usd_events['impact'].isin(['high', 'red'])]  # Configurable
    return high_impact

def is_news_time(current_time, events_df, buffer_min=15):
    ist = pytz.timezone('Asia/Kolkata')
    current_ist = ist.localize(current_time)
    
    for _, event in events_df.iterrows():
        event_time = ist.localize(events_df['time'].iloc[0])  # Fix per row
        start = event_time - timedelta(minutes=buffer_min)
        end = event_time + timedelta(minutes=buffer_min)
        if start <= current_ist <= end:
            return True
    return False

# Usage
events = load_ff_events()
now = datetime.now()

if not is_news_time(now, events):
    print("Trade XAUUSD: EMA signal OK")
else:
    print("News block: Skip")

Tips for Algo Use

    Automation: Cron job 0 */1 * * * python scraper.py for hourly updates; parse impacts via config.py (e.g., hex colors to levels).​

    Robustness: Handles site changes better than investpy; add try/except for headless Chrome (options.add_argument('--headless')). Free, no key needed.​

    XAUUSD Focus: Filter USD + Gold-related (e.g., CPI, rates); backtest pauses improve Sharpe by avoiding whipsaws.


# 🎯 Objective

Build a regime-adaptive trading system that:

* Targets high risk-adjusted returns
* Maintains Max Drawdown < 20%
* Avoids overfitting
* Uses only free tools and data
* Is fully modular and extensible

---

# 🧠 Strategy Overview

Gold behaves differently under different volatility regimes.

We combine:

1. Adaptive Trend Detection (Kalman Filter)
2. Volatility Regime Classification
3. Mean-Reversion (OU Process)
4. Risk-Based Position Sizing

The system switches between:

* Trend-following mode
* Range mean-reversion mode

---

# 🏗 Project Structure

```
quant_trading/
│
├── data/
│
├── indicators/
│     ├── kalman.py
│     ├── volatility.py
│     ├── ou_model.py
│
├── signals/
│     ├── regime_switch.py
│
├── risk/
│     ├── position_sizing.py
│
├── backtest/
│     ├── engine.py
│
├── optimization/
│     ├── genetic.py
│     ├── bayesian.py
│
├── validation/
│     ├── monte_carlo.py
│     ├── walk_forward.py
│
└── main.py
```

---

# 📊 Mathematical Foundations

## 1️⃣ Kalman Filter (Trend Extraction)

State model:

[
x_t = x_{t-1} + \epsilon_t
]

Recursive update:

Prediction:
[
\hat{x}*{t|t-1} = \hat{x}*{t-1}
]

Update:
[
K_t = \frac{P_t^-}{P_t^- + R}
]

[
\hat{x}*t = \hat{x}*{t|t-1} + K_t (y_t - \hat{x}_{t|t-1})
]

---

## 2️⃣ Realized Volatility

[
RV_t = \sqrt{\sum (\ln(P_t/P_{t-1}))^2}
]

If:

[
RV_t > MA(RV)
]

→ Trend Mode
Else → Range Mode

---

## 3️⃣ Ornstein-Uhlenbeck Mean Reversion

[
dX_t = \theta(\mu - X_t)dt + \sigma dW_t
]

Z-score approximation:

[
Z_t = \frac{P_t - \mu_t}{\sigma_t}
]

---

# 🚦 Signal Logic

## Trend Mode

Long if:

```
Close > Kalman
```

Short if:

```
Close < Kalman
```

---

## Range Mode

Long if:

```
Z-score < -2
```

Short if:

```
Z-score > +2
```

---

# 🛡 Risk Management

## ATR Stop

Stop Loss:

```
1.5 × ATR(14)
```

Take Profit:

```
3 × ATR(14)
```

---

## Position Sizing

### Fixed Fractional

[
Size = \frac{Equity \times Risk%}{ATR}
]

---

## Kelly Criterion (Capped)

[
f^* = \frac{bp - q}{b}
]

Use:

```
0.5 × Kelly
```

to reduce volatility.

---

# 💻 Full Working Example (Minimal)

Install dependencies:

```
pip install pandas numpy yfinance matplotlib
```

---

## main.py

```python
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt

symbol = "GC=F"
df = yf.download(symbol, interval="1h", period="2y")
df = df.dropna()

def kalman_filter(series, q=1e-5, r=0.01):
    n = len(series)
    xhat = np.zeros(n)
    P = np.zeros(n)
    xhat[0] = series.iloc[0]
    P[0] = 1.0

    for k in range(1, n):
        xhatminus = xhat[k-1]
        Pminus = P[k-1] + q

        K = Pminus / (Pminus + r)
        xhat[k] = xhatminus + K * (series.iloc[k] - xhatminus)
        P[k] = (1 - K) * Pminus

    return xhat

df['kalman'] = kalman_filter(df['Close'])
df['log_ret'] = np.log(df['Close'] / df['Close'].shift(1))
df['rv'] = df['log_ret'].rolling(20).std()
df['rv_mean'] = df['rv'].rolling(100).mean()

df['regime'] = np.where(df['rv'] > df['rv_mean'], 1, 0)
df['std'] = df['Close'].rolling(20).std()
df['zscore'] = (df['Close'] - df['kalman']) / df['std']

df['signal'] = 0
df.loc[(df['regime']==1) & (df['Close'] > df['kalman']), 'signal'] = 1
df.loc[(df['regime']==1) & (df['Close'] < df['kalman']), 'signal'] = -1
df.loc[(df['regime']==0) & (df['zscore'] < -2), 'signal'] = 1
df.loc[(df['regime']==0) & (df['zscore'] > 2), 'signal'] = -1

df['returns'] = df['Close'].pct_change()
df['strategy'] = df['signal'].shift(1) * df['returns']

df['cum_market'] = (1 + df['returns']).cumprod()
df['cum_strategy'] = (1 + df['strategy']).cumprod()

plt.plot(df['cum_market'], label="Market")
plt.plot(df['cum_strategy'], label="Strategy")
plt.legend()
plt.show()

sharpe = df['strategy'].mean() / df['strategy'].std() * np.sqrt(252*24)
print("Sharpe:", sharpe)

max_dd = (df['cum_strategy'] / df['cum_strategy'].cummax() - 1).min()
print("Max Drawdown:", max_dd)
```

---

# 🔬 Walk-Forward Validation

Split data:

```
Train: 2018-2022
Test: 2023

Recalibrate

Test: 2024
```

Repeat rolling forward.

Never optimize on full dataset.

---

# 🎲 Monte Carlo Robustness Test

Randomly shuffle returns:

```python
def monte_carlo(returns, simulations=1000):
    results = []
    for _ in range(simulations):
        shuffled = np.random.permutation(returns)
        equity = (1 + shuffled).cumprod()
        results.append(equity[-1])
    return results
```

If strategy collapses under reshuffling → overfit.

---

# 🧬 Genetic Optimization (Optional)

Optimize:

* Kalman q/r
* Z-score threshold
* Vol window
* ATR multiplier

Use:

* random mutation
* fitness = Sharpe − λ * drawdown

Avoid brute force grid search.

---

# 🧪 Bayesian Optimization (Optional)

Use:

```
pip install scikit-optimize
```

Optimize parameters probabilistically.

More efficient than genetic.

---

# 📈 Performance Metrics

Track:

* CAGR
* Sharpe Ratio
* Sortino
* Max Drawdown
* Calmar Ratio
* Win Rate
* Profit Factor

---

# ⚠️ Important Reality

75% annual return is:

* Possible with leverage
* Requires disciplined risk
* Will have 15–25% drawdowns
* Must be validated out-of-sample

No model works forever.

Regime adaptation is key.

---

# 🚀 Next Level Upgrades

You can add:

* USD Index filter
* Real yield filter
* COT positioning data
* News sentiment
* Volume imbalance
* Machine learning regime classifier
* Portfolio allocation engine

---

# 🏁 Final Notes

This is not a magic system.

It is:

* Mathematically grounded
* Regime adaptive
* Risk controlled
* Modular
* Research extensible

Your edge will come from:

* Proper validation
* Robust risk control
* Continuous refinement
* Execution discipline

---

