//+------------------------------------------------------------------+
//|                                   GoldHTF_AutoOptimizer_EA.mq5   |
//|            Multi-TF ICT Strategy with Dynamic Regime Detection   |
//+------------------------------------------------------------------+
#property copyright "Auto-Optimizer Edition"
#property version   "3.00"

//--- Standard Input Groups
input group "=== RISK MANAGEMENT ==="
input double   InpLotSize       = 0.01;        // Fixed Lot (used only when Risk% <= 0)
input double   InpRiskPercent    = 1.0;        // Risk % per trade (>0 overrides Fixed Lot)
input int      InpMaxSpread     = 50;          // Max Spread (points)
input int      InpSlippage      = 30;          // Max Slippage
input int      InpMaxOpenTrades = 1;           // Max concurrent positions (1 = one at a time)
input double   InpMinLot        = 0.02;        // Lot floor (raised above broker minimum)
input double   InpMaxLot        = 1.00;        // Lot ceiling (lowered below broker maximum)

input group "=== TIMEFRAME SETTINGS ==="
input ENUM_TIMEFRAMES InpTF_HTF1  = PERIOD_H4;  // HTF1 - Direction (4H)
input ENUM_TIMEFRAMES InpTF_HTF2  = PERIOD_H1;  // HTF2 - FVG/OB Zone (1H)
input ENUM_TIMEFRAMES InpTF_MTF   = PERIOD_M15; // MTF - Structure (15min)
input ENUM_TIMEFRAMES InpTF_ENTRY = PERIOD_M5;  // Entry TF (5min)

input group "=== H4 DOUBLE-FVG SETUP ==="
// FAILED the repo backtest gate 2026-08-09 (reports/double_fvg_research.md):
// XAUUSD 2022-2026 strict fills, PF 1.09 on 34 trades/yr, 2 of 6 gates passed,
// and it beats only 58% of matched random-entry draws. Ablation shows neither the
// "double" nor the "first gap" condition is load-bearing.
// ENABLED BY USER DECISION 2026-08-09 pending their own MT5 Strategy Tester run.
input bool     InpUseDoubleFVG   = true;        // Enable H4 double-FVG entry path
input bool     InpDFVG_Only      = false;       // true = run ONLY the double-FVG path
input ENUM_TIMEFRAMES InpDFVG_TF = PERIOD_H4;   // FVG detection timeframe
input int      InpDFVG_Lookback  = 60;          // Bars to scan for FVGs
input double   InpDFVG_MinATR    = 0.25;        // Min FVG size (x ATR of the FVG TF)
input int      InpDFVG_PairWindow= 12;          // Max bars between FVG #1 and FVG #2
input int      InpDFVG_MaxAgeBars= 40;          // Discard zone older than N bars
input double   InpDFVG_TapMinPct = 50.0;        // Must reach this % depth into FVG #1
input double   InpDFVG_TapMaxPct = 60.0;        // Must close back beyond this % depth
input bool     InpDFVG_RequireColor = true;     // Trigger candle must match FVG direction
input bool     InpDFVG_RequireTrend = false;    // Also require HTF trend agreement

input group "=== FVG & ORDER BLOCK (legacy chain) ==="
input bool     InpUseFVG        = true;
input bool     InpUseOB         = true;
input int      InpFVGMinPips    = 5;           // Base FVG Min Pips (Auto-adjusted)
input int      InpOBLookback    = 10;

input group "=== CANDLESTICK PATTERNS ==="
input bool     InpUseEngulfing  = true;
input bool     InpUseHammer     = true;
input bool     InpUseInvHammer  = true;
input bool     InpUsePiercing   = true;
input bool     InpUse3Candle    = true;
input bool     InpUseWM         = true;

input group "=== TREND FILTERS ==="
input bool     InpUseMAFilter   = true;
input int      InpMAFast        = 9;
input int      InpMASlow        = 21;
input bool     InpUseRSIFilter  = true;
input int      InpRSIPeriod     = 14;

input group "=== SESSION & VOLATILITY ==="
input bool     InpUseSession    = true;
input int      InpStartHour     = 8;           // Broker-server hour, NOT UTC
input int      InpEndHour       = 20;          // Broker-server hour, NOT UTC
input bool     InpUseATR        = true;
input int      InpATRPeriod     = 14;

input group "=== PROFIT LADDER (SL/TP management) ==="
input bool     InpUseProfitLadder = true;      // Ladder owns SL when ON (disables legacy BE/trail)
// 0 = OFF. Measured 2026-08-09 over 2022-2026: a BE stop at 25% of target cost
// 34pp with fixed 1:2 and 24pp with the regime RR table, by scratching the
// trail-stage runners that carry all of this system's profit. Matches the repo's
// earlier kalman exit-tuning result (tightening breakeven is harmful on gold).
input double   InpBETriggerPct    = 0.0;       // At this % of target -> SL to breakeven (0 = off)
input double   InpLockTriggerPct  = 50.0;      // At this % of target ...
// Swept 0/5/10/15/20/25/30/35/40/45 over 2022-2026 on 2026-08-09.
//   full EA net:  0 -5.03% | 5 -4.17% | 10 -2.30% | 25 -1.35% | 35 -14.7% | 40 -17.3%
//   DFVG-only PF: 0  1.30  | 5  1.31  | 10  1.28  | 25  1.21  | 40  1.18
// Above 25 is monotonically harmful on both paths. Below 25 the DFVG path improves
// slightly but 0/5/10 are within noise of each other (118 trades), and 0 is the WORST
// full-EA value (DD -27.7%) plus it parks the stop exactly where the spread oscillates.
// Set to 10 by user decision -- DFVG-optimum end of the range, clear of the spread.
// This parameter does not decide the outcome; the entry still fails its random control.
input double   InpLockPct         = 10.0;      // ... park SL at this % of target
input double   InpTrailStartPct   = 80.0;      // At this % of target: remove TP, start ratchet
input double   InpTrailGapPct     = 20.0;      // SL stays this many % behind the peak
input double   InpTrailStepPct    = 5.0;       // Min SL improvement (%) before sending a modify
input bool     InpRemoveTPOnTrail = true;      // Delete TP when the ratchet starts

input group "=== LEGACY TRADE MANAGEMENT (only if ladder OFF) ==="
input bool     InpUseBreakeven  = false;
input double   InpBETrigger     = 1.0;
input bool     InpUseTrailing   = false;
input double   InpTrailDist     = 2.0;

