---

```markdown
# XAUUSD Market Sentiment Engine — Technical Specification

**Version:** 1.0  
**Date:** June 2026  
**Asset:** XAUUSD (Spot Gold / US Dollar)  
**Objective:** Build a modular sentiment scoring system that aggregates fundamental, technical, institutional, retail, and news data into a composite **Gold Sentiment Score (GSS)**. An AI decision layer (Claude) uses this score, combined with real-time market structure, to generate directional trading decisions with embedded risk management.

---

## 1. Project Overview

### 1.1 Philosophy
Gold does not trade on a single factor. It trades on the **intersection** of:
- Real yields and Fed policy (the opportunity cost of holding zero-yield bullion)
- USD strength (the denominator effect)
- Central bank structural demand (the "de-dollarization" bid)
- Geopolitical risk (the fear premium)
- Institutional positioning (COT "smart money")
- Retail positioning (contrarian extremes)
- Technical market structure (trend, momentum, volatility)

This engine weights these inputs into a single normalized score and maps that score to actionable trade decisions.

### 1.2 Target Performance
The system is not designed to predict every tick. It is designed to:
- Identify high-probability directional regimes (bullish/bearish/neutral)
- Avoid chop during conflicting-signal periods
- Size positions according to volatility and conviction
- Protect capital via systematic stop-loss and correlation checks

### 1.3 Core Hypotheses (2026 Market Regime)
1. **Structural bid intact:** Central bank buying (China, India, Poland, Turkey) remains at historic highs. This creates a floor under gold near \$4,000–\$4,200.
2. **Fed easing cycle:** Rate cuts are expected to continue through 2026. Lower real yields = bullish gold.
3. **Geopolitical premium:** Middle East and Eastern Europe tensions are persistent. Sudden escalations cause safe-haven spikes.
4. **Inverse DXY correlation:** Gold and the Dollar Index maintain ~-0.85 correlation. DXY strength must override bullish gold signals.
5. **Retail is wrong at extremes:** When retail long/short ratio exceeds 80/20, a reversal or deep pullback becomes probable within 5–10 trading days.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA INGESTION LAYER                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │  Macro   │ │  Price   │ │Technical │ │Sentiment │          │
│  │  Feed    │ │  Feed    │ │  Feed    │ │  Feed    │          │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘          │
│       └─────────────┴─────────────┴─────────────┘              │
│                         │                                       │
│              ┌──────────▼──────────┐                           │
│              │   Data Normalizer   │  (cleans, aligns, fills)  │
│              └──────────┬──────────┘                           │
│                         │                                       │
│         ┌───────────────▼───────────────┐                     │
│         │   SENTIMENT SCORING ENGINE    │                     │
│         │      (Gold Sentiment Score)   │                     │
│         │         GSS = 0 to 100        │                     │
│         └───────────────┬───────────────┘                     │
│                         │                                       │
│         ┌───────────────▼───────────────┐                     │
│         │    AI DECISION LAYER (Claude)   │                     │
│         │  Receives GSS + Market Context  │                     │
│         │  Outputs: Direction, Size, SL   │                     │
│         └───────────────┬───────────────┘                     │
│                         │                                       │
│         ┌───────────────▼───────────────┐                     │
│         │      EXECUTION & RISK MODULE    │                     │
│         │  Position sizing, stops, logs │                     │
│         └───────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Sources & APIs

### 3.1 Macro Data (Fundamental Bias)
| Data Point | Frequency | Source / API | Endpoint Example |
|---|---|---|---|
| US CPI YoY | Monthly | Alpha Vantage | `function=CPI` |
| Core PCE | Monthly | FRED API | `series_id=PCEPILFE` |
| Fed Funds Rate | Per meeting | FRED API | `series_id=FEDFUNDS` |
| 10Y TIPS Yield (Real Yield) | Daily | FRED API | `series_id=DFII10` |
| DXY (US Dollar Index) | Daily | Alpha Vantage | `function=FX_DAILY` |
| US National Debt | Monthly | Treasury.gov | RSS / scrape |
| Central Bank Gold Buying | Monthly | World Gold Council | Manual input / PDF parse |
| FOMC Statement / Dot Plot | Per meeting | Federal Reserve | NLP scrape |

**Normalization Rule:** All macro inputs are converted to a **directional delta** (improving, deteriorating, stable) based on the last 3 data points.

### 3.2 Price Data (Execution Reference)
| Source | Type | Limits | Best For |
|---|---|---|---|
| **GoldAPI.io** | REST JSON (spot XAU/USD) | Free tier available | Primary price feed |
| **UniRateAPI** | LBMA-blended spot | 200 req/day free | Backup / cross-check |
| **Alpha Vantage** | Daily/Intraday OHLC | 25 calls/day free | Historical backfill |
| **iTick** | WebSocket real-time | 5 calls/min free | Live tick data |

**Required Fields:** `symbol`, `price`, `bid`, `ask`, `timestamp`, `change_24h`.

### 3.3 Technical Indicators
| Indicator | Periods | Source / API | Notes |
|---|---|---|---|
| EMA-50, EMA-200 | Daily/H4 | EODHD API | Trend structure |
| RSI-14 | H4/Daily | EODHD API | Overbought >70, Oversold <30 |
| MACD (12,26,9) | Daily | EODHD API | Signal crossovers |
| Bollinger Bands (20,2) | Daily | EODHD API | Volatility expansion |
| ATR-14 | Daily | EODHD API | Stop-loss calculation |
| Stochastic (14,3,3) | H4 | EODHD API | Short-term reversals |

**EODHD Endpoint:** `https://eodhd.com/api/technical/{symbol}?order=a&fmt=json&function=rsi&period=14&api_token={KEY}`

