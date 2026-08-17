//+------------------------------------------------------------------+
//|                                            EA_KalmanRegime.mq5   |
//|      MQL5 port of src/strategies/kalman_regime_strategy.py (v2)  |
//+------------------------------------------------------------------+
//  WHAT THIS EA IS
//    A bar-for-bar port of the Python `kalman_regime` strategy, plus an
//    explicit record of WHY each trade was taken. Every closed bar builds a
//    gate trace — an ordered list of (gate, actual value, threshold, verdict).
//    When a trade fires the trace is written to the Experts log, drawn on the
//    chart as an arrow, and stored in that arrow's tooltip. When no trade
//    fires, the FIRST failing gate is logged using the same wording as the
//    Python `_log_no_signal()` calls, so the two are directly comparable.
//
//  THE DECISION CHAIN — evaluated in exactly this order, on CLOSED bars only.
//    Any gate that fails ends the bar; nothing below it is evaluated.
//
//    G1  symbol prefix     ticker starts with InpAllowedSymbolPrefix
//                          (prefix match, so a broker suffix like XAUUSD.p passes)
//    G2  data              window length >= min_bars (derived, see MinBars())
//    G3  cooldown          bars since last signal >= InpCooldownBars
//    G4  session           UTC hour of the bar is inside InpAllowedSessions
//    G5  regime            RV(20) > MA(RV,100)  ->  TREND    else  RANGE
//
//    -- TREND branch --------------------------------------------------------
//    T1  ADX               ADX(14) >= InpTrendAdxMin
//    T2  trend quality     |slope|/(|slope|+sd(slope)) >= min   (default OFF)
//    T3  side              close > kalman -> BUY ; close < kalman -> SELL
//    T4  confirmation      the InpKalmanConfirmBars bars BEFORE this one were
//                          all on the same side of the Kalman line
//    T5  slope             kalman[-1] - kalman[-3] > 0 (BUY) / < 0 (SELL)
//    T6  acceleration      slope now vs slope InpKalmanAccelBars ago  (OFF)
//    T7  EMA               EMA(9) > EMA(21) for BUY, < for SELL      (OFF)
//    T8  MACD              histogram > 0 for BUY, < 0 for SELL       (OFF)
//        strength = 0.5*min(|close-kalman|/ATR,1) + 0.3*min(ADX/50,1)
//                 + 0.2*(RSI/100)            [SELL uses (100-RSI)/100]
//
//    -- RANGE branch --------------------------------------------------------
//    R1  z-score + RSI     z < -threshold AND RSI < InpRangeRsiBuy   -> BUY
//                          z > +threshold AND RSI > InpRangeRsiSell  -> SELL
//    R2  stochastic        %K < oversold (BUY) / > overbought (SELL) (OFF)
//    R3  channel           last N closes inside +/- mult*ATR(50)     (OFF)
//    R4  divergence        MACD-hist divergence agreeing with fade   (OFF)
//    R5  volume node       price within mult*ATR of a volume shelf   (OFF)
//        strength = min(|z| / (threshold*1.5), 1)
//
//    -- both branches -------------------------------------------------------
//    G6  long-only         suppress SELL when InpLongOnly            (OFF)
//    G7  HTF gate          SELL needs 1H close <  1H EMA(50)          (ON)
//                          BUY  needs 1H close >  1H EMA(50)          (OFF)
//    G8  min strength      BUY >= InpMinSignalStrength (0.50)
//                          SELL >= InpMinSignalStrengthSell (0.85)
//                          -> FIRE
//
//  INDICATORS ARE HAND-CODED ON PURPOSE — DO NOT SWAP IN iATR/iRSI/iADX.
//    The Python stack uses SIMPLE moving averages where MT5's built-ins use
//    Wilder smoothing, and its ADX is not the textbook one. Using the built-ins
//    would silently produce a different EA. What is implemented here:
//      ATR    = SMA(TrueRange, 14)                      (not Wilder)
//      RSI    = 100 - 100/(1+SMA(gain,14)/SMA(loss,14)) (not Wilder)
//      ADX    = SMA(DX, 14),  DX = 100*|+DI - -DI|/(+DI + -DI),
//               +/-DI = 100 * rolling_SUM(+/-DM, 14) / SMA-ATR(14)
//      EMA    = pandas ewm(span=n, adjust=False), seeded at the first bar
//      Kalman = scalar random-walk filter, x0 = close[0], P0 = 1
//      RV     = sample std (ddof=1) of log returns over 20
//      OU z   = (close - kalman) / sample_std(close - kalman, 20)
//    Every rolling std is the SAMPLE std (ddof=1) that pandas uses; the one
//    exception is the volume-node threshold, where numpy's population std
//    (ddof=0) is what the Python calls, and that is what is used here.
//
//  SL / TP — the LIVE policy (budget SL + RR TP), reproduced end to end.
//    The ATR stop still exists, but only as the SIZING input; the stop that
//    reaches the broker comes from the dollar budget. This is why
//    `sl_atr_multiplier` is a no-op on the live path.
//      provisional SL = InpSlAtrMultiplier * ATR(14)
//      lot            = balance * risk_pct * (0.7 + 0.6*strength)
//                       / (provisional_SL * value_per_lot)     [floor to step,
//                                                               clamp min/max]
//      budget SL dist = max_loss_USD / (lot * value_per_lot)   <-- the real stop
//      TP             = InpRewardRiskRatio * budget SL dist
//    If the budget stop is tighter than 1.05x the broker minimum the trade is
//    SKIPPED, exactly as execution_engine.py does.
//    SL/TP are anchored to the BAR CLOSE (the Python signal's entry_price),
//    not to the fill, so the planned geometry is exact.
//
//  TWO THINGS THAT WILL BITE YOU IF IGNORED
//    1. TIMEZONE. The session windows are UTC but MT5 bar stamps are broker
//       server time. InpBrokerGmtOffsetHours = 99 auto-detects the offset from
//       TimeCurrent()-TimeGMT(); set it manually if your broker's DST handling
//       makes the auto value jump. Get this wrong and every session shifts.
//    2. THE KALMAN IS RECURSIVE. Its value at the newest bar depends on where
//       the series started. Steady-state gain is ~0.031 (memory ~32 bars), so
//       the default 1500-bar window makes the seed irrelevant — but shortening
//       InpWarmupBars towards min_bars will move the numbers.
//
//  PARITY
//    Set InpExportCSV = true, let it run, then:
//        python scripts/check_kalman_ea_parity.py
//    That replays the exported bars through the real Python strategy and diffs
//    every indicator and every decision. Until that has been run and passed,
//    "matches the Python" is a claim, not a measurement.
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property version   "1.00"
#property description "Kalman regime-switching (v2) with a full per-bar gate trace, budget SL + RR TP."

//==================================================================
//  INPUTS
//==================================================================
input group "=== Core (mirrors strategies.kalman_regime) ==="
input ENUM_TIMEFRAMES InpTimeframe            = PERIOD_M15; // Strategy timeframe
input int             InpWarmupBars           = 1500;       // Bars in the evaluation window
input string          InpAllowedSymbolPrefix  = "XAUUSD";   // Hard symbol gate (prefix match)
input double          InpKalmanQ              = 0.00001;    // Kalman process noise q
input double          InpKalmanR              = 0.01;       // Kalman measurement noise r
input int             InpRvWindow             = 20;         // Realized-vol window
input int             InpRvMaWindow           = 100;        // RV moving-average window
input int             InpZscoreWindow         = 20;         // OU z-score window
input double          InpEntryThreshold       = 2.0;        // RANGE |z| entry threshold
input double          InpTrendAdxMin          = 17;         // TREND minimum ADX
input int             InpKalmanConfirmBars    = 4;          // Consecutive bars on Kalman side
input int             InpAtrPeriod            = 14;         // ATR period
input int             InpCooldownBars         = 2;          // Bars between signals
input bool            InpLongOnly             = false;      // Suppress all SELLs

input group "=== Signal strength gates ==="
input double          InpMinSignalStrength     = 0.50;      // Minimum strength (BUY)
input double          InpMinSignalStrengthSell = 0.85;      // Minimum strength (SELL)

input group "=== Session filter (UTC hours) ==="
input bool            InpSessionFilterEnabled = true;              // Enable session filter
input string          InpAllowedSessions      = "1-4,7-8,10-12,15-23"; // UTC windows, inclusive
input int             InpBrokerGmtOffsetHours = 99;                // Broker GMT offset (99 = auto)

input group "=== TREND confirmations (all OFF in the live config) ==="
input bool            InpEmaConfirmEnabled    = false;      // Require EMA fast/slow alignment
input int             InpEmaFastPeriod        = 9;          // EMA fast
input int             InpEmaSlowPeriod        = 21;         // EMA slow
input bool            InpMacdConfirmation     = false;      // Require MACD histogram agreement
input int             InpMacdFast             = 12;         // MACD fast
input int             InpMacdSlow             = 26;         // MACD slow
input int             InpMacdSignal           = 9;          // MACD signal
input bool            InpKalmanAccelEnabled   = false;      // Require Kalman acceleration
input int             InpKalmanAccelBars      = 5;          // Acceleration lookback
input bool            InpTrendQualityEnabled  = false;      // Trend-quality score gate
input int             InpTqSlopeBars          = 3;          // Trend-quality slope bars
input int             InpTqStdWindow          = 20;         // Trend-quality std window
input double          InpTqMinScore           = 0.75;       // Trend-quality minimum score