input group "=== AUTO-OPTIMIZER ==="
input bool     InpUseAutoOpt    = true;        // Enable Auto-Optimization
// OFF by user decision 2026-08-09: restores the v2 regime R:R table below
// (2.5 strong trend / 1.5 weak / 1.0 ranging / 0.8 dry).
input bool     InpUseFixedRR    = false;       // Force RR to InpDefaultRR (ignore regime RR)
input double   InpDefaultRR     = 2.0;         // Fallback RR when AutoOpt is OFF
input double   InpMaxRR         = 2.5;         // Max R:R (Strong Trend)
input double   InpMinRR         = 0.8;         // Min R:R (Dry Market)
input double   InpMaxATRMult    = 3.0;         // Max ATR Multiplier
input double   InpMinATRMult    = 1.0;         // Min ATR Multiplier
input double   InpDryLotFactor  = 0.5;         // Lot reduction in dry markets

//--- Indicator Handles
int handle_MA_Fast, handle_MA_Slow, handle_RSI, handle_ATR, handle_ADX, handle_BB;
int handle_ATR_DFVG;

//--- Dynamic Global Variables (Auto-Optimizer)
enum ENUM_MARKET_REGIME
{
   REGIME_TRENDING_STRONG,
   REGIME_TRENDING_WEAK,
   REGIME_RANGING,
   REGIME_DRY
};

ENUM_MARKET_REGIME currentRegime = REGIME_RANGING;
double dynFVGMinPips    = 5;
double dynATRMultiplier = 1.5;
double dynRR            = 2.0;
double dynLotMultiplier = 1.0;
string regimeText       = "Ranging";

//--- Zone Tracking (shared by every entry path; feeds CalculateSLTP)
double zoneHigh = 0, zoneLow = 0;
bool   zoneActive = false;
int    zoneType = 0; // 1=Bullish, -1=Bearish

//--- Trade Control
int    magicNumber = 123456;
datetime lastTradeTime = 0;
datetime lastRegimeCheck = 0;

//--- Double-FVG state (recomputed from scratch on every new FVG-TF bar)
struct FVGRec
{
   datetime time;     // open time of the newest candle of the 3-bar pattern
   int      shift;    // bar index at scan time
   double   top;      // upper edge of the gap
   double   bottom;   // lower edge of the gap
   int      dir;      // +1 bullish, -1 bearish
};

bool     dfvgArmed        = false;
int      dfvgDir          = 0;
double   dfvgTop          = 0;
double   dfvgBottom       = 0;
datetime dfvgFormTime     = 0;
datetime dfvgLastScan     = 0;   // FVG-TF bar time of the last scan
datetime dfvgLastEntryBar = 0;   // entry-TF bar already evaluated for a tap
datetime dfvgConsumedTime = 0;   // zone we already traded — never re-arm it
string   dfvgStatus       = "no setup";

//+------------------------------------------------------------------+
int OnInit()
{
   handle_MA_Fast = iMA(_Symbol, InpTF_HTF1, InpMAFast, 0, MODE_EMA, PRICE_CLOSE);
   handle_MA_Slow = iMA(_Symbol, InpTF_HTF1, InpMASlow, 0, MODE_EMA, PRICE_CLOSE);
   handle_RSI     = iRSI(_Symbol, InpTF_HTF1, InpRSIPeriod, PRICE_CLOSE);
   handle_ATR     = iATR(_Symbol, InpTF_ENTRY, InpATRPeriod);
   handle_ADX     = iADX(_Symbol, InpTF_HTF1, 14);
   handle_BB      = iBands(_Symbol, InpTF_HTF1, 20, 0, 2.0, PRICE_CLOSE);
   handle_ATR_DFVG= iATR(_Symbol, InpDFVG_TF, InpATRPeriod);

   if(handle_MA_Fast==INVALID_HANDLE || handle_MA_Slow==INVALID_HANDLE ||
      handle_RSI==INVALID_HANDLE || handle_ATR==INVALID_HANDLE ||
      handle_ADX==INVALID_HANDLE || handle_BB==INVALID_HANDLE ||
      handle_ATR_DFVG==INVALID_HANDLE)
   {
      Print("Indicator initialization failed");
      return(INIT_FAILED);
   }

   dynRR = InpDefaultRR;

   SeedTargetsForOpenPositions();
   PurgeStaleTargets();

   Print("EA Initialized. AutoOpt:", InpUseAutoOpt?"ON":"OFF",
         " | FixedRR:", InpUseFixedRR?DoubleToString(InpDefaultRR,2):"regime",
         " | DoubleFVG:", InpUseDoubleFVG?"ON":"OFF",
         " | Ladder:", InpUseProfitLadder?"ON":"OFF");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(handle_MA_Fast);
   IndicatorRelease(handle_MA_Slow);
   IndicatorRelease(handle_RSI);
   IndicatorRelease(handle_ATR);
   IndicatorRelease(handle_ADX);
   IndicatorRelease(handle_BB);
   IndicatorRelease(handle_ATR_DFVG);
   Comment("");
}