### 3.4 Sentiment Data
| Data Point | Frequency | Source | Access Method |
|---|---|---|---|
| **CFTC COT Report** | Weekly (Fri) | CFTC / fxmacrodata.com | API: `fxmacrodata.com/api/v1/cot` |
| **Retail Long/Short %** | Real-time | OANDA / Dukascopy / Myfxbook | Broker API or scrape |
| **GLD ETF Holdings** | Daily | Yahoo Finance | `stock_finance_data` API |
| **News Sentiment** | Real-time | Alpha Vantage News | `function=NEWS_SENTIMENT` |
| **Alternative News** | Real-time | Polygon.io / APITube | REST with ticker filter |

**COT Key Metrics for Gold:**
- `non_commercial_long`
- `non_commercial_short`
- `commercial_long`
- `commercial_short`
- `net_non_commercial` (speculative bias)

---

## 4. The Gold Sentiment Score (GSS) Algorithm

### 4.1 Weighting Framework
The GSS is a composite index from **0 (extreme bearish)** to **100 (extreme bullish)**.

| Component | Weight | Inputs | Scoring Logic |
|---|---|---|---|
| **Fundamental Bias** | 30% | Fed policy, real yields, DXY, CPI, debt | +10 if easing cycle; +10 if real yields falling; +10 if DXY weak |
| **Technical Bias** | 25% | EMA trend, RSI, MACD, BB position | +10 if price > 50/200 EMA; +10 if RSI 50-65 (healthy); +5 if MACD bullish |
| **Institutional Sentiment** | 20% | COT net positioning, ETF flows | +20 if specs increasing net longs / ETFs inflowing |
| **Retail Sentiment** | 15% | Long/Short ratio (contrarian) | +15 if retail is heavily short (bullish signal); -15 if heavily long |
| **News & Event Risk** | 10% | NLP sentiment, geopolitical alerts | +10 if positive gold headlines / risk-off events |

### 4.2 Component Scoring Details

#### A. Fundamental Bias (30 points max)
```
Score = FedScore + YieldScore + DXYScore + InflationScore + FiscalScore

FedScore:
  +10 if last FOMC was dovish OR futures price >1 cut in 6 months
  +5  if neutral / pause
  -10 if hawkish / hike

YieldScore:
  +10 if 10Y TIPS yield < 1.5% and falling
  +5  if 1.5–2.0%
  -10 if >2.5% and rising

DXYScore:
  +10 if DXY < 100 and falling
  +5  if 100–103 range
  -10 if >105 and rising

InflationScore:
  +5 if CPI >3% (inflation hedge demand)
  0  if 2–3%
  -5 if <2% and falling fast (deflationary)

FiscalScore:
  +5 if debt ceiling crisis / shutdown risk active
  0  otherwise
```

#### B. Technical Bias (25 points max)
```
Score = TrendScore + MomentumScore + VolatilityScore

TrendScore:
  +10 if Price > EMA-50 > EMA-200 (bullish alignment)
  +5  if Price > EMA-50 but < EMA-200 (recovering)
  0   if mixed / chop
  -10 if Price < EMA-50 < EMA-200 (bearish alignment)

MomentumScore:
  +10 if RSI(14) between 50 and 65 (strong but not overbought)
  +5  if RSI 40–50 or 65–70
  0   if RSI 30–40
  -5  if RSI >70 (overbought warning) or <30 (weak momentum)

MACDScore:
  +5 if MACD line > Signal and histogram expanding
  0  if mixed / convergence
  -5 if bearish crossover

BBScore:
  +5 if price walking upper band (strong trend)
  0  if inside bands
  -5 if lower band breach
```