input group "=== RANGE mode ==="
input double          InpRangeRsiBuy          = 42;         // RANGE BUY: RSI below
input double          InpRangeRsiSell         = 65;         // RANGE SELL: RSI above
input bool            InpStochConfirmEnabled  = false;      // Stochastic confirmation
input double          InpStochOversold        = 25;         // Stoch oversold
input double          InpStochOverbought      = 75;         // Stoch overbought
input bool            InpRangeChannelEnabled  = false;      // Layer 1: true range-bound test
input int             InpRangeChannelBars     = 20;         // Channel bars
input int             InpRangeChannelAtrPer   = 50;         // Channel ATR period
input double          InpRangeChannelAtrMult  = 1.5;        // Channel ATR multiple
input bool            InpRangeDivergenceEn    = false;      // Layer 2: exhaustion divergence
input int             InpRangeDivLookback     = 60;         // Divergence lookback
input bool            InpRangePocEnabled      = false;      // Layer 3: volume-node proximity
input int             InpRangePocBars         = 50;         // Volume-profile bars
input int             InpRangePocBins         = 24;         // Volume-profile bins
input double          InpRangePocAtrMult      = 1.0;        // Volume-node ATR multiple

input group "=== Higher-timeframe directional gate ==="
input bool            InpHtfSellFilterEnabled = true;       // SELL needs bearish HTF
input bool            InpHtfBuyFilterEnabled  = false;      // BUY needs bullish HTF
input ENUM_TIMEFRAMES InpHtfTimeframe         = PERIOD_H1;  // HTF (resampled from InpTimeframe)
input int             InpHtfEmaPeriod         = 50;         // HTF EMA period

input group "=== Risk: budget SL + RR TP (live policy) ==="
input double          InpMaxLossUSD           = 0;          // Max loss per trade USD (0 = use pct)
input double          InpBudgetRiskPct        = 0.0024;     // Budget pct of balance when USD = 0
input double          InpSizingRiskPct        = 0.0024;     // Sizing risk pct of balance
input bool            InpStrengthScaling      = true;       // Scale risk by 0.7+0.6*strength
input double          InpSlAtrMultiplier      = 3.0;        // ATR mult for SIZING ONLY
input double          InpRewardRiskRatio      = 2.0;        // TP = RR x budget SL
input double          InpValuePerLot          = 0;          // Value per 1.0 price move (0 = auto)
input double          InpMinLot               = 0.04;       // Minimum lot
input double          InpMaxLot               = 0.12;       // Maximum lot
input double          InpLotStep              = 0.01;       // Lot step
input double          InpMinStopsDistance     = 1.0;        // Config min stop distance (price units)

input group "=== Execution ==="
input bool            InpDryRun               = false;      // Mark only, never send orders
input int             InpMaxPositions         = 1;          // Max concurrent positions (this magic)
input long            InpMagic                = 20260817;   // Magic number
input int             InpSlippage             = 30;         // Max deviation (points)
input double          InpMaxSpreadPoints      = 0;          // Max spread in points (0 = no check)

input group "=== Marking and diagnostics ==="
input bool            InpDrawMarkers          = true;       // Draw entry arrows and labels
input bool            InpLogRejections        = true;       // Log the first failing gate each bar
input bool            InpShowPanel            = true;       // Show the live gate panel
input bool            InpExportCSV            = false;      // Export parity CSV to Common\Files
input string          InpCsvName              = "kalman_parity.csv";      // Parity CSV name
input string          InpMetaName             = "kalman_parity_meta.csv"; // Parity meta name

//==================================================================
//  CONSTANTS / GLOBALS
//==================================================================
#define NA  DBL_MAX

#define SIDE_NONE  0
#define SIDE_BUY   1
#define SIDE_SELL -1

// --- source window (index 0 = oldest, n-1 = newest CLOSED bar) ---
double   g_open[], g_high[], g_low[], g_close[];
double   g_vol[];
datetime g_time[];       // broker server time
datetime g_utc[];        // UTC time
int      g_n = 0;

// --- computed series ---
double g_kalman[], g_rv[], g_rvma[], g_z[], g_rsi[], g_adx[], g_atr[];
double g_emaFast[], g_emaSlow[], g_macdHist[], g_stochK[];
int    g_regime[];

// --- HTF (resampled from the same window) ---
double   g_htfClose[];
double   g_htfEma[];
int      g_htfN = 0;

// --- state ---
datetime g_lastBarTime  = 0;
int      g_barsSince    = 0;      // mirrors _bars_since_signal
int      g_gmtOffsetSec = 0;
double   g_valuePerLot  = 0;
int      g_minBars      = 0;
int      g_csvHandle    = INVALID_HANDLE;
bool     g_csvSeeded    = false;
int      g_sessStart[], g_sessEnd[];
int      g_sessN        = 0;
string   g_objPrefix    = "KAL_";

// --- per-bar trace ---
string g_trace[];
int    g_traceN = 0;

//==================================================================
//  SMALL HELPERS
//==================================================================
bool IsNA(const double v) { return(!MathIsValidNumber(v) || v >= DBL_MAX * 0.5); }

void TraceReset() { g_traceN = 0; ArrayResize(g_trace, 0); }

void TraceAdd(const string line)
  {
   ArrayResize(g_trace, g_traceN + 1);
   g_trace[g_traceN] = line;
   g_traceN++;
  }

string TraceJoin(const string sep)
  {
   string s = "";
   for(int i = 0; i < g_traceN; i++)
      s += (i > 0 ? sep : "") + g_trace[i];
   return(s);
  }

string F(const double v, const int digits = 2)
  {
   if(IsNA(v)) return("n/a");
   return(DoubleToString(v, digits));
  }

//==================================================================
//  INDICATOR PRIMITIVES  (pandas-equivalent, index 0 = oldest)
//==================================================================

// pandas: series.rolling(w).mean()
void RollMean(const double &src[], const int n, const int w, double &dst[])
  {
   ArrayResize(dst, n);
   for(int i = 0; i < n; i++)
     {
      if(i < w - 1) { dst[i] = NA; continue; }
      double s = 0; bool bad = false;
      for(int k = i - w + 1; k <= i; k++)
        {
         if(IsNA(src[k])) { bad = true; break; }
         s += src[k];
        }
      dst[i] = bad ? NA : s / w;
     }
  }

// pandas: series.rolling(w).sum()
void RollSum(const double &src[], const int n, const int w, double &dst[])
  {
   ArrayResize(dst, n);
   for(int i = 0; i < n; i++)
     {
      if(i < w - 1) { dst[i] = NA; continue; }
      double s = 0; bool bad = false;
      for(int k = i - w + 1; k <= i; k++)
        {
         if(IsNA(src[k])) { bad = true; break; }
         s += src[k];
        }
      dst[i] = bad ? NA : s;
     }
  }

// pandas: series.rolling(w).std()  -- SAMPLE std, ddof = 1
void RollStd(const double &src[], const int n, const int w, double &dst[])
  {
   ArrayResize(dst, n);
   for(int i = 0; i < n; i++)
     {
      if(i < w - 1 || w < 2) { dst[i] = NA; continue; }
      double s = 0; bool bad = false;
      for(int k = i - w + 1; k <= i; k++)
        {
         if(IsNA(src[k])) { bad = true; break; }
         s += src[k];
        }
      if(bad) { dst[i] = NA; continue; }
      double m = s / w, q = 0;
      for(int k = i - w + 1; k <= i; k++)
         q += (src[k] - m) * (src[k] - m);
      dst[i] = MathSqrt(q / (w - 1));
     }
  }

// pandas: series.ewm(span=p, adjust=False).mean()  -- seeded at src[0]
void EwmSpan(const double &src[], const int n, const int p, double &dst[])
  {
   ArrayResize(dst, n);
   if(n <= 0) return;
   double a = 2.0 / (p + 1.0);
   dst[0] = src[0];
   for(int i = 1; i < n; i++)
      dst[i] = a * src[i] + (1.0 - a) * dst[i - 1];
  }

// scalar random-walk Kalman: x0 = y0, P0 = 1
void KalmanSeries(const double &y[], const int n, const double q, const double r, double &dst[])
  {
   ArrayResize(dst, n);
   if(n <= 0) return;
   double x = y[0], P = 1.0;
   dst[0] = x;
   for(int i = 1; i < n; i++)
     {
      double Pm = P + q;
      double K  = Pm / (Pm + r);
      x = x + K * (y[i] - x);
      P = (1.0 - K) * Pm;
      dst[i] = x;
     }
  }

// ATR = SMA(TrueRange, period). TR[0] = high[0]-low[0] (no previous close).
void AtrSeries(const int n, const int period, double &dst[])
  {
   double tr[]; ArrayResize(tr, n);
   for(int i = 0; i < n; i++)
     {
      if(i == 0) { tr[0] = g_high[0] - g_low[0]; continue; }
      double a = g_high[i] - g_low[i];
      double b = MathAbs(g_high[i] - g_close[i - 1]);
      double c = MathAbs(g_low[i]  - g_close[i - 1]);
      tr[i] = MathMax(a, MathMax(b, c));
     }
   RollMean(tr, n, period, dst);
  }