//+------------------------------------------------------------------+
void OnTick()
{
   //--- Update Auto-Optimizer (every 30 seconds to save CPU)
   if(InpUseAutoOpt && TimeCurrent()-lastRegimeCheck > 30)
   {
      UpdateMarketRegime();
      lastRegimeCheck = TimeCurrent();
   }
   else if(!InpUseAutoOpt)
   {
      dynRR = InpDefaultRR;
      dynATRMultiplier = 1.5;
      dynFVGMinPips = InpFVGMinPips;
      dynLotMultiplier = 1.0;
      currentRegime = REGIME_TRENDING_WEAK;
   }

   //--- RR override: "1:2 by default" regardless of regime
   if(InpUseFixedRR) dynRR = InpDefaultRR;

   //--- Basic Filters
   if(!IsTradeAllowed()) return;

   //--- One trade at a time: manage what is open, never stack
   if(CountOwnPositions() >= InpMaxOpenTrades)
   {
      ManageOpenPositions();
      UpdateDashboard();
      return;
   }

   //--- Keep the armed H4 zone fresh even while filters block entry
   if(InpUseDoubleFVG) UpdateDoubleFVG();
   UpdateDashboard();

   if(InpUseSession && !IsTradingSession()) return;

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > InpMaxSpread) return;

   //=== PATH A: H4 double-FVG -> 50-60% tap of the FIRST gap ========
   if(InpUseDoubleFVG)
   {
      int dfvgSignal = CheckDFVGEntry();
      if(dfvgSignal != 0)
      {
         ExecuteDoubleFVG(dfvgSignal);
         return;
      }
   }
   if(InpDFVG_Only) return;

   //=== PATH B: legacy multi-TF confluence chain ====================
   //--- Step 1: HTF Trend (4H)
   int trend = GetHTFTrend();
   if(trend == 0) return;

   //--- Step 2: HTF2 (1H) FVG or OB Mitigation
   bool htfZoneValid = false;
   int htfSignal = 0;

   if(InpUseFVG)
   {
      htfSignal = DetectFVG(InpTF_HTF2);
      if(htfSignal != 0 && IsFVG_Mitigated(InpTF_HTF2, htfSignal))
         htfZoneValid = true;
   }

   if(!htfZoneValid && InpUseOB)
   {
      htfSignal = DetectOrderBlock(InpTF_HTF2);
      if(htfSignal != 0 && IsOB_Mitigated(InpTF_HTF2, htfSignal))
         htfZoneValid = true;
   }

   if(!htfZoneValid) return;

   //--- Step 3: MTF Structure (15min)
   if(!CheckMTFStructure(trend)) return;

   //--- Step 4: Entry TF Confirmation (5min/1min)
   int entrySignal = CheckEntryConfirmation(trend, htfSignal);
   if(entrySignal == 0) return;

   //--- Step 5: Dynamic SL/TP
   double sl, tp;
   if(!CalculateSLTP(entrySignal, sl, tp)) return;

   //--- Step 6: Lot Size & Execute
   double lots = CalculateLotSize(sl);
   if(lots <= 0) return;

   OpenTrade(entrySignal, lots, sl, tp, "HTF");
}

//+------------------------------------------------------------------+
//| AUTO-OPTIMIZER CORE                                              |
//+------------------------------------------------------------------+
void UpdateMarketRegime()
{
   double adx[], atrNow[], atrHist[], bbUpper[], bbLower[], bbMid[];
   ArraySetAsSeries(adx, true);
   ArraySetAsSeries(atrNow, true);
   ArraySetAsSeries(atrHist, true);
   ArraySetAsSeries(bbUpper, true);
   ArraySetAsSeries(bbLower, true);
   ArraySetAsSeries(bbMid, true);

   if(CopyBuffer(handle_ADX, 0, 0, 1, adx) < 1) return;
   if(CopyBuffer(handle_ATR, 0, 0, 1, atrNow) < 1) return;
   if(CopyBuffer(handle_ATR, 0, 1, 50, atrHist) < 50) return;
   if(CopyBuffer(handle_BB, 1, 0, 1, bbUpper) < 1) return;
   if(CopyBuffer(handle_BB, 2, 0, 1, bbLower) < 1) return;
   if(CopyBuffer(handle_BB, 0, 0, 1, bbMid) < 1) return;

   //--- ATR Percentile (current vs 50-period mean)
   double atrMean = 0;
   for(int i=0; i<50; i++) atrMean += atrHist[i];
   atrMean /= 50.0;
   double atrRatio = (atrMean > 0) ? atrNow[0]/atrMean : 1.0;

   //--- Bollinger Band Width %
   double bbWidthPct = (bbMid[0] > 0) ? (bbUpper[0]-bbLower[0])/bbMid[0] : 0;

   //--- Regime Classification
   if(adx[0] > 30 && atrRatio >= 1.0)
   {
      currentRegime = REGIME_TRENDING_STRONG;
      regimeText = "Strong Trend";
   }
   else if(adx[0] > 20)
   {
      currentRegime = REGIME_TRENDING_WEAK;
      regimeText = "Weak Trend";
   }
   else if(atrRatio < 0.6 || bbWidthPct < 0.005) // Very tight, dead market
   {
      currentRegime = REGIME_DRY;
      regimeText = "Dry Market";
   }
   else
   {
      currentRegime = REGIME_RANGING;
      regimeText = "Ranging";
   }

   //--- Dynamic Parameter Mapping
   switch(currentRegime)
   {
      case REGIME_TRENDING_STRONG:
         dynRR = InpMaxRR;                          // e.g. 2.5
         dynATRMultiplier = InpMinATRMult;            // 1.0 (tight, trust trend)
         dynLotMultiplier = 1.0;
         break;

      case REGIME_TRENDING_WEAK:
         dynRR = 1.5;                               // Balanced
         dynATRMultiplier = 1.3;
         dynLotMultiplier = 1.0;
         break;

      case REGIME_RANGING:
         dynRR = 1.0;                               // Quick profits
         dynATRMultiplier = 2.0;                    // Wider SL for chop
         dynLotMultiplier = 0.8;                    // Slightly reduce size
         break;

      case REGIME_DRY:
         dynRR = InpMinRR;                          // e.g. 0.8 (scalp)
         dynATRMultiplier = InpMaxATRMult;            // 3.0 (avoid noise)
         dynLotMultiplier = InpDryLotFactor;        // 0.5 (half size)
         break;
   }

   //--- Dynamic FVG Size: Scale base pips by volatility
   // In high vol (trend), require larger FVGs. In range/dry, accept smaller.
   double volFactor = atrRatio;
   if(currentRegime == REGIME_RANGING) volFactor *= 0.85;
   if(currentRegime == REGIME_DRY) volFactor *= 0.7;
   if(currentRegime == REGIME_TRENDING_STRONG) volFactor *= 1.3;

   dynFVGMinPips = InpFVGMinPips * volFactor;
   if(dynFVGMinPips < 2) dynFVGMinPips = 2; // Hard floor
}

//+------------------------------------------------------------------+
void UpdateDashboard()
{
   Comment("Regime: ", regimeText,
           " | RR: ", DoubleToString(InpUseFixedRR?InpDefaultRR:dynRR,1),
           " | ATRx: ", DoubleToString(dynATRMultiplier,1),
           " | FVG: ", DoubleToString(dynFVGMinPips,1),
           " | Lot%: ", DoubleToString(dynLotMultiplier*100,0),
           "\nDoubleFVG: ", dfvgStatus,
           "\nOpen: ", IntegerToString(CountOwnPositions()), "/", IntegerToString(InpMaxOpenTrades));
}