#### C. Institutional Sentiment (20 points max)
```
Score = COTScore + ETFScore

COTScore (based on weekly change):
  +20 if Net Non-Commercial Longs increased >10% WoW
  +15 if increased 5–10%
  +10 if increased 0–5%
  0   if flat
  -10 if decreased (profit taking)

ETFScore (GLD daily flow proxy):
  +5 if 3-day average inflow positive
  0  if flat
  -5 if outflow
```

#### D. Retail Sentiment (15 points max) — CONTRARIAN
```
If Retail Long % > 80%:  Score = -15  (extreme bullish retail = bearish signal)
If Retail Long % 65–80%: Score = -5
If Retail Long % 35–65%: Score = +5   (balanced, trend can continue)
If Retail Long % 20–35%: Score = +10
If Retail Long % < 20%:  Score = +15  (extreme bearish retail = bullish signal)
```

#### E. News & Event Risk (10 points max)
```
Score = NewsSentiment + GeoRisk

NewsSentiment (NLP average of last 100 headlines):
  +5 if avg sentiment > 0.2 (positive gold mentions)
  0  if -0.2 to 0.2
  -5 if < -0.2

GeoRisk:
  +5 if active military escalation / sanctions / election shock in last 48h
  0  if stable
```

### 4.3 GSS Interpretation Scale
| GSS Range | Regime | Recommended Action |
|---|---|---|
| **80–100** | Extreme Bullish | Full long, aggressive pyramiding on pullbacks |
| **65–79** | Strong Bullish | Standard long position, standard size |
| **50–64** | Moderate Bullish | Reduced long, wait for better entry |
| **35–49** | Neutral / Chop | No new position, reduce exposure |
| **20–34** | Moderate Bearish | Reduced short or exit longs |
| **5–19** | Strong Bearish | Standard short position |
| **0–4** | Extreme Bearish | Full short, aggressive on rallies |

---

## 5. AI Decision Layer (Claude Prompting Interface)

Claude does not receive raw data. It receives a **structured context object** and outputs a decision.

### 5.1 Input Context Schema (JSON)
```json
{
  "timestamp": "2026-06-02T14:30:00Z",
  "asset": "XAUUSD",
  "price": 4585.50,
  "gss": {
    "total_score": 72,
    "regime": "Strong Bullish",
    "breakdown": {
      "fundamental": 25,
      "technical": 20,
      "institutional": 15,
      "retail": 8,
      "news": 4
    }
  },
  "market_structure": {
    "trend": "bullish",
    "price_vs_50ema": "above",
    "price_vs_200ema": "above",
    "rsi_14": 58,
    "macd_signal": "bullish",
    "atr_14": 165.20,
    "nearest_support": 4520.00,
    "nearest_resistance": 4650.00,
    "session": "new_york"
  },
  "macro_context": {
    "fed_policy": "easing",
    "real_yield_10y": 1.25,
    "dxy": 98.45,
    "cpi_yoy": 3.2,
    "next_high_impact_event": "NFP_Friday_08:30_ET"
  },
  "risk_flags": {
    "dxy_surging": false,
    "real_yields_spiking": false,
    "retail_extreme_long": true,
    "geopolitical_shock": false,
    "weekend_gap_risk": false
  },
  "position_status": {
    "current_position": "none",
    "unrealized_pnl": 0
  }
}
```

### 5.2 Claude Decision Prompt Template
```
You are a disciplined gold (XAUUSD) trader. You receive a structured market context 
and must output a trading decision in strict JSON format.

RULES:
1. Respect the GSS regime but override if risk flags are active.
2. Never fight the DXY. If dxy_surging=true AND real_yields_spiking=true, 
   ignore bullish GSS and go flat/short.
3. If retail_extreme_long=true, reduce position size by 50% even if GSS is high.
4. Avoid entries 2 hours before high-impact US data (NFP, CPI, FOMC).
5. Use ATR for stop-loss placement (1.5x ATR for swing, 1x ATR for scalp).

OUTPUT FORMAT:
{
  "decision": "LONG | SHORT | FLAT | REDUCE",
  "confidence": "HIGH | MEDIUM | LOW",
  "entry_zone": {"min": 4580.00, "max": 4590.00},
  "stop_loss": 4550.00,
  "take_profit_1": 4650.00,
  "take_profit_2": 4700.00,
  "position_size_pct": 2.0,
  "rationale": "string explaining the logic",
  "override_reason": "null or string if overriding GSS"
}
```