// ATR over an arbitrary period (used by the RANGE channel layer).
void AtrSeriesPeriod(const int n, const int period, double &dst[]) { AtrSeries(n, period, dst); }

void RsiSeries(const int n, const int period, double &dst[])
  {
   double gain[], loss[];
   ArrayResize(gain, n); ArrayResize(loss, n);
   for(int i = 0; i < n; i++)
     {
      if(i == 0) { gain[0] = 0; loss[0] = 0; continue; }   // diff() -> NaN -> where() -> 0
      double d = g_close[i] - g_close[i - 1];
      gain[i] = (d > 0) ? d : 0.0;
      loss[i] = (d < 0) ? -d : 0.0;
     }
   double ag[], al[];
   RollMean(gain, n, period, ag);
   RollMean(loss, n, period, al);

   ArrayResize(dst, n);
   for(int i = 0; i < n; i++)
     {
      if(IsNA(ag[i]) || IsNA(al[i])) { dst[i] = NA; continue; }
      if(al[i] == 0.0)
        {
         // pandas: x/0 -> inf  (rsi 100)  ;  0/0 -> NaN
         dst[i] = (ag[i] == 0.0) ? NA : 100.0;
         continue;
        }
      double rs = ag[i] / al[i];
      dst[i] = 100.0 - (100.0 / (1.0 + rs));
     }
  }

void AdxSeries(const int n, const int period, double &dst[])
  {
   double pdm[], mdm[];
   ArrayResize(pdm, n); ArrayResize(mdm, n);
   for(int i = 0; i < n; i++)
     {
      if(i == 0) { pdm[0] = 0; mdm[0] = 0; continue; }     // diff() -> NaN -> np.where -> 0
      double hd =  g_high[i] - g_high[i - 1];
      double ld = -(g_low[i] - g_low[i - 1]);
      pdm[i] = (hd > ld && hd > 0) ? hd : 0.0;
      mdm[i] = (ld > hd && ld > 0) ? ld : 0.0;
     }
   double sp[], sm[], atr[];
   RollSum(pdm, n, period, sp);
   RollSum(mdm, n, period, sm);
   AtrSeries(n, period, atr);

   double dx[]; ArrayResize(dx, n);
   for(int i = 0; i < n; i++)
     {
      if(IsNA(sp[i]) || IsNA(sm[i]) || IsNA(atr[i]) || atr[i] == 0.0) { dx[i] = NA; continue; }
      double pdi = 100.0 * sp[i] / atr[i];
      double mdi = 100.0 * sm[i] / atr[i];
      double den = pdi + mdi;
      if(den == 0.0) { dx[i] = NA; continue; }
      dx[i] = 100.0 * MathAbs(pdi - mdi) / den;
     }
   RollMean(dx, n, period, dst);
  }

void StochKSeries(const int n, const int period, double &dst[])
  {
   ArrayResize(dst, n);
   for(int i = 0; i < n; i++)
     {
      if(i < period - 1) { dst[i] = NA; continue; }
      double lo = g_low[i], hi = g_high[i];
      for(int k = i - period + 1; k <= i; k++)
        {
         if(g_low[k]  < lo) lo = g_low[k];
         if(g_high[k] > hi) hi = g_high[k];
        }
      dst[i] = (hi == lo) ? NA : 100.0 * (g_close[i] - lo) / (hi - lo);
     }
  }

void MacdHistSeries(const int n, const int fast, const int slow, const int sig, double &dst[])
  {
   double ef[], es[], line[], sl[];
   EwmSpan(g_close, n, fast, ef);
   EwmSpan(g_close, n, slow, es);
   ArrayResize(line, n);
   for(int i = 0; i < n; i++) line[i] = ef[i] - es[i];
   EwmSpan(line, n, sig, sl);
   ArrayResize(dst, n);
   for(int i = 0; i < n; i++) dst[i] = line[i] - sl[i];
  }

//==================================================================
//  HTF RESAMPLE  (pandas resample('1h') on the SAME window)
//==================================================================
//  Bars are binned by the UTC clock period of their OPEN time; empty bins are
//  dropped (pandas .agg(...).dropna()); the bin's close is its LAST close.
//  The EMA is seeded at the first bin in the window, exactly like the Python.
//------------------------------------------------------------------
int HtfSeconds(const ENUM_TIMEFRAMES tf) { return(PeriodSeconds(tf)); }

void BuildHtf()
  {
   g_htfN = 0;
   ArrayResize(g_htfClose, 0);
   int step = HtfSeconds(InpHtfTimeframe);
   if(step <= 0 || g_n <= 0) return;

   datetime curKey = 0;
   for(int i = 0; i < g_n; i++)
     {
      datetime key = (datetime)(((long)g_utc[i] / step) * step);
      if(g_htfN == 0 || key != curKey)
        {
         g_htfN++;
         ArrayResize(g_htfClose, g_htfN);
         curKey = key;
        }
      g_htfClose[g_htfN - 1] = g_close[i];   // 'last' within the bin
     }
   EwmSpan(g_htfClose, g_htfN, InpHtfEmaPeriod, g_htfEma);
  }

//==================================================================
//  SESSION FILTER
//==================================================================
void ParseSessions()
  {
   g_sessN = 0;
   ArrayResize(g_sessStart, 0);
   ArrayResize(g_sessEnd, 0);
   string parts[];
   int m = StringSplit(InpAllowedSessions, ',', parts);
   for(int i = 0; i < m; i++)
     {
      string t = parts[i];
      StringTrimLeft(t); StringTrimRight(t);
      if(StringLen(t) == 0) continue;
      int dash = StringFind(t, "-");
      int a, b;
      if(dash < 0) { a = (int)StringToInteger(t); b = a; }
      else
        {
         a = (int)StringToInteger(StringSubstr(t, 0, dash));
         b = (int)StringToInteger(StringSubstr(t, dash + 1));
        }
      g_sessN++;
      ArrayResize(g_sessStart, g_sessN);
      ArrayResize(g_sessEnd,   g_sessN);
      g_sessStart[g_sessN - 1] = a;
      g_sessEnd[g_sessN - 1]   = b;
     }
  }

bool SessionOk(const int utcHour)
  {
   if(!InpSessionFilterEnabled || g_sessN == 0) return(true);
   for(int i = 0; i < g_sessN; i++)
     {
      int a = g_sessStart[i], b = g_sessEnd[i];
      if(a <= b) { if(utcHour >= a && utcHour <= b) return(true); }
      else       { if(utcHour >= a || utcHour <= b) return(true); }
     }
   return(false);
  }

//==================================================================
//  RANGE STRUCTURAL LAYERS
//==================================================================
bool RangeChannelOk(const int last, string &reason)
  {
   double atrCh[];
   AtrSeriesPeriod(g_n, InpRangeChannelAtrPer, atrCh);
   double v = atrCh[last];
   if(IsNA(v) || v <= 0) { reason = "RANGE: channel ATR unavailable"; return(false); }

   int w = InpRangeChannelBars;
   if(w > g_n) w = g_n;
   double s = 0;
   for(int k = last - w + 1; k <= last; k++) s += g_close[k];
   double centre = s / w, maxDev = 0;
   for(int k = last - w + 1; k <= last; k++)
     {
      double d = MathAbs(g_close[k] - centre);
      if(d > maxDev) maxDev = d;
     }
   if(maxDev > InpRangeChannelAtrMult * v)
     {
      reason = StringFormat("RANGE: not range-bound (dev %.2f > %g" + "·ATR%d %.2f) — looks like a trend",
                            maxDev, InpRangeChannelAtrMult, InpRangeChannelAtrPer, v);
      return(false);
     }
   return(true);
  }