//+------------------------------------------------------------------+
//| Helper Functions                                                 |
//+------------------------------------------------------------------+
bool IsTradeAllowed()
{
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED)) return false;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return false;
   return true;
}

int CountOwnPositions()
{
   int n = 0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket<=0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol &&
         PositionGetInteger(POSITION_MAGIC)==magicNumber)
         n++;
   }
   return n;
}

bool IsTradingSession()
{
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   return (dt.hour >= InpStartHour && dt.hour < InpEndHour);
}

//+------------------------------------------------------------------+
int GetHTFTrend()
{
   double maFast[], maSlow[], rsi[];
   ArraySetAsSeries(maFast, true); ArraySetAsSeries(maSlow, true); ArraySetAsSeries(rsi, true);
   if(CopyBuffer(handle_MA_Fast, 0, 0, 3, maFast)<3) return 0;
   if(CopyBuffer(handle_MA_Slow, 0, 0, 3, maSlow)<3) return 0;
   if(CopyBuffer(handle_RSI, 0, 0, 3, rsi)<3) return 0;

   double h[], l[], c[];
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true); ArraySetAsSeries(c, true);
   if(CopyHigh(_Symbol, InpTF_HTF1, 0, 5, h)<5) return 0;
   if(CopyLow(_Symbol, InpTF_HTF1, 0, 5, l)<5) return 0;
   if(CopyClose(_Symbol, InpTF_HTF1, 0, 5, c)<5) return 0;

   bool hh = (h[1]>h[2] && h[2]>h[3]);
   bool hl = (l[1]>l[2] && l[2]>l[3]);
   bool lh = (h[1]<h[2] && h[2]<h[3]);
   bool ll = (l[1]<l[2] && l[2]<l[3]);

   // A disabled filter stops constraining its leg. If BOTH are off the momentum
   // branch is skipped entirely and swing structure alone decides the trend —
   // otherwise "no filter" would degenerate into an always-bullish read.
   bool useMomentum = InpUseMAFilter || InpUseRSIFilter;

   bool maBull = !InpUseMAFilter  || (maFast[0] > maSlow[0]);
   bool maBear = !InpUseMAFilter  || (maFast[0] < maSlow[0]);

   bool rsiBull = !InpUseRSIFilter || (rsi[0]>40 && rsi[0]<80);
   bool rsiBear = !InpUseRSIFilter || (rsi[0]>20 && rsi[0]<60);

   if((hh && hl) || (useMomentum && maBull && rsiBull)) return 1;
   if((lh && ll) || (useMomentum && maBear && rsiBear)) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| H4 DOUBLE-FVG ENGINE                                             |
//+------------------------------------------------------------------+
//| A bullish FVG is a 3-bar gap where the NEWEST bar's low sits      |
//| above the OLDEST bar's high. Series indexing: index 0 = current,  |
//| so newest = i, oldest = i+2.                                     |
//+------------------------------------------------------------------+
int CollectFVGs(const MqlRates &r[], int copied, double minGap, FVGRec &out[])
{
   ArrayFree(out);
   int n = 0;
   // i = newest bar of the pattern; start at 1 so we only use CLOSED bars
   for(int i = 1; i <= copied-3; i++)
   {
      bool bull = (r[i].low  - r[i+2].high) > minGap;
      bool bear = (r[i+2].low - r[i].high)  > minGap;
      if(!bull && !bear) continue;

      ArrayResize(out, n+1);
      out[n].time  = r[i].time;
      out[n].shift = i;
      if(bull)
      {
         out[n].dir    = 1;
         out[n].top    = r[i].low;      // near edge — price returns to this first
         out[n].bottom = r[i+2].high;   // far edge
      }
      else
      {
         out[n].dir    = -1;
         out[n].top    = r[i+2].low;    // far edge
         out[n].bottom = r[i].high;     // near edge — price returns to this first
      }
      n++;
   }
   return n; // ordered newest -> oldest
}

//--- Gap fully traded through since it formed?
bool IsFVGFilled(const MqlRates &r[], const FVGRec &z)
{
   for(int i = z.shift-1; i >= 0; i--)
   {
      if(z.dir ==  1 && r[i].low  <= z.bottom) return true;
      if(z.dir == -1 && r[i].high >= z.top)    return true;
   }
   return false;
}

//--- Find the most recent same-direction PAIR and return the FIRST-formed gap
bool FindDoubleFVG(const MqlRates &r[], FVGRec &list[], int n, FVGRec &first)
{
   for(int b = 0; b < n; b++)            // candidate SECOND (newer) gap
   {
      for(int a = b+1; a < n; a++)       // candidate FIRST (older) gap
      {
         if(list[a].shift - list[b].shift > InpDFVG_PairWindow) break; // too far apart
         if(list[a].dir != list[b].dir) continue;
         if(list[a].time == dfvgConsumedTime) continue;                // already traded
         if(IsFVGFilled(r, list[a])) continue;                         // gap already closed
         first = list[a];
         return true;
      }
   }
   return false;
}

//--- Rebuild the armed zone once per new FVG-TF bar
void UpdateDoubleFVG()
{
   datetime scanBar = iTime(_Symbol, InpDFVG_TF, 0);
   if(scanBar == dfvgLastScan) return;
   dfvgLastScan = scanBar;

   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(handle_ATR_DFVG, 0, 0, 1, atr) < 1) return;
   double minGap = atr[0] * InpDFVG_MinATR;
   if(minGap <= 0) return;

   MqlRates r[];
   ArraySetAsSeries(r, true);
   int copied = CopyRates(_Symbol, InpDFVG_TF, 0, InpDFVG_Lookback+3, r);
   if(copied < 6) return;

   FVGRec list[];
   int n = CollectFVGs(r, copied, minGap, list);

   FVGRec first;
   ZeroMemory(first);
   dfvgArmed = false;

   if(FindDoubleFVG(r, list, n, first))
   {
      dfvgArmed    = true;
      dfvgDir      = first.dir;
      dfvgTop      = first.top;
      dfvgBottom   = first.bottom;
      dfvgFormTime = first.time;

      double d = dfvgTop - dfvgBottom;
      double lvMin = (dfvgDir==1) ? dfvgTop - d*InpDFVG_TapMinPct/100.0
                                  : dfvgBottom + d*InpDFVG_TapMinPct/100.0;
      dfvgStatus = StringFormat("%s zone %.2f-%.2f | tap @ %.2f | formed %s",
                                (dfvgDir==1?"BULL":"BEAR"), dfvgBottom, dfvgTop, lvMin,
                                TimeToString(dfvgFormTime, TIME_DATE|TIME_MINUTES));
      PrintFormat("[DFVG] armed %s", dfvgStatus);
   }
   else
   {
      dfvgStatus = "no setup";
   }
}