---

## 6. Risk Management Module

### 6.1 Position Sizing Formula
```
Base Risk Per Trade = 1.0% of account equity

Size Multiplier based on GSS:
  GSS 80–100: 1.5x (1.5% risk)
  GSS 65–79:  1.0x (1.0% risk)
  GSS 50–64:  0.5x (0.5% risk)
  GSS 35–49:  0.25x or flat
  GSS <35:    0.5x short (inverse sizing)

Volatility Adjustment:
  If ATR_14 > $200: reduce size by 50%
  If ATR_14 > $250: reduce size by 75%
  If VIX > 35: reduce all sizes by 50%

Correlation Check:
  If DXY daily change > +0.5% AND gold is flat/up: NO LONG. Wait.
  If real yields daily change > +5bps: NO LONG. Wait.
```

### 6.2 Stop Loss Rules
| Trade Type | Stop Distance | Calculation |
|---|---|---|
| Swing Trade (H4/Daily) | 1.5x ATR | `Entry - (1.5 * ATR_14)` |
| Day Trade (M15/H1) | 1.0x ATR | `Entry - (1.0 * ATR_14)` |
| Hard Max | $300/oz | Never risk more than $300 per oz regardless of ATR |

### 6.3 Take Profit Rules
- **TP1:** 1.5x risk (move stop to breakeven when hit)
- **TP2:** 3x risk (trailing stop activation)
- **TP3:** 5x risk (discretionary, only if trend strongly aligned)

### 6.4 Kill Switches (Emergency Flat)
Trigger immediate liquidation and 24h trading halt:
1. Geopolitical de-escalation headline + gold drops >$100 in 1 hour
2. Fed emergency hawkish statement (unexpected hike)
3. DXY breaks 107 with volume
4. Two consecutive stop-loss hits in same direction (system likely wrong)

---

## 7. Implementation Roadmap

### Phase 1: Foundation (Week 1–2)
- [ ] Set up Python project structure (`/data`, `/scoring`, `/decision`, `/risk`, `/logs`)
- [ ] Integrate **GoldAPI.io** or **UniRateAPI** for live price feed
- [ ] Integrate **FRED API** for macro data (real yields, Fed funds, CPI)
- [ ] Build data normalizer (handles missing data, timezone alignment)

### Phase 2: Technical Engine (Week 3)
- [ ] Integrate **EODHD API** for technical indicators
- [ ] Build trend classifier (bullish/bearish/neutral based on EMAs)
- [ ] Build momentum classifier (RSI, MACD states)
- [ ] Calculate ATR-based stop levels automatically

### Phase 3: Sentiment Engine (Week 4)
- [ ] Integrate **fxmacrodata.com** COT API (weekly polling)
- [ ] Scrape or API-connect retail long/short ratios
- [ ] Integrate **Alpha Vantage News API** for NLP sentiment
- [ ] Build GSS calculator with all 5 components

### Phase 4: Decision Interface (Week 5)
- [ ] Build context assembler (creates the JSON prompt for Claude)
- [ ] Design Claude prompt template with strict output rules
- [ ] Parse Claude JSON output into execution commands
- [ ] Paper trade for 2 weeks to validate signal quality

### Phase 5: Execution & Risk (Week 6–7)
- [ ] Connect to broker API (OANDA / Dukascopy / similar) for execution
- [ ] Implement position sizing calculator
- [ ] Implement kill switches and correlation overrides
- [ ] Build logging and P&L tracking dashboard

### Phase 6: Optimization (Ongoing)
- [ ] Weekly GSS weight recalibration based on hit rate
- [ ] Backtest against 2024–2026 gold regime
- [ ] Add machine learning layer to predict GSS regime persistence

---

## 8. Key 2026 Market Levels & Context

### Critical Price Levels
| Level | Significance |
|---|---|
| **\$4,700–\$4,800** | 2026 major resistance, psychological barrier |
| **\$4,550** | Current consolidation ceiling, breakout target |
| **\$4,350–\$4,400** | Recent breakout retest zone, strong support |
| **\$4,200** | **Critical weekly support.** Break below = trend change to \$3,450–\$3,200 |
| **\$4,000** | "Line in the sand" for structural bull market |
| **\$3,450** | 2025 breakout origin, major demand zone |