// Port of Indicators.detect_divergence (MACD-histogram regular divergence).
string DetectDivergence(const int last, const int lookback, const int pivotWindow)
  {
   int need = InpMacdSlow + InpMacdSignal + 2 * pivotWindow + 4;
   if(g_n < need) return("none");

   int start = last - lookback + 1;
   if(start < 0) start = 0;
   int m = last - start + 1;

   // Python runs the MACD on the TAIL SLICE, so its EMAs seed at the slice's
   // first bar, not the window's. Reproduce that or the histogram is wrong.
   double tailClose[], ef[], es[], line[], sl[], hist[];
   ArrayResize(tailClose, m);
   for(int i = 0; i < m; i++) tailClose[i] = g_close[start + i];
   EwmSpan(tailClose, m, InpMacdFast, ef);
   EwmSpan(tailClose, m, InpMacdSlow, es);
   ArrayResize(line, m);
   for(int i = 0; i < m; i++) line[i] = ef[i] - es[i];
   EwmSpan(line, m, InpMacdSignal, sl);
   ArrayResize(hist, m);
   for(int i = 0; i < m; i++) hist[i] = line[i] - sl[i];   // indexed TAIL-LOCAL

   int    phIdx[], plIdx[];
   double phP[], phO[], plP[], plO[];
   int    nph = 0, npl = 0;
   int w = pivotWindow;

   for(int i = w; i < m - w; i++)
     {
      int g = start + i;
      double hv = g_high[g];
      bool isHigh = true;
      for(int k = g - w; k <= g + w; k++) if(g_high[k] > hv) { isHigh = false; break; }
      if(isHigh && hv > g_high[g - 1])
        {
         nph++; ArrayResize(phIdx, nph); ArrayResize(phP, nph); ArrayResize(phO, nph);
         phIdx[nph - 1] = i; phP[nph - 1] = hv; phO[nph - 1] = hist[i];
        }
      double lv = g_low[g];
      bool isLow = true;
      for(int k = g - w; k <= g + w; k++) if(g_low[k] < lv) { isLow = false; break; }
      if(isLow && lv < g_low[g - 1])
        {
         npl++; ArrayResize(plIdx, npl); ArrayResize(plP, npl); ArrayResize(plO, npl);
         plIdx[npl - 1] = i; plP[npl - 1] = lv; plO[npl - 1] = hist[i];
        }
     }

   string kind = "none";
   int recency = -1;
   if(nph >= 2)
     {
      double p1 = phP[nph - 2], o1 = phO[nph - 2];
      double p2 = phP[nph - 1], o2 = phO[nph - 1];
      int    i2 = phIdx[nph - 1];
      if(p2 > p1 && o2 < o1 && o1 > 0 && i2 > recency) { kind = "bearish"; recency = i2; }
     }
   if(npl >= 2)
     {
      double q1 = plP[npl - 2], r1 = plO[npl - 2];
      double q2 = plP[npl - 1], r2 = plO[npl - 1];
      int    j2 = plIdx[npl - 1];
      if(q2 < q1 && r2 > r1 && r1 < 0 && j2 > recency) { kind = "bullish"; recency = j2; }
     }
   return(kind);
  }

bool RangePocOk(const int last, const double curAtr, string &reason)
  {
   int nb = InpRangePocBars;
   if(nb > g_n) nb = g_n;
   int start = last - nb + 1;

   double lo = g_low[start], hi = g_high[start], vsum = 0;
   for(int k = start; k <= last; k++)
     {
      if(g_low[k]  < lo) lo = g_low[k];
      if(g_high[k] > hi) hi = g_high[k];
      vsum += g_vol[k];
     }
   if(!(hi > lo) || vsum <= 0) { reason = "RANGE: volume profile unavailable"; return(false); }

   int bins = InpRangePocBins;
   double edges[], vbin[], centres[];
   ArrayResize(edges, bins + 1); ArrayResize(vbin, bins); ArrayResize(centres, bins);
   for(int b = 0; b <= bins; b++) edges[b] = lo + (hi - lo) * b / bins;
   for(int b = 0; b < bins; b++) { vbin[b] = 0; centres[b] = (edges[b] + edges[b + 1]) / 2.0; }

   for(int k = start; k <= last; k++)
     {
      double tp = (g_high[k] + g_low[k] + g_close[k]) / 3.0;
      int d = 0;                                   // np.digitize(tp, edges)
      while(d <= bins && tp >= edges[d]) d++;
      int idx = d - 1;
      if(idx < 0) idx = 0;
      if(idx > bins - 1) idx = bins - 1;
      vbin[idx] += g_vol[k];
     }

   double mean = 0;
   for(int b = 0; b < bins; b++) mean += vbin[b];
   mean /= bins;
   double var = 0;                                 // numpy std -> population (ddof=0)
   for(int b = 0; b < bins; b++) var += (vbin[b] - mean) * (vbin[b] - mean);
   double sd = MathSqrt(var / bins);
   double thresh = mean + 0.5 * sd;

   double cur = g_close[last], nearest = -1;
   for(int b = 0; b < bins; b++)
     {
      if(vbin[b] < thresh) continue;
      double d2 = MathAbs(centres[b] - cur);
      if(nearest < 0 || d2 < nearest) nearest = d2;
     }
   if(nearest < 0) { reason = "RANGE: volume profile unavailable"; return(false); }
   if(nearest > InpRangePocAtrMult * curAtr)
     {
      reason = StringFormat("RANGE: price %.2f not near a volume shelf (nearest %.2f > %g" + "·ATR)",
                            cur, nearest, InpRangePocAtrMult);
      return(false);
     }
   return(true);
  }

//==================================================================
//  TREND QUALITY SCORE
//==================================================================
double TrendQualityScore(const int last)
  {
   int sb = InpTqSlopeBars, sw = InpTqStdWindow;
   if(g_n <= sb + sw) return(1.0);
   double slope[]; ArrayResize(slope, g_n);
   for(int i = 0; i < g_n; i++)
      slope[i] = (i < sb) ? NA : MathAbs(g_kalman[i] - g_kalman[i - sb]);
   double sd[];
   RollStd(slope, g_n, sw, sd);
   if(IsNA(sd[last]) || IsNA(slope[last])) return(1.0);
   double cur = slope[last];
   return(cur / (cur + sd[last] + 1e-12));
  }

//==================================================================
//  DATA LOAD + INDICATOR REFRESH
//==================================================================
int MinBars()
  {
   int mb = MathMax(InpRvMaWindow, 100) + InpRvWindow + 10;
   if(InpHtfSellFilterEnabled || InpHtfBuyFilterEnabled)
     {
      int ratio = HtfSeconds(InpHtfTimeframe) / MathMax(1, PeriodSeconds(InpTimeframe));
      if(ratio < 1) ratio = 1;
      mb = MathMax(mb, (InpHtfEmaPeriod + 10) * ratio);
     }
   if(InpRangeChannelEnabled) mb = MathMax(mb, InpRangeChannelAtrPer + InpRangeChannelBars + 5);
   if(InpRangePocEnabled)     mb = MathMax(mb, InpRangePocBars + 5);
   if(InpRangeDivergenceEn)   mb = MathMax(mb, InpRangeDivLookback + 5);
   return(mb);
  }

bool LoadWindow()
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, InpTimeframe, 1, InpWarmupBars, r);   // shift 1 = skip forming bar
   if(got <= 0) return(false);
   g_n = got;

   ArrayResize(g_open, g_n); ArrayResize(g_high, g_n);
   ArrayResize(g_low,  g_n); ArrayResize(g_close, g_n);
   ArrayResize(g_vol,  g_n); ArrayResize(g_time, g_n); ArrayResize(g_utc, g_n);
   for(int i = 0; i < g_n; i++)
     {
      g_open[i]  = r[i].open;
      g_high[i]  = r[i].high;
      g_low[i]   = r[i].low;
      g_close[i] = r[i].close;
      g_vol[i]   = (double)r[i].tick_volume;
      g_time[i]  = r[i].time;
      g_utc[i]   = (datetime)((long)r[i].time - g_gmtOffsetSec);
     }
   return(true);
  }

void ComputeAll()
  {
   KalmanSeries(g_close, g_n, InpKalmanQ, InpKalmanR, g_kalman);

   // realized-vol regime
   double lr[]; ArrayResize(lr, g_n);
   for(int i = 0; i < g_n; i++)
      lr[i] = (i == 0 || g_close[i - 1] <= 0) ? NA : MathLog(g_close[i] / g_close[i - 1]);
   RollStd(lr, g_n, InpRvWindow, g_rv);
   RollMean(g_rv, g_n, InpRvMaWindow, g_rvma);
   ArrayResize(g_regime, g_n);
   for(int i = 0; i < g_n; i++)                       // NaN comparison -> False -> 0 (RANGE)
      g_regime[i] = (!IsNA(g_rv[i]) && !IsNA(g_rvma[i]) && g_rv[i] > g_rvma[i]) ? 1 : 0;

   // OU z-score off the Kalman line
   double dev[]; ArrayResize(dev, g_n);
   for(int i = 0; i < g_n; i++) dev[i] = g_close[i] - g_kalman[i];
   double sd[]; RollStd(dev, g_n, InpZscoreWindow, sd);
   ArrayResize(g_z, g_n);
   for(int i = 0; i < g_n; i++)
      g_z[i] = (IsNA(sd[i]) || sd[i] == 0.0) ? NA : dev[i] / sd[i];

   RsiSeries(g_n, 14, g_rsi);
   AdxSeries(g_n, 14, g_adx);
   AtrSeries(g_n, InpAtrPeriod, g_atr);

   if(InpEmaConfirmEnabled)
     {
      EwmSpan(g_close, g_n, InpEmaFastPeriod, g_emaFast);
      EwmSpan(g_close, g_n, InpEmaSlowPeriod, g_emaSlow);
     }
   else { ArrayResize(g_emaFast, 0); ArrayResize(g_emaSlow, 0); }

   if(InpMacdConfirmation) MacdHistSeries(g_n, InpMacdFast, InpMacdSlow, InpMacdSignal, g_macdHist);
   else                    ArrayResize(g_macdHist, 0);

   if(InpStochConfirmEnabled) StochKSeries(g_n, 14, g_stochK);
   else                       ArrayResize(g_stochK, 0);

   if(InpHtfSellFilterEnabled || InpHtfBuyFilterEnabled) BuildHtf();
  }