//--- Tap trigger, evaluated once per CLOSED entry-TF bar
int CheckDFVGEntry()
{
   if(!dfvgArmed) return 0;

   int barsSince = iBarShift(_Symbol, InpDFVG_TF, dfvgFormTime, false);
   if(barsSince < 0 || barsSince > InpDFVG_MaxAgeBars)
   {
      dfvgArmed = false;
      dfvgStatus = "expired";
      return 0;
   }

   datetime bt = iTime(_Symbol, InpTF_ENTRY, 1);   // last CLOSED entry bar
   if(bt == 0 || bt == dfvgLastEntryBar) return 0;
   dfvgLastEntryBar = bt;

   double o = iOpen (_Symbol, InpTF_ENTRY, 1);
   double h = iHigh (_Symbol, InpTF_ENTRY, 1);
   double l = iLow  (_Symbol, InpTF_ENTRY, 1);
   double c = iClose(_Symbol, InpTF_ENTRY, 1);

   double d = dfvgTop - dfvgBottom;
   if(d <= 0) return 0;

   if(InpDFVG_RequireTrend && GetHTFTrend() != dfvgDir) return 0;

   if(dfvgDir == 1)
   {
      double lvMin = dfvgTop - d*InpDFVG_TapMinPct/100.0;  // 50% depth
      double lvMax = dfvgTop - d*InpDFVG_TapMaxPct/100.0;  // 60% depth (deeper)
      bool tapped = (l <= lvMin);                          // reached the 50-60% zone
      bool held   = (c >= lvMax);                          // closed back above 60% depth
      bool colour = (!InpDFVG_RequireColor) || (c > o);    // bullish candle
      if(tapped && held && colour) return 1;
   }
   else
   {
      double lvMin = dfvgBottom + d*InpDFVG_TapMinPct/100.0;
      double lvMax = dfvgBottom + d*InpDFVG_TapMaxPct/100.0;
      bool tapped = (h >= lvMin);
      bool held   = (c <= lvMax);
      bool colour = (!InpDFVG_RequireColor) || (c < o);    // bearish candle
      if(tapped && held && colour) return -1;
   }
   return 0;
}

//--- Enter using the armed gap as the structural zone
void ExecuteDoubleFVG(int signal)
{
   zoneHigh   = dfvgTop;
   zoneLow    = dfvgBottom;
   zoneType   = signal;
   zoneActive = true;

   double sl, tp;
   if(!CalculateSLTP(signal, sl, tp)) return;

   double lots = CalculateLotSize(sl);
   if(lots <= 0) return;

   if(OpenTrade(signal, lots, sl, tp, "DFVG"))
   {
      dfvgConsumedTime = dfvgFormTime;   // one trade per gap
      dfvgArmed = false;
      dfvgStatus = "traded";
   }
}

//+------------------------------------------------------------------+
//| Legacy single-FVG detector (H1 zone leg)                         |
//+------------------------------------------------------------------+
int DetectFVG(ENUM_TIMEFRAMES tf)
{
   double h[], l[], o[], c[];
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true);
   ArraySetAsSeries(o, true); ArraySetAsSeries(c, true);

   if(CopyHigh(_Symbol, tf, 0, 5, h)<5) return 0;
   if(CopyLow(_Symbol, tf, 0, 5, l)<5) return 0;
   if(CopyOpen(_Symbol, tf, 0, 5, o)<5) return 0;
   if(CopyClose(_Symbol, tf, 0, 5, c)<5) return 0;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minGap = dynFVGMinPips * 10 * point; // Gold pip adjustment

   for(int i=1; i<3; i++)
   {
      // Bullish FVG: newest low above oldest high (zoneHigh > zoneLow)
      if(l[i] - h[i+2] > minGap)
      {
         zoneHigh = l[i]; zoneLow = h[i+2]; zoneType = 1;
         return 1;
      }
      // Bearish FVG: oldest low above newest high
      if(l[i+2] - h[i] > minGap)
      {
         zoneHigh = l[i+2]; zoneLow = h[i]; zoneType = -1;
         return -1;
      }
   }
   return 0;
}

bool IsFVG_Mitigated(ENUM_TIMEFRAMES tf, int type)
{
   double c[], l[], h[];
   ArraySetAsSeries(c, true); ArraySetAsSeries(l, true); ArraySetAsSeries(h, true);
   if(CopyClose(_Symbol, tf, 0, 3, c)<3) return false;
   if(CopyLow(_Symbol, tf, 0, 3, l)<3) return false;
   if(CopyHigh(_Symbol, tf, 0, 3, h)<3) return false;

   if(type==1) // Bullish
      return (l[0]<=zoneHigh && c[0]>zoneLow);
   else
      return (h[0]>=zoneLow && c[0]<zoneHigh);
}

//+------------------------------------------------------------------+
int DetectOrderBlock(ENUM_TIMEFRAMES tf)
{
   double o[], h[], l[], c[];
   ArraySetAsSeries(o, true); ArraySetAsSeries(h, true);
   ArraySetAsSeries(l, true); ArraySetAsSeries(c, true);

   if(CopyOpen(_Symbol, tf, 0, InpOBLookback+5, o)<InpOBLookback+5) return 0;
   if(CopyHigh(_Symbol, tf, 0, InpOBLookback+5, h)<InpOBLookback+5) return 0;
   if(CopyLow(_Symbol, tf, 0, InpOBLookback+5, l)<InpOBLookback+5) return 0;
   if(CopyClose(_Symbol, tf, 0, InpOBLookback+5, c)<InpOBLookback+5) return 0;

   for(int i=2; i<InpOBLookback; i++)
   {
      bool bear = c[i]<o[i];
      bool bullDisp = c[i-1]>o[i-1] && (c[i-1]-o[i-1])>(o[i]-c[i])*0.5;
      if(bear && bullDisp && c[i-1]>o[i])
      {
         zoneHigh=h[i]; zoneLow=l[i]; zoneType=1;
         return 1;
      }
      bool bull = c[i]>o[i];
      bool bearDisp = c[i-1]<o[i-1] && (o[i-1]-c[i-1])>(c[i]-o[i])*0.5;
      if(bull && bearDisp && c[i-1]<o[i])
      {
         zoneHigh=h[i]; zoneLow=l[i]; zoneType=-1;
         return -1;
      }
   }
   return 0;
}