### 2026 Analyst Consensus
- **J.P. Morgan:** \$5,200–\$5,300 target (mid-2026)
- **Goldman Sachs:** \$5,000 target
- **UBS:** \$4,700 near-term, \$5,000+ 12-month
- **ANZ:** \$4,400 near-term, \$5,000 long-term

### Session Timing
| Session | Time (GMT) | Volatility | Best For |
|---|---|---|---|
| Tokyo | 00:00–08:00 | Low | Avoid (unless China news) |
| London | 08:00–16:00 | High | Trend establishment, breakout entries |
| New York | 13:00–21:00 | Very High | Momentum, news reaction, volume confirmation |
| NY-London Overlap | 13:00–16:00 | Highest | Best execution window |

---

## 9. Data Schema Definitions

### 9.1 Normalized Price Tick
```json
{
  "symbol": "XAUUSD",
  "timestamp": "2026-06-02T14:30:00.123Z",
  "bid": 4585.40,
  "ask": 4585.60,
  "mid": 4585.50,
  "volume": 1420,
  "source": "goldapi.io"
}
```

### 9.2 GSS Output
```json
{
  "generated_at": "2026-06-02T14:30:00Z",
  "gss_total": 72,
  "regime": "Strong Bullish",
  "components": {
    "fundamental": {"score": 25, "max": 30, "details": "Fed easing, DXY weak, yields falling"},
    "technical": {"score": 20, "max": 25, "details": "Above EMAs, RSI 58, MACD bullish"},
    "institutional": {"score": 15, "max": 20, "details": "COT net longs +12% WoW"},
    "retail": {"score": 8, "max": 15, "details": "Retail 72% long (moderate contrarian)"},
    "news": {"score": 4, "max": 10, "details": "Neutral sentiment, no geo shocks"}
  },
  "recommendation": "LONG",
  "override_flags": []
}
```

### 9.3 Trade Log Entry
```json
{
  "trade_id": "uuid",
  "timestamp_open": "2026-06-02T14:35:00Z",
  "timestamp_close": null,
  "direction": "LONG",
  "entry_price": 4585.50,
  "stop_loss": 4555.00,
  "take_profit_1": 4650.00,
  "take_profit_2": 4700.00,
  "size_oz": 10,
  "gss_at_entry": 72,
  "risk_flags_at_entry": ["retail_extreme_long"],
  "rationale": "Strong bullish GSS, price retesting 50 EMA in NY session",
  "claude_override": null,
  "status": "OPEN",
  "unrealized_pnl": 0
}
```

---

## 10. Testing & Validation Checklist

Before going live, verify:
- [ ] GSS correctly predicted direction in 60%+ of backtested weeks (2024–2026)
- [ ] System correctly **avoided** longs during March 2025 DXY spike
- [ ] Retail contrarian signal flagged the April 2025 top (retail was 85% long)
- [ ] Kill switch would have triggered on [insert historical shock event]
- [ ] Latency from price tick to Claude decision < 5 seconds
- [ ] All API failovers work (GoldAPI down → UniRateAPI backup)

---

## 11. Glossary

| Term | Definition |
|---|---|
| **XAUUSD** | Spot Gold priced in US Dollars (1 troy ounce) |
| **GSS** | Gold Sentiment Score (0–100 composite index) |
| **COT** | Commitment of Traders report (CFTC weekly positioning data) |
| **TIPS** | Treasury Inflation-Protected Securities (used to derive real yields) |
| **DXY** | US Dollar Index (basket of major currencies) |
| **GLD** | SPDR Gold Shares ETF (largest gold ETF, proxy for institutional demand) |
| **Real Yield** | Nominal bond yield minus expected inflation (opportunity cost of gold) |
| **LBMA** | London Bullion Market Association (sets gold pricing standards) |

---

## 12. References & Further Reading

1. **World Gold Council** — Gold Demand Trends (quarterly): gold.org
2. **CFTC COT Reports** — cftc.gov/marketreports/commitmentsoftraders
3. **FRED Economic Data** — St. Louis Fed (fred.stlouisfed.org)
4. **GoldAPI Documentation** — goldapi.io/docs
5. **EODHD Technical API Docs** — eodhd.com/financial-apis
6. **Alpha Vantage Docs** — alphavantage.co/documentation
7. **SentimenTrader** — sentimenTrader.com (paid sentiment indices)
8. **Forex Factory Calendar** — forexfactory.com/calendar (economic event scheduling)

---

**Document Owner:** [Your Name]  
**Next Review Date:** July 2026  
**Status:** Draft v1.0 — Ready for implementation
```

---