//==================================================================
//  THE DECISION  — mirrors KalmanRegimeStrategy.on_bar() gate for gate
//==================================================================
struct KDecision
  {
   bool   fired;
   int    side;             // SIDE_BUY / SIDE_SELL / SIDE_NONE
   double strength;
   double entry;
   double atr;
   string mode;             // "trend" / "range"
   string reject;           // first failing gate, Python wording
  };

bool Evaluate(KDecision &d)
  {
   d.fired = false; d.side = SIDE_NONE; d.strength = 0; d.entry = 0;
   d.atr = NA; d.mode = ""; d.reject = "";
   TraceReset();

   // ---- G1 symbol gate ----
   string sym = _Symbol; StringToUpper(sym);
   string pre = InpAllowedSymbolPrefix; StringToUpper(pre);
   if(StringFind(sym, pre) != 0)
     {
      d.reject = StringFormat("Symbol %s not in allowed set (%s) — kalman is gold-only", _Symbol, pre);
      return(false);
     }
   TraceAdd("G1 symbol      PASS  " + _Symbol + " starts with " + pre);

   // ---- G2 data ----
   if(g_n < g_minBars)
     {
      d.reject = StringFormat("Insufficient data: %d < %d", g_n, g_minBars);
      return(false);
     }
   TraceAdd(StringFormat("G2 data        PASS  %d bars >= %d", g_n, g_minBars));

   int last = g_n - 1;
   double close = g_close[last];

   // ---- G3 cooldown (increments only once past the data gate, as in Python) ----
   g_barsSince++;
   if(g_barsSince < InpCooldownBars)
     {
      d.reject = StringFormat("Cooldown: %d/%d bars", g_barsSince, InpCooldownBars);
      return(false);
     }
   TraceAdd(StringFormat("G3 cooldown    PASS  %d >= %d bars since last signal", g_barsSince, InpCooldownBars));

   // ---- G4 session ----
   MqlDateTime dt; TimeToStruct(g_utc[last], dt);
   if(!SessionOk(dt.hour))
     {
      d.reject = "Outside allowed session hours";
      return(false);
     }
   TraceAdd(StringFormat("G4 session     PASS  %02d:00 UTC in [%s]", dt.hour, InpAllowedSessions));

   double kal = g_kalman[last];
   double atr = g_atr[last];
   double rsi = IsNA(g_rsi[last]) ? 50.0 : g_rsi[last];
   double adx = IsNA(g_adx[last]) ? 0.0  : g_adx[last];
   double z   = IsNA(g_z[last])   ? 0.0  : g_z[last];

   if(IsNA(atr) || atr <= 0) { d.reject = "ATR unavailable"; return(false); }
   d.atr = atr;

   // ---- G5 regime ----
   bool isTrend = (g_regime[last] == 1);
   d.mode = isTrend ? "trend" : "range";
   TraceAdd(StringFormat("G5 regime      %s   RV %.6f %s MA(RV) %.6f",
                         isTrend ? "TREND" : "RANGE",
                         IsNA(g_rv[last]) ? 0.0 : g_rv[last],
                         isTrend ? ">" : "<=",
                         IsNA(g_rvma[last]) ? 0.0 : g_rvma[last]));

   int    side     = SIDE_NONE;
   double strength = 0.0;

   if(isTrend)
     {
      // ---- T1 ADX ----
      if(adx < InpTrendAdxMin)
        {
         d.reject = StringFormat("TREND mode: ADX too low (%.1f < %g)", adx, InpTrendAdxMin);
         return(false);
        }
      TraceAdd(StringFormat("T1 ADX         PASS  %.1f >= %g", adx, InpTrendAdxMin));

      // ---- T2 trend quality ----
      if(InpTrendQualityEnabled)
        {
         double tq = TrendQualityScore(last);
         if(tq < InpTqMinScore)
           {
            d.reject = StringFormat("TREND quality gate: score %.2f < %g", tq, InpTqMinScore);
            return(false);
           }
         TraceAdd(StringFormat("T2 quality     PASS  score %.2f >= %g", tq, InpTqMinScore));
        }

      bool above = (close > kal);
      bool below = (close < kal);
      double slope = g_kalman[last] - g_kalman[last - 2];   // kalman[-1] - kalman[-3]

      // ---- T6 acceleration (computed once, applied per side) ----
      bool accelOk = true;
      double accel = 0;
      if(InpKalmanAccelEnabled && g_n >= InpKalmanAccelBars + 2)
        {
         double slopeNow  = g_kalman[last] - g_kalman[last - 1];
         double slopePrev = g_kalman[g_n - InpKalmanAccelBars] - g_kalman[g_n - InpKalmanAccelBars - 1];
         accel = slopeNow - slopePrev;
         if(above && accel < 0) accelOk = false;
         if(below && accel > 0) accelOk = false;
        }

      if(above)
        {
         // ---- T4 confirmation: the N bars BEFORE this one, all above ----
         for(int k = last - InpKalmanConfirmBars; k <= last - 1; k++)
           {
            if(k < 0 || !(g_close[k] > g_kalman[k]))
              {
               d.reject = StringFormat("TREND BUY: not %d consecutive bars above Kalman", InpKalmanConfirmBars);
               return(false);
              }
           }
         TraceAdd(StringFormat("T3 side        BUY   close %.2f > kalman %.2f", close, kal));
         TraceAdd(StringFormat("T4 confirm     PASS  %d prior bars above Kalman", InpKalmanConfirmBars));

         if(slope <= 0)
           {
            d.reject = StringFormat("TREND BUY: Kalman slope flat/down (%.4f)", slope);
            return(false);
           }
         TraceAdd(StringFormat("T5 slope       PASS  +%.4f over 2 bars", slope));

         if(!accelOk) { d.reject = "TREND BUY: Kalman acceleration negative (trend weakening)"; return(false); }
         if(InpKalmanAccelEnabled) TraceAdd(StringFormat("T6 accel       PASS  %+.5f", accel));

         if(InpEmaConfirmEnabled)
           {
            if(g_emaFast[last] <= g_emaSlow[last])
              {
               d.reject = StringFormat("TREND BUY: EMA%d (%.2f) <= EMA%d (%.2f)",
                                       InpEmaFastPeriod, g_emaFast[last], InpEmaSlowPeriod, g_emaSlow[last]);
               return(false);
              }
            TraceAdd(StringFormat("T7 EMA         PASS  %.2f > %.2f", g_emaFast[last], g_emaSlow[last]));
           }
         if(InpMacdConfirmation)
           {
            double h = IsNA(g_macdHist[last]) ? 0.0 : g_macdHist[last];
            if(h <= 0)
              {
               d.reject = StringFormat("TREND BUY: MACD histogram negative (%.4f)", h);
               return(false);
              }
            TraceAdd(StringFormat("T8 MACD        PASS  hist %+.4f", h));
           }

         side = SIDE_BUY;
         double kd = MathMin(MathAbs(close - kal) / atr, 1.0);
         double ad = MathMin(adx / 50.0, 1.0);
         strength = 0.5 * kd + 0.3 * ad + 0.2 * (rsi / 100.0);
         TraceAdd(StringFormat("   strength    %.4f = 0.5*%.4f(dist) + 0.3*%.4f(adx) + 0.2*%.4f(rsi)",
                               strength, kd, ad, rsi / 100.0));
        }
      else if(below)
        {
         for(int k = last - InpKalmanConfirmBars; k <= last - 1; k++)
           {
            if(k < 0 || !(g_close[k] < g_kalman[k]))
              {
               d.reject = StringFormat("TREND SELL: not %d consecutive bars below Kalman", InpKalmanConfirmBars);
               return(false);
              }
           }
         TraceAdd(StringFormat("T3 side        SELL  close %.2f < kalman %.2f", close, kal));
         TraceAdd(StringFormat("T4 confirm     PASS  %d prior bars below Kalman", InpKalmanConfirmBars));

         if(slope >= 0)
           {
            d.reject = StringFormat("TREND SELL: Kalman slope flat/up (%.4f)", slope);
            return(false);
           }
         TraceAdd(StringFormat("T5 slope       PASS  %.4f over 2 bars", slope));

         if(!accelOk) { d.reject = "TREND SELL: Kalman acceleration positive (trend weakening)"; return(false); }
         if(InpKalmanAccelEnabled) TraceAdd(StringFormat("T6 accel       PASS  %+.5f", accel));

         if(InpEmaConfirmEnabled)
           {
            if(g_emaFast[last] >= g_emaSlow[last])
              {
               d.reject = StringFormat("TREND SELL: EMA%d (%.2f) >= EMA%d (%.2f)",
                                       InpEmaFastPeriod, g_emaFast[last], InpEmaSlowPeriod, g_emaSlow[last]);
               return(false);
              }
            TraceAdd(StringFormat("T7 EMA         PASS  %.2f < %.2f", g_emaFast[last], g_emaSlow[last]));
           }
         if(InpMacdConfirmation)
           {
            double h = IsNA(g_macdHist[last]) ? 0.0 : g_macdHist[last];
            if(h >= 0)
              {
               d.reject = StringFormat("TREND SELL: MACD histogram positive (%.4f)", h);
               return(false);
              }
            TraceAdd(StringFormat("T8 MACD        PASS  hist %+.4f", h));
           }

         side = SIDE_SELL;
         double kd = MathMin(MathAbs(close - kal) / atr, 1.0);
         double ad = MathMin(adx / 50.0, 1.0);
         strength = 0.5 * kd + 0.3 * ad + 0.2 * ((100.0 - rsi) / 100.0);
         TraceAdd(StringFormat("   strength    %.4f = 0.5*%.4f(dist) + 0.3*%.4f(adx) + 0.2*%.4f(rsi-inv)",
                               strength, kd, ad, (100.0 - rsi) / 100.0));
        }
     }
   else
     {
      // ---- RANGE ----
      if(z < -InpEntryThreshold && rsi < InpRangeRsiBuy)
        {
         if(InpStochConfirmEnabled)
           {
            double k = IsNA(g_stochK[last]) ? 50.0 : g_stochK[last];
            if(k > InpStochOversold)
              {
               d.reject = StringFormat("RANGE BUY: Stoch K (%.1f) > %g", k, InpStochOversold);
               return(false);
              }
            TraceAdd(StringFormat("R2 stoch       PASS  %%K %.1f <= %g", k, InpStochOversold));
           }
         side = SIDE_BUY;
         strength = MathMin(MathAbs(z) / (InpEntryThreshold * 1.5), 1.0);
         TraceAdd(StringFormat("R1 z + RSI     BUY   z %.2f < -%g and RSI %.1f < %g", z, InpEntryThreshold, rsi, InpRangeRsiBuy));
         TraceAdd(StringFormat("   strength    %.4f = min(|z|/%.2f, 1)", strength, InpEntryThreshold * 1.5));
        }
      else if(z > InpEntryThreshold && rsi > InpRangeRsiSell)
        {
         if(InpStochConfirmEnabled)
           {
            double k = IsNA(g_stochK[last]) ? 50.0 : g_stochK[last];
            if(k < InpStochOverbought)
              {
               d.reject = StringFormat("RANGE SELL: Stoch K (%.1f) < %g", k, InpStochOverbought);
               return(false);
              }
            TraceAdd(StringFormat("R2 stoch       PASS  %%K %.1f >= %g", k, InpStochOverbought));
           }
         side = SIDE_SELL;
         strength = MathMin(MathAbs(z) / (InpEntryThreshold * 1.5), 1.0);
         TraceAdd(StringFormat("R1 z + RSI     SELL  z %.2f > %g and RSI %.1f > %g", z, InpEntryThreshold, rsi, InpRangeRsiSell));
         TraceAdd(StringFormat("   strength    %.4f = min(|z|/%.2f, 1)", strength, InpEntryThreshold * 1.5));
        }

      if(side != SIDE_NONE)
        {
         string reason = "";
         if(InpRangeChannelEnabled && !RangeChannelOk(last, reason)) { d.reject = reason; return(false); }
         if(InpRangeChannelEnabled) TraceAdd("R3 channel     PASS  window inside the ATR band");

         if(InpRangeDivergenceEn)
           {
            string want = (side == SIDE_BUY) ? "bullish" : "bearish";
            string got  = DetectDivergence(last, InpRangeDivLookback, 3);
            if(got != want)
              {
               d.reject = StringFormat("RANGE: no %s exhaustion divergence (got %s)", want, got);
               return(false);
              }
            TraceAdd("R4 divergence  PASS  " + want + " MACD-hist divergence");
           }
         if(InpRangePocEnabled && !RangePocOk(last, atr, reason)) { d.reject = reason; return(false); }
         if(InpRangePocEnabled) TraceAdd("R5 volume node PASS  price near a high-volume shelf");
        }
     }

   if(side == SIDE_NONE)
     {
      d.reject = StringFormat("No signal in %s mode (close=%.2f, kalman=%.2f, z=%.2f, adx=%.1f, rsi=%.1f)",
                              isTrend ? "TREND" : "RANGE", close, kal, z, adx, rsi);
      return(false);
     }

   // ---- G6 long-only ----
   if(InpLongOnly && side == SIDE_SELL)
     {
      d.reject = "Long-only mode: SELL signal suppressed";
      return(false);
     }

   // ---- G7 HTF directional gate ----
   bool gateSell = (InpHtfSellFilterEnabled && side == SIDE_SELL);
   bool gateBuy  = (InpHtfBuyFilterEnabled  && side == SIDE_BUY);
   if(gateSell || gateBuy)
     {
      if(g_htfN < InpHtfEmaPeriod + 1)
        {
         d.reject = StringFormat("HTF filter: insufficient %s bars", EnumToString(InpHtfTimeframe));
         return(false);
        }
      double hc = g_htfClose[g_htfN - 1];
      double he = g_htfEma[g_htfN - 1];
      if(gateSell && hc >= he)
        {
         d.reject = StringFormat("HTF SELL filter: %s close %.2f >= EMA%d %.2f — bullish HTF",
                                 EnumToString(InpHtfTimeframe), hc, InpHtfEmaPeriod, he);
         return(false);
        }
      if(gateBuy && hc <= he)
        {
         d.reject = StringFormat("HTF BUY filter: %s close %.2f <= EMA%d %.2f — bearish HTF",
                                 EnumToString(InpHtfTimeframe), hc, InpHtfEmaPeriod, he);
         return(false);
        }
      TraceAdd(StringFormat("G7 HTF gate    PASS  %s close %.2f %s EMA%d %.2f",
                            EnumToString(InpHtfTimeframe), hc, gateSell ? "<" : ">", InpHtfEmaPeriod, he));
     }

   // ---- G8 minimum strength ----
   double minStr = (side == SIDE_SELL) ? InpMinSignalStrengthSell : InpMinSignalStrength;
   if(strength < minStr)
     {
      d.reject = StringFormat("Kalman signal strength too low (%.2f < %g)", strength, minStr);
      return(false);
     }
   TraceAdd(StringFormat("G8 strength    PASS  %.4f >= %g (%s gate)", strength, minStr, side == SIDE_SELL ? "SELL" : "BUY"));

   // ---- FIRE ----
   g_barsSince = 0;
   d.fired    = true;
   d.side     = side;
   d.strength = strength;
   d.entry    = close;
   return(true);
  }