bool IsOB_Mitigated(ENUM_TIMEFRAMES tf, int type)
{
   return IsFVG_Mitigated(tf, type);
}

//+------------------------------------------------------------------+
bool CheckMTFStructure(int trend)
{
   double h[], l[], c[];
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true); ArraySetAsSeries(c, true);
   if(CopyHigh(_Symbol, InpTF_MTF, 0, 5, h)<5) return false;
   if(CopyLow(_Symbol, InpTF_MTF, 0, 5, l)<5) return false;
   if(CopyClose(_Symbol, InpTF_MTF, 0, 5, c)<5) return false;

   if(trend==1)
   {
      bool bos = c[0]>h[2] && l[1]>l[2];
      bool hl = l[1]>l[3];
      return (bos || hl);
   }
   else
   {
      bool bos = c[0]<l[2] && h[1]<h[2];
      bool lh = h[1]<h[3];
      return (bos || lh);
   }
}

//+------------------------------------------------------------------+
int CheckEntryConfirmation(int trend, int htfSignal)
{
   if(trend!=htfSignal && htfSignal!=0) return 0;

   double o[], h[], l[], c[];
   ArraySetAsSeries(o, true); ArraySetAsSeries(h, true);
   ArraySetAsSeries(l, true); ArraySetAsSeries(c, true);

   if(CopyOpen(_Symbol, InpTF_ENTRY, 0, 5, o)<5) return 0;
   if(CopyHigh(_Symbol, InpTF_ENTRY, 0, 5, h)<5) return 0;
   if(CopyLow(_Symbol, InpTF_ENTRY, 0, 5, l)<5) return 0;
   if(CopyClose(_Symbol, InpTF_ENTRY, 0, 5, c)<5) return 0;

   bool pattern = false;

   if(trend==1)
   {
      if(InpUseHammer && IsHammer(o[1],h[1],l[1],c[1],true)) pattern=true;
      if(InpUseInvHammer && IsInvHammer(o[1],h[1],l[1],c[1],true)) pattern=true;
      if(InpUseEngulfing && IsBullEngulf(o,h,l,c,1)) pattern=true;
      if(InpUsePiercing && IsPiercing(o,h,l,c,1)) pattern=true;
      if(InpUse3Candle && IsMorningStar(o,h,l,c,1)) pattern=true;
      if(InpUseWM && IsW(h,l,c,1)) pattern=true;

      if(pattern && c[0]>o[0]) return 1;
   }
   else
   {
      if(InpUseHammer && IsHammer(o[1],h[1],l[1],c[1],false)) pattern=true;
      if(InpUseInvHammer && IsInvHammer(o[1],h[1],l[1],c[1],false)) pattern=true;
      if(InpUseEngulfing && IsBearEngulf(o,h,l,c,1)) pattern=true;
      if(InpUsePiercing && IsDarkCloud(o,h,l,c,1)) pattern=true;
      if(InpUse3Candle && IsEveningStar(o,h,l,c,1)) pattern=true;
      if(InpUseWM && IsM(h,l,c,1)) pattern=true;

      if(pattern && c[0]<o[0]) return -1;
   }
   return 0;
}

//+------------------------------------------------------------------+
//| Pattern Functions                                                |
//+------------------------------------------------------------------+
bool IsHammer(double o,double h,double l,double c,bool bull)
{
   double body=MathAbs(c-o), range=h-l;
   double lowShadow=(bull?o:c)-l;
   double upShadow=h-(bull?c:o);
   if(range==0) return false;
   return (body/range<0.3 && lowShadow/range>0.6 && upShadow/range<0.1);
}

bool IsInvHammer(double o,double h,double l,double c,bool bull)
{
   double body=MathAbs(c-o), range=h-l;
   double upShadow=h-(bull?c:o);
   double lowShadow=(bull?o:c)-l;
   if(range==0) return false;
   return (body/range<0.3 && upShadow/range>0.6 && lowShadow/range<0.1);
}

bool IsBullEngulf(double &o[],double &h[],double &l[],double &c[],int i)
{
   if(i+1>=ArraySize(o)) return false;
   return (c[i+1]<o[i+1] && c[i]>o[i] && o[i]<c[i+1] && c[i]>o[i+1]);
}

bool IsBearEngulf(double &o[],double &h[],double &l[],double &c[],int i)
{
   if(i+1>=ArraySize(o)) return false;
   return (c[i+1]>o[i+1] && c[i]<o[i] && o[i]>c[i+1] && c[i]<o[i+1]);
}

bool IsPiercing(double &o[],double &h[],double &l[],double &c[],int i)
{
   if(i+1>=ArraySize(o)) return false;
   double prevMid=(o[i+1]+c[i+1])/2;
   return (c[i+1]<o[i+1] && c[i]>o[i] && c[i]>prevMid && o[i]<c[i+1]);
}

bool IsDarkCloud(double &o[],double &h[],double &l[],double &c[],int i)
{
   if(i+1>=ArraySize(o)) return false;
   double prevMid=(o[i+1]+c[i+1])/2;
   return (c[i+1]>o[i+1] && c[i]<o[i] && c[i]<prevMid && o[i]>c[i+1]);
}

bool IsMorningStar(double &o[],double &h[],double &l[],double &c[],int i)
{
   if(i+2>=ArraySize(o)) return false;
   bool firstBear=c[i+2]<o[i+2];
   bool small=MathAbs(c[i+1]-o[i+1])<MathAbs(c[i+2]-o[i+2])*0.3;
   bool thirdBull=c[i]>o[i] && c[i]>(o[i+2]+c[i+2])/2;
   return firstBear && small && thirdBull;
}

bool IsEveningStar(double &o[],double &h[],double &l[],double &c[],int i)
{
   if(i+2>=ArraySize(o)) return false;
   bool firstBull=c[i+2]>o[i+2];
   bool small=MathAbs(c[i+1]-o[i+1])<MathAbs(c[i+2]-o[i+2])*0.3;
   bool thirdBear=c[i]<o[i] && c[i]<(o[i+2]+c[i+2])/2;
   return firstBull && small && thirdBear;
}

bool IsW(double &h[],double &l[],double &c[],int i)
{
   if(i+4>=ArraySize(h)) return false;
   bool low1=l[i+4]<l[i+3] && l[i+4]<l[i+2];
   bool low2=l[i]<l[i+1] && l[i]<l[i+2];
   bool mid=l[i+2]>l[i+4] && l[i+2]>l[i];
   bool neck=c[i]>h[i+2];
   return low1 && low2 && mid && neck;
}

bool IsM(double &h[],double &l[],double &c[],int i)
{
   if(i+4>=ArraySize(h)) return false;
   bool high1=h[i+4]>h[i+3] && h[i+4]>h[i+2];
   bool high2=h[i]>h[i+1] && h[i]>h[i+2];
   bool mid=h[i+2]<h[i+4] && h[i+2]<h[i];
   bool neck=c[i]<l[i+2];
   return high1 && high2 && mid && neck;
}

//+------------------------------------------------------------------+
bool CalculateSLTP(int signal, double &sl, double &tp)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(handle_ATR, 0, 0, 1, atr)<1) return false;

   double point=SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double price=(signal==1)?SymbolInfoDouble(_Symbol,SYMBOL_ASK):SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double buffer=InpUseATR ? atr[0]*dynATRMultiplier : 50*point;
   double rr = InpUseFixedRR ? InpDefaultRR : dynRR;

   if(signal==1)
   {
      sl=zoneLow-buffer;
      tp=price+(price-sl)*rr;
   }
   else
   {
      sl=zoneHigh+buffer;
      tp=price-(sl-price)*rr;
   }

   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   double minSL=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   if(MathAbs(price-sl)<=minSL) return false;
   if((signal==1 && sl>=price) || (signal==-1 && sl<=price)) return false;
   return true;
}

//+------------------------------------------------------------------+
double CalculateLotSize(double sl)
{
   double lotStep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double minLot =SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot =SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double lots;

   // Operator lot band, clamped inside what the broker actually permits.
   if(InpMinLot>0) minLot=MathMax(minLot,InpMinLot);
   if(InpMaxLot>0) maxLot=MathMin(maxLot,InpMaxLot);
   if(minLot>maxLot) minLot=maxLot;               // nonsense band -> ceiling wins

   if(InpRiskPercent<=0) lots=InpLotSize;
   else
   {
      double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
      double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);

      if(tickSize==0 || tickValue==0) return InpLotSize;

      double entry=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      double slDist=MathAbs(entry-sl);
      if(slDist<=0) return 0;
      double riskAmt=AccountInfoDouble(ACCOUNT_BALANCE)*InpRiskPercent/100.0;
      double ticksAtRisk=slDist/tickSize;
      lots=riskAmt/(ticksAtRisk*tickValue);
   }

   // Auto-Optimizer lot multiplier (dry market = smaller size)
   lots *= dynLotMultiplier;

   // Round to the broker's volume step AFTER every multiplier, then clamp
   if(lotStep>0) lots=MathFloor(lots/lotStep)*lotStep;
   lots=MathMax(minLot, MathMin(maxLot, lots));
   lots=NormalizeDouble(lots, 2);

   return lots;
}

//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode()
{
   long modes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
bool OpenTrade(int signal, double lots, double sl, double tp, string tag)
{
   MqlTradeRequest req={};
   MqlTradeResult res={};

   req.action=TRADE_ACTION_DEAL;
   req.symbol=_Symbol;
   req.volume=lots;
   req.deviation=InpSlippage;
   req.magic=magicNumber;
   req.sl=NormalizeDouble(sl,_Digits);
   req.tp=NormalizeDouble(tp,_Digits);
   req.comment=tag;
   req.type_filling=GetFillingMode();

   if(signal==1)
   {
      req.price=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      req.type=ORDER_TYPE_BUY;
   }
   else
   {
      req.price=SymbolInfoDouble(_Symbol,SYMBOL_BID);
      req.type=ORDER_TYPE_SELL;
   }

   if(!OrderSend(req,res) ||
      (res.retcode!=TRADE_RETCODE_DONE && res.retcode!=TRADE_RETCODE_PLACED))
   {
      PrintFormat("OrderSend failed [%s] ret=%d err=%d", tag, res.retcode, GetLastError());
      return false;
   }

   PrintFormat("Trade opened #%I64u [%s] | Regime:%s | Lots:%.2f | RR:%.2f | SL:%.2f TP:%.2f",
               res.order, tag, regimeText, lots,
               InpUseFixedRR?InpDefaultRR:dynRR, req.sl, req.tp);
   lastTradeTime=TimeCurrent();

   SeedTargetsForOpenPositions();
   return true;
}

//+------------------------------------------------------------------+
//| PER-TICKET TARGET DISTANCE                                       |
//| The ladder measures progress against the ORIGINAL TP distance.    |
//| Once the TP is deleted that reference is gone, so it is persisted |
//| in a terminal GlobalVariable keyed by ticket.                     |
//+------------------------------------------------------------------+
string TargetKey(ulong ticket)
{
   return "GHTF_"+IntegerToString(magicNumber)+"_TGT_"+IntegerToString((long)ticket);
}

void SeedTargetsForOpenPositions()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=magicNumber) continue;

      string key=TargetKey(ticket);
      if(GlobalVariableCheck(key)) continue;

      double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
      double tp=PositionGetDouble(POSITION_TP);
      if(tp>0) GlobalVariableSet(key, MathAbs(tp-openPrice));
   }
}

void PurgeStaleTargets()
{
   string prefix="GHTF_"+IntegerToString(magicNumber)+"_TGT_";
   for(int i=GlobalVariablesTotal()-1; i>=0; i--)
   {
      string name=GlobalVariableName(i);
      if(StringFind(name, prefix)!=0) continue;
      string tail=StringSubstr(name, StringLen(prefix));
      ulong ticket=(ulong)StringToInteger(tail);
      if(!PositionSelectByTicket(ticket)) GlobalVariableDel(name);
   }
}

double GetTargetDist(ulong ticket, double openPrice, double tp)
{
   string key=TargetKey(ticket);
   if(GlobalVariableCheck(key))
   {
      double v=GlobalVariableGet(key);
      if(v>0) return v;
   }
   if(tp>0)
   {
      double d=MathAbs(tp-openPrice);
      if(d>0) { GlobalVariableSet(key,d); return d; }
   }
   return 0; // TP already removed and reference lost -> leave the position alone
}