//==================================================================
//  SIZING + BUDGET SL / RR TP
//==================================================================
double AutoValuePerLot()
  {
   if(InpValuePerLot > 0) return(InpValuePerLot);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize > 0 && tickVal > 0) return(tickVal / tickSize);   // $ per 1.0 price move per lot
   return(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE));
  }

// Returns lot (0 on failure) and fills sl/tp. `why` explains a rejection.
double PlanTrade(const KDecision &d, double &sl, double &tp, string &why)
  {
   sl = 0; tp = 0; why = "";
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double vpl     = g_valuePerLot;
   if(vpl <= 0) { why = "value_per_lot unavailable"; return(0); }

   // 1. provisional ATR stop -- SIZING INPUT ONLY
   double provSl = InpSlAtrMultiplier * d.atr;
   if(provSl <= 0) { why = "provisional ATR stop is zero"; return(0); }

   // 2. risk-scaled lot
   double riskPct = InpSizingRiskPct;
   double scale   = 1.0;
   if(InpStrengthScaling)
     {
      double s = MathMax(0.0, MathMin(1.0, d.strength));
      scale = MathMax(0.7, MathMin(1.3, 0.7 + 0.6 * s));
      scale = NormalizeDouble(scale, 4);
      riskPct *= scale;
     }
   double riskAmt    = balance * riskPct;
   double riskPerLot = provSl * vpl;
   if(riskPerLot <= 0) { why = "risk per lot is zero"; return(0); }

   double raw = riskAmt / riskPerLot;
   double lot = MathFloor(raw / InpLotStep) * InpLotStep;
   lot = MathMax(InpMinLot, MathMin(InpMaxLot, lot));
   lot = NormalizeDouble(lot, 2);
   if(lot <= 0) { why = "computed lot is zero"; return(0); }

   // 3. budget SL -- the stop that actually reaches the broker
   double maxLoss = (InpMaxLossUSD > 0) ? InpMaxLossUSD : balance * InpBudgetRiskPct;
   if(maxLoss <= 0) { why = "max loss budget is zero"; return(0); }
   double slDist = maxLoss / (lot * vpl);

   double bufferedMin = (InpMinStopsDistance > 0) ? InpMinStopsDistance * 1.05 : 0.0;
   if(bufferedMin > 0 && slDist < bufferedMin)
     {
      why = StringFormat("[BudgetSL] Required SL %.2f < broker min %.2f (max_loss %.2f, lot %.2f) — skipping trade",
                         slDist, bufferedMin, maxLoss, lot);
      return(0);
     }

   // broker's own stop level, on top of the config floor
   double point   = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double stopLvl = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   if(stopLvl > 0 && slDist < stopLvl * 1.05)
     {
      why = StringFormat("[BudgetSL] Required SL %.2f below broker stops level %.2f — skipping trade", slDist, stopLvl);
      return(0);
     }

   if(d.side == SIDE_BUY) { sl = d.entry - slDist; tp = d.entry + slDist * InpRewardRiskRatio; }
   else                   { sl = d.entry + slDist; tp = d.entry - slDist * InpRewardRiskRatio; }

   TraceAdd(StringFormat("   size        lot %.2f = %.2f x %.5f%s / (%.2f x %.0f)",
                         lot, balance, riskPct, InpStrengthScaling ? StringFormat(" [x%.4f str]", scale) : "", provSl, vpl));
   TraceAdd(StringFormat("   budget SL   %.2f pts = $%.2f / (%.2f lot x %.0f)  -> SL %.2f", slDist, maxLoss, lot, vpl, sl));
   TraceAdd(StringFormat("   RR TP       %.2f pts = %g x SL              -> TP %.2f", slDist * InpRewardRiskRatio, InpRewardRiskRatio, tp));
   return(lot);
  }