//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket<=0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=magicNumber) continue;

      if(InpUseProfitLadder) { ApplyProfitLadder(ticket); continue; }

      //--- Legacy management (only when the ladder is OFF)
      double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
      double currSL=PositionGetDouble(POSITION_SL);
      double currTP=PositionGetDouble(POSITION_TP);
      double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
      double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      int type=(int)PositionGetInteger(POSITION_TYPE);

      if(InpUseBreakeven && currSL!=openPrice && currTP>0)
      {
         double rr=InpUseFixedRR?InpDefaultRR:dynRR;
         double triggerDist=MathAbs(currTP-openPrice)/(rr>0?rr:1.0)*InpBETrigger;
         if(type==POSITION_TYPE_BUY && bid>=openPrice+triggerDist)
            ModifyPosition(ticket,openPrice,currTP);
         else if(type==POSITION_TYPE_SELL && ask<=openPrice-triggerDist)
            ModifyPosition(ticket,openPrice,currTP);
      }

      if(InpUseTrailing)
      {
         double atr[];
         ArraySetAsSeries(atr,true);
         if(CopyBuffer(handle_ATR,0,0,1,atr)>0)
         {
            double trailDist=atr[0]*InpTrailDist;
            if(type==POSITION_TYPE_BUY && bid-currSL>trailDist*2)
            {
               double newSL=NormalizeDouble(bid-trailDist,_Digits);
               if(newSL>currSL) ModifyPosition(ticket,newSL,currTP);
            }
            else if(type==POSITION_TYPE_SELL && currSL-ask>trailDist*2)
            {
               double newSL=NormalizeDouble(ask+trailDist,_Digits);
               if(newSL<currSL) ModifyPosition(ticket,newSL,currTP);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| PROFIT LADDER                                                    |
//| All percentages are of the ORIGINAL take-profit distance.         |
//|   >= 25% of target  -> SL to BREAKEVEN (entry price)              |
//|   >= 50% of target  -> SL parked at 25% of target                 |
//|   >= 80% of target  -> TP deleted, SL ratchets 20% behind peak    |
//|                        (80->60, 100->80, 120->100, ...)           |
//| SL only ever moves in the profitable direction.                   |
//| NB: breakeven means the ENTRY PRICE, so a trade stopped there     |
//| still gives back the spread and commission.                       |
//+------------------------------------------------------------------+
void ApplyProfitLadder(ulong ticket)
{
   double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
   double sl       =PositionGetDouble(POSITION_SL);
   double tp       =PositionGetDouble(POSITION_TP);
   bool   isBuy    =(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY);

   double target=GetTargetDist(ticket, openPrice, tp);
   if(target<=0) return;

   double price=isBuy ? SymbolInfoDouble(_Symbol,SYMBOL_BID)
                      : SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   double moved   =isBuy ? (price-openPrice) : (openPrice-price);
   double progress=moved/target*100.0;
   if(progress<=0) return;

   // Where the current SL sits, expressed in % of target (negative = below entry)
   double curSLpct=-1e9;
   if(sl>0) curSLpct=(isBuy ? (sl-openPrice) : (openPrice-sl))/target*100.0;

   bool   trailMode=(progress>=InpTrailStartPct) || (tp==0.0 && InpRemoveTPOnTrail);
   double newSLpct =-1e9;
   double newTP    =tp;

   if(trailMode)
   {
      newSLpct=progress-InpTrailGapPct;
      if(InpRemoveTPOnTrail) newTP=0.0;
   }
   else if(progress>=InpLockTriggerPct)
   {
      newSLpct=InpLockPct;
   }
   else if(InpBETriggerPct>0.0 && progress>=InpBETriggerPct)
   {
      newSLpct=0.0;                          // breakeven = the entry price
   }

   bool tpChanged=(newTP!=tp);
   if(newSLpct<=-1e8)
   {
      if(tpChanged) ModifyPosition(ticket, sl, newTP);
      return;
   }

   // Ratchet: only send a modify when the SL genuinely improves
   if(!tpChanged && newSLpct <= curSLpct + InpTrailStepPct) return;
   if(newSLpct <= curSLpct) newSLpct = curSLpct;   // never walk the stop backwards
   if(newSLpct<=-1e8) return;

   double newSL=isBuy ? openPrice + target*newSLpct/100.0
                      : openPrice - target*newSLpct/100.0;

   // Respect the broker's minimum stop distance
   double minDist=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point;
   double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   if(isBuy  && newSL > bid-minDist) newSL = bid-minDist;
   if(!isBuy && newSL < ask+minDist) newSL = ask+minDist;

   newSL=NormalizeDouble(newSL,_Digits);
   if(sl>0)
   {
      if(isBuy  && newSL <= sl) { if(tpChanged) ModifyPosition(ticket, sl, newTP); return; }
      if(!isBuy && newSL >= sl) { if(tpChanged) ModifyPosition(ticket, sl, newTP); return; }
   }

   if(ModifyPosition(ticket,newSL,newTP))
      PrintFormat("[LADDER] #%I64u progress %.1f%% -> SL %.2f (%.1f%%)%s",
                  ticket, progress, newSL, newSLpct, tpChanged?" | TP removed":"");
}

//+------------------------------------------------------------------+
bool ModifyPosition(ulong ticket, double sl, double tp)
{
   MqlTradeRequest req={};
   MqlTradeResult res={};
   req.action  =TRADE_ACTION_SLTP;
   req.position=ticket;
   req.symbol  =_Symbol;
   req.sl      =NormalizeDouble(sl,_Digits);
   req.tp      =NormalizeDouble(tp,_Digits);

   if(!OrderSend(req,res) ||
      (res.retcode!=TRADE_RETCODE_DONE && res.retcode!=TRADE_RETCODE_PLACED))
   {
      PrintFormat("ModifyPosition #%I64u failed ret=%d err=%d sl=%.2f tp=%.2f",
                  ticket, res.retcode, GetLastError(), req.sl, req.tp);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   // Clean up the persisted target once a position is gone
   if(trans.type==TRADE_TRANSACTION_DEAL_ADD && trans.symbol==_Symbol)
   {
      if(trans.position>0 && !PositionSelectByTicket(trans.position))
      {
         string key=TargetKey(trans.position);
         if(GlobalVariableCheck(key)) GlobalVariableDel(key);
      }
   }
}
//+------------------------------------------------------------------+