int CountOwnPositions()
  {
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      c++;
     }
   return(c);
  }

ENUM_ORDER_TYPE_FILLING PickFilling()
  {
   long mode = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((mode & SYMBOL_FILLING_FOK) != 0) return(ORDER_FILLING_FOK);
   if((mode & SYMBOL_FILLING_IOC) != 0) return(ORDER_FILLING_IOC);
   return(ORDER_FILLING_RETURN);
  }

bool SendOrder(const KDecision &d, const double lot, const double sl, const double tp)
  {
   MqlTradeRequest  req; MqlTradeResult res;
   ZeroMemory(req); ZeroMemory(res);

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = _Symbol;
   req.volume       = lot;
   req.type         = (d.side == SIDE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   req.price        = (d.side == SIDE_BUY) ? ask : bid;
   req.sl           = NormalizeDouble(sl, digits);
   req.tp           = NormalizeDouble(tp, digits);
   req.deviation    = InpSlippage;
   req.magic        = InpMagic;
   req.type_filling = PickFilling();
   req.comment      = "kalman_" + d.mode;

   if(!OrderSend(req, res))
     {
      PrintFormat("[KAL] OrderSend failed: retcode=%d %s", res.retcode, res.comment);
      return(false);
     }
   if(res.retcode != TRADE_RETCODE_DONE && res.retcode != TRADE_RETCODE_PLACED)
     {
      PrintFormat("[KAL] Order rejected: retcode=%d %s", res.retcode, res.comment);
      return(false);
     }
   PrintFormat("[KAL] %s %.2f lots @ %.2f  SL %.2f  TP %.2f  ticket=%I64u",
               (d.side == SIDE_BUY) ? "BUY" : "SELL", lot, res.price, req.sl, req.tp, res.order);
   return(true);
  }

//==================================================================
//  CHART MARKING
//==================================================================
void DrawEntry(const KDecision &d, const datetime t, const double lot, const double sl, const double tp)
  {
   if(!InpDrawMarkers) return;

   string base  = g_objPrefix + (string)(long)t;
   string arrow = base + "_a";
   string label = base + "_t";
   bool   buy   = (d.side == SIDE_BUY);
   color  col   = buy ? clrDodgerBlue : clrOrangeRed;

   double y = buy ? g_low[g_n - 1] - d.atr * 0.5 : g_high[g_n - 1] + d.atr * 0.5;

   if(ObjectCreate(0, arrow, OBJ_ARROW, 0, t, y))
     {
      ObjectSetInteger(0, arrow, OBJPROP_ARROWCODE, buy ? 233 : 234);
      ObjectSetInteger(0, arrow, OBJPROP_COLOR, col);
      ObjectSetInteger(0, arrow, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, arrow, OBJPROP_ANCHOR, buy ? ANCHOR_TOP : ANCHOR_BOTTOM);
      // The whole reason this EA exists: the full gate trace lives on the arrow.
      ObjectSetString(0, arrow, OBJPROP_TOOLTIP, TraceJoin("\n"));
     }

   string txt = StringFormat("%s %s  str %.2f  lot %.2f", buy ? "BUY" : "SELL",
                             d.mode == "trend" ? "TREND" : "RANGE", d.strength, lot);
   if(ObjectCreate(0, label, OBJ_TEXT, 0, t, buy ? y - d.atr * 0.4 : y + d.atr * 0.4))
     {
      ObjectSetString(0, label, OBJPROP_TEXT, txt);
      ObjectSetInteger(0, label, OBJPROP_COLOR, col);
      ObjectSetInteger(0, label, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, label, OBJPROP_ANCHOR, buy ? ANCHOR_UPPER : ANCHOR_LOWER);
      ObjectSetString(0, label, OBJPROP_TOOLTIP, TraceJoin("\n"));
     }
  }

void ShowPanel(const KDecision &d, const datetime t)
  {
   if(!InpShowPanel) return;
   string s = "KALMAN REGIME  " + _Symbol + "  " + EnumToString(InpTimeframe) + "\n";
   s += "bar " + TimeToString(t, TIME_DATE | TIME_MINUTES) + " (server)  |  ";
   s += TimeToString(g_utc[g_n - 1], TIME_DATE | TIME_MINUTES) + " UTC\n";
   s += "------------------------------------------------------------\n";
   s += TraceJoin("\n");
   if(!d.fired)
      s += (g_traceN > 0 ? "\n" : "") + "STOP >> " + d.reject;
   else
      s += "\n>>> ENTRY TAKEN <<<";
   Comment(s);
  }

void ClearObjects()
  {
   ObjectsDeleteAll(0, g_objPrefix);
   Comment("");
  }

//==================================================================
//  PARITY CSV
//==================================================================
string CsvNum(const double v, const int digits = 10)
  {
   if(IsNA(v)) return("");
   return(DoubleToString(v, digits));
  }

string UtcStamp(const datetime t) { return(TimeToString(t, TIME_DATE | TIME_SECONDS)); }

void WriteMeta()
  {
   int h = FileOpen(InpMetaName, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE) { PrintFormat("[KAL] meta open failed: %d", GetLastError()); return; }
   FileWrite(h, "key", "value");
   FileWrite(h, "symbol", _Symbol);
   FileWrite(h, "timeframe_seconds", (string)PeriodSeconds(InpTimeframe));
   FileWrite(h, "warmup_bars", (string)InpWarmupBars);
   FileWrite(h, "min_bars", (string)g_minBars);
   FileWrite(h, "gmt_offset_hours", (string)(g_gmtOffsetSec / 3600));
   FileWrite(h, "allowed_symbols", InpAllowedSymbolPrefix);
   FileWrite(h, "kalman_q", DoubleToString(InpKalmanQ, 12));
   FileWrite(h, "kalman_r", DoubleToString(InpKalmanR, 12));
   FileWrite(h, "rv_window", (string)InpRvWindow);
   FileWrite(h, "rv_ma_window", (string)InpRvMaWindow);
   FileWrite(h, "zscore_window", (string)InpZscoreWindow);
   FileWrite(h, "entry_threshold", DoubleToString(InpEntryThreshold, 6));
   FileWrite(h, "trend_adx_min", DoubleToString(InpTrendAdxMin, 6));
   FileWrite(h, "kalman_confirm_bars", (string)InpKalmanConfirmBars);
   FileWrite(h, "atr_period", (string)InpAtrPeriod);
   FileWrite(h, "cooldown_bars", (string)InpCooldownBars);
   FileWrite(h, "long_only", InpLongOnly ? "true" : "false");
   FileWrite(h, "min_signal_strength", DoubleToString(InpMinSignalStrength, 6));
   FileWrite(h, "min_signal_strength_sell", DoubleToString(InpMinSignalStrengthSell, 6));
   FileWrite(h, "session_filter_enabled", InpSessionFilterEnabled ? "true" : "false");
   FileWrite(h, "allowed_sessions", InpAllowedSessions);
   FileWrite(h, "ema_confirm_enabled", InpEmaConfirmEnabled ? "true" : "false");
   FileWrite(h, "ema_fast_period", (string)InpEmaFastPeriod);
   FileWrite(h, "ema_slow_period", (string)InpEmaSlowPeriod);
   FileWrite(h, "macd_confirmation", InpMacdConfirmation ? "true" : "false");
   FileWrite(h, "macd_fast", (string)InpMacdFast);
   FileWrite(h, "macd_slow", (string)InpMacdSlow);
   FileWrite(h, "macd_signal", (string)InpMacdSignal);
   FileWrite(h, "kalman_accel_enabled", InpKalmanAccelEnabled ? "true" : "false");
   FileWrite(h, "kalman_accel_bars", (string)InpKalmanAccelBars);
   FileWrite(h, "trend_quality_gate_enabled", InpTrendQualityEnabled ? "true" : "false");
   FileWrite(h, "trend_quality_slope_bars", (string)InpTqSlopeBars);
   FileWrite(h, "trend_quality_std_window", (string)InpTqStdWindow);
   FileWrite(h, "trend_quality_min_score", DoubleToString(InpTqMinScore, 6));
   FileWrite(h, "range_rsi_buy", DoubleToString(InpRangeRsiBuy, 6));
   FileWrite(h, "range_rsi_sell", DoubleToString(InpRangeRsiSell, 6));
   FileWrite(h, "stoch_confirm_enabled", InpStochConfirmEnabled ? "true" : "false");
   FileWrite(h, "stoch_oversold", DoubleToString(InpStochOversold, 6));
   FileWrite(h, "stoch_overbought", DoubleToString(InpStochOverbought, 6));
   FileWrite(h, "range_channel_enabled", InpRangeChannelEnabled ? "true" : "false");
   FileWrite(h, "range_channel_bars", (string)InpRangeChannelBars);
   FileWrite(h, "range_channel_atr_period", (string)InpRangeChannelAtrPer);
   FileWrite(h, "range_channel_atr_mult", DoubleToString(InpRangeChannelAtrMult, 6));
   FileWrite(h, "range_divergence_enabled", InpRangeDivergenceEn ? "true" : "false");
   FileWrite(h, "range_divergence_lookback", (string)InpRangeDivLookback);
   FileWrite(h, "range_poc_enabled", InpRangePocEnabled ? "true" : "false");
   FileWrite(h, "range_poc_bars", (string)InpRangePocBars);
   FileWrite(h, "range_poc_bins", (string)InpRangePocBins);
   FileWrite(h, "range_poc_atr_mult", DoubleToString(InpRangePocAtrMult, 6));
   FileWrite(h, "htf_sell_filter_enabled", InpHtfSellFilterEnabled ? "true" : "false");
   FileWrite(h, "htf_buy_filter_enabled", InpHtfBuyFilterEnabled ? "true" : "false");
   FileWrite(h, "htf_resample_seconds", (string)PeriodSeconds(InpHtfTimeframe));
   FileWrite(h, "htf_ema_period", (string)InpHtfEmaPeriod);
   FileClose(h);
  }

void CsvOpen()
  {
   if(!InpExportCSV) return;
   g_csvHandle = FileOpen(InpCsvName, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(g_csvHandle == INVALID_HANDLE)
     {
      PrintFormat("[KAL] CSV open failed: %d", GetLastError());
      return;
     }
   FileWrite(g_csvHandle,
             "time_utc", "open", "high", "low", "close", "tick_volume",
             "evaluated", "kalman", "rv", "rv_ma", "regime", "zscore",
             "rsi", "adx", "atr", "htf_close", "htf_ema",
             "fired", "side", "mode", "strength", "reject");
  }

void CsvSeedHistory()
  {
   // The evaluation window's leading bars have never been emitted; write them
   // OHLC-only (evaluated=0) so the Python replay has the same history.
   if(g_csvHandle == INVALID_HANDLE || g_csvSeeded) return;
   for(int i = 0; i < g_n - 1; i++)
      FileWrite(g_csvHandle, UtcStamp(g_utc[i]),
                CsvNum(g_open[i], 8), CsvNum(g_high[i], 8), CsvNum(g_low[i], 8), CsvNum(g_close[i], 8),
                CsvNum(g_vol[i], 0), "0", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "");
   g_csvSeeded = true;
  }

void CsvRow(const KDecision &d)
  {
   if(g_csvHandle == INVALID_HANDLE) return;
   int i = g_n - 1;
   double hc = (g_htfN > 0) ? g_htfClose[g_htfN - 1] : NA;
   double he = (g_htfN > 0) ? g_htfEma[g_htfN - 1]   : NA;

   FileWrite(g_csvHandle, UtcStamp(g_utc[i]),
             CsvNum(g_open[i], 8), CsvNum(g_high[i], 8), CsvNum(g_low[i], 8), CsvNum(g_close[i], 8),
             CsvNum(g_vol[i], 0), "1",
             CsvNum(g_kalman[i]), CsvNum(g_rv[i]), CsvNum(g_rvma[i]), (string)g_regime[i], CsvNum(g_z[i]),
             CsvNum(g_rsi[i]), CsvNum(g_adx[i]), CsvNum(g_atr[i]), CsvNum(hc), CsvNum(he),
             d.fired ? "1" : "0",
             d.side == SIDE_BUY ? "BUY" : (d.side == SIDE_SELL ? "SELL" : ""),
             d.mode, CsvNum(d.strength, 10), d.reject);
   FileFlush(g_csvHandle);
  }

//==================================================================
//  LIFECYCLE
//==================================================================
int OnInit()
  {
   g_minBars = MinBars();
   if(InpWarmupBars < g_minBars)
     {
      PrintFormat("[KAL] InpWarmupBars (%d) < required min_bars (%d) — raise it.", InpWarmupBars, g_minBars);
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpKalmanQ <= 0 || InpKalmanR <= 0)
     {
      Print("[KAL] kalman q and r must be positive.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpLotStep <= 0 || InpMinLot <= 0 || InpMaxLot < InpMinLot)
     {
      Print("[KAL] lot bounds are inconsistent.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   g_gmtOffsetSec = (InpBrokerGmtOffsetHours == 99)
                    ? (int)(MathRound((double)(TimeCurrent() - TimeGMT()) / 3600.0) * 3600)
                    : InpBrokerGmtOffsetHours * 3600;

   ParseSessions();
   g_valuePerLot = AutoValuePerLot();
   g_barsSince   = InpCooldownBars;      // mirrors _bars_since_signal init
   g_lastBarTime = 0;

   string sym = _Symbol; StringToUpper(sym);
   string pre = InpAllowedSymbolPrefix; StringToUpper(pre);
   if(StringFind(sym, pre) != 0)
      PrintFormat("[KAL] WARNING: %s does not start with %s — this EA will never trade here.", _Symbol, pre);

   PrintFormat("[KAL] init: TF=%s window=%d min_bars=%d gmt_offset=%+dh value_per_lot=%.2f sessions=%d dry_run=%s",
               EnumToString(InpTimeframe), InpWarmupBars, g_minBars, g_gmtOffsetSec / 3600,
               g_valuePerLot, g_sessN, InpDryRun ? "YES" : "no");

   if(InpExportCSV) { CsvOpen(); WriteMeta(); }
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_csvHandle != INVALID_HANDLE) { FileClose(g_csvHandle); g_csvHandle = INVALID_HANDLE; }
   ClearObjects();
  }

void OnTick()
  {
   datetime bt = iTime(_Symbol, InpTimeframe, 1);   // last CLOSED bar
   if(bt == 0 || bt == g_lastBarTime) return;

   if(!LoadWindow()) return;
   if(g_n < 3) return;
   ComputeAll();
   g_lastBarTime = bt;

   if(InpExportCSV) CsvSeedHistory();

   KDecision d;
   Evaluate(d);

   if(!d.fired)
     {
      if(InpLogRejections) PrintFormat("[KAL] %s  no signal: %s", TimeToString(g_utc[g_n - 1], TIME_DATE | TIME_MINUTES), d.reject);
      ShowPanel(d, bt);
      if(InpExportCSV) CsvRow(d);
      return;
     }

   // --- signal fired: plan the trade ---
   double sl, tp, lot; string why;
   lot = PlanTrade(d, sl, tp, why);

   if(lot <= 0)
     {
      TraceAdd("   SKIPPED     " + why);
      PrintFormat("[KAL] %s  SIGNAL %s %s but not taken: %s",
                  TimeToString(g_utc[g_n - 1], TIME_DATE | TIME_MINUTES),
                  d.side == SIDE_BUY ? "BUY" : "SELL", d.mode, why);
      Print("[KAL] gate trace:\n" + TraceJoin("\n"));
      ShowPanel(d, bt);
      if(InpExportCSV) CsvRow(d);
      return;
     }

   int open = CountOwnPositions();
   if(open >= InpMaxPositions)
     {
      TraceAdd(StringFormat("   SKIPPED     max positions reached (%d/%d)", open, InpMaxPositions));
      PrintFormat("[KAL] signal suppressed: %d/%d positions already open", open, InpMaxPositions);
      ShowPanel(d, bt);
      if(InpExportCSV) CsvRow(d);
      return;
     }

   if(InpMaxSpreadPoints > 0)
     {
      double point  = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      double spread = (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / point;
      if(spread > InpMaxSpreadPoints)
        {
         TraceAdd(StringFormat("   SKIPPED     spread %.0f > %.0f points", spread, InpMaxSpreadPoints));
         PrintFormat("[KAL] signal suppressed: spread %.0f > %.0f points", spread, InpMaxSpreadPoints);
         ShowPanel(d, bt);
         if(InpExportCSV) CsvRow(d);
         return;
        }
     }

   // --- the full basis for this trade, in one block ---
   Print("[KAL] ================ ENTRY =================\n" +
         StringFormat("[KAL] %s  %s %s @ %.2f\n", TimeToString(g_utc[g_n - 1], TIME_DATE | TIME_MINUTES),
                      d.side == SIDE_BUY ? "BUY" : "SELL", d.mode, d.entry) +
         TraceJoin("\n") +
         "\n[KAL] ========================================");

   if(!InpDryRun) SendOrder(d, lot, sl, tp);
   else           Print("[KAL] DRY RUN — no order sent.");

   DrawEntry(d, bt, lot, sl, tp);
   ShowPanel(d, bt);
   if(InpExportCSV) CsvRow(d);
  }
//+------------------------------------------------------------------+
