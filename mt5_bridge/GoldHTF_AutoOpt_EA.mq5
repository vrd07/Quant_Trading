//+------------------------------------------------------------------+
//|                                   GoldHTF_AutoOptimizer_EA.mq5   |
//|            Multi-TF ICT Strategy with Dynamic Regime Detection   |
//+------------------------------------------------------------------+
#property copyright "Auto-Optimizer Edition"
#property version   "3.0"

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
// H1 bars the legacy FVG scan covers. The old code was hard-wired to a 4-bar
// window. Swept 2022-2026 on the isolated legacy path: 20 -> PF 1.18, 60 -> PF 1.15
// with a worse year map, so a longer memory is not free -- older gaps are weaker.
input int      InpFVGLookback   = 20;          // Bars scanned for a legacy H1 FVG
// The zone a legacy stop sits behind is an H1 structure, so pad it with the H1 ATR
// rather than the 14-period M5 ATR (~70 minutes of entry-TF noise).
// Measured on the isolated legacy path, on TOP of the rewritten zone leg:
//   M5 ATR buffer   630 tr  PF 1.18  +274%  DD -40%
//   zone-TF buffer  475 tr  PF 1.28  +397%  DD -44%
// ⚠️ It is NOT additive on its own: applied to the OLD zone leg it gave PF 1.15 with
// three losing years and a -102% peak drawdown, and on the H4 double-FVG path it was
// flat-to-worse (PF 1.06 -> 1.04, DD -34% -> -89%), so it stays OFF for that path.
// A wider buffer only pays once the zone it pads is one price actually respected.
input bool     InpZoneATRBuffer = true;        // Pad legacy stops with the H1 ATR

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
// The original bands were bull 40-80 / bear 20-60, which OVERLAP on 40-60 -- both
// passed there, so the MA cross alone decided and GetHTFTrend() returned a direction
// on 2378 of 2378 bars measured. Non-overlapping bands give it a genuine "no trade".
input double   InpRSIBull       = 55.0;        // BUY needs RSI above this
input double   InpRSIBear       = 45.0;        // SELL needs RSI below this

input group "=== ENTRY QUALITY ==="
// Measured over 935 trades: the EA enters BUYS at the 81st percentile of the prior 2h
// range and SELLS at the 22nd -- it chases. This gate rejects entries that have already
// run more than N x M5-ATR from the zone their stop sits behind.
// ⚠️ OFF BY DEFAULT, and the earlier "tested and harmful" verdict was drawn at ONE
// loose setting. At 2.0 the gate barely binds and costs money (full span net
// +4.31% -> -0.10%, maxDD -25.26% -> -26.51%) because the chasing is not the losing
// part: bucketed by entry position the TOP-25% buys were the BEST bucket (PF 1.16 vs
// 0.87-1.02), and the bleed is the SELL side, where 334 of 356 sells sit in the
// bottom half of the range. At 0.5 it binds hard and the picture flips -- measured
// 2026-08-09 on the isolated legacy path with the rewritten zone leg:
//   off   630 tr  PF 1.18  +274%  DD -40%  1 losing year
//   0.5   150 tr  PF 1.24  +42%   DD -22%  2 losing years
// Better PF and half the drawdown, but it discards three quarters of the trades and
// most of the net, and the PF gain is inside the random-entry null either way.
// Left OFF; the zone-touch requirement in DetectFVG is now the entry anchor.
input double   InpMaxZoneDistATR = 0.0;       // Max entry distance from the zone edge
// Re-entering the same idea minutes after being stopped just pays the spread twice.
input int      InpCooldownMinutes = 30;       // Wait this long after a position closes

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
input double   InpWeakRR        = 1.5;         // R:R in a weak trend
input double   InpMaxATRMult    = 3.0;         // Max ATR Multiplier
input double   InpMinATRMult    = 1.0;         // Min ATR Multiplier
input double   InpDryLotFactor  = 0.5;         // Lot reduction in dry markets

//--- Indicator Handles
int handle_MA_Fast, handle_MA_Slow, handle_RSI, handle_ATR, handle_ADX, handle_BB;
int handle_ATR_DFVG, handle_ATR_REG, handle_ATR_ZONE;

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
int    zoneType = 0; // 1=Bullish, -1=Bearish

//--- Trade Control
int    magicNumber = 123456;
datetime lastTradeTime = 0;
datetime lastRegimeCheck = 0;
datetime lastCloseTime = 0;      // when our last position closed (cooldown anchor)
int      lastPosCount = 0;       // to detect a close between ticks

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
   handle_ATR_REG = iATR(_Symbol, InpTF_HTF1, InpATRPeriod);  // regime vol on H4
   handle_ATR_ZONE= iATR(_Symbol, InpTF_HTF2, InpATRPeriod);  // legacy zone vol on H1

   if(handle_MA_Fast==INVALID_HANDLE || handle_MA_Slow==INVALID_HANDLE ||
      handle_RSI==INVALID_HANDLE || handle_ATR==INVALID_HANDLE ||
      handle_ADX==INVALID_HANDLE || handle_BB==INVALID_HANDLE ||
      handle_ATR_DFVG==INVALID_HANDLE || handle_ATR_REG==INVALID_HANDLE ||
      handle_ATR_ZONE==INVALID_HANDLE)
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
   IndicatorRelease(handle_ATR_REG);
   IndicatorRelease(handle_ATR_ZONE);
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
   int openNow = CountOwnPositions();
   if(openNow < lastPosCount) lastCloseTime = TimeCurrent();   // a position just closed
   lastPosCount = openNow;

   if(openNow >= InpMaxOpenTrades)
   {
      ManageOpenPositions();
      UpdateDashboard();
      return;
   }

   //--- Cooldown: do not re-enter the same idea minutes after being stopped
   if(InpCooldownMinutes > 0 && lastCloseTime > 0 &&
      TimeCurrent() - lastCloseTime < InpCooldownMinutes*60)
      return;

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

   //--- Step 2: HTF2 (1H) zone. The detectors now return a zone ONLY when it is
   //--- unfilled and has already been retested, so there is no separate
   //--- mitigation gate to pass afterwards.
   bool htfZoneValid = false;
   int htfSignal = 0;

   if(InpUseFVG)
   {
      htfSignal = DetectFVG(InpTF_HTF2);
      if(htfSignal != 0) htfZoneValid = true;
   }

   if(!htfZoneValid && InpUseOB)
   {
      htfSignal = DetectOrderBlock(InpTF_HTF2);
      if(htfSignal != 0) htfZoneValid = true;
   }

   if(!htfZoneValid) return;

   //--- Step 3: MTF Structure (15min)
   if(!CheckMTFStructure(trend)) return;

   //--- Step 4: Entry TF Confirmation (5min/1min)
   int entrySignal = CheckEntryConfirmation(trend, htfSignal);
   if(entrySignal == 0) return;

   //--- Step 5: Dynamic SL/TP
   double sl, tp;
   if(!CalculateSLTP(entrySignal, sl, tp, true)) return;   // H1 zone

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
   // Regime is an H4 question; measuring it with a 14-period M5 ATR is ~70 minutes
   // of data and cannot distinguish a strong H4 trend from a dry one.
   if(CopyBuffer(handle_ATR_REG, 0, 1, 1, atrNow) < 1) return;
   if(CopyBuffer(handle_ATR_REG, 0, 2, 50, atrHist) < 50) return;
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
         dynRR = InpWeakRR;                         // Balanced
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

   bool rsiBull = !InpUseRSIFilter || (rsi[0] > InpRSIBull);
   bool rsiBear = !InpUseRSIFilter || (rsi[0] < InpRSIBear);

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

//+------------------------------------------------------------------+
//| ZONE LIFETIME TESTS (shared by the double-FVG and legacy paths)    |
//| A zone is a price band plus the bar it formed on. `high` is the    |
//| upper edge, `low` the lower edge. Price coming back to a BULLISH   |
//| zone meets `high` first and has filled it once it reaches `low`;   |
//| a BEARISH zone is the mirror.                                     |
//+------------------------------------------------------------------+
struct ZoneRec
{
   double high;
   double low;
   int    dir;     // +1 bullish, -1 bearish
   int    shift;   // bar index it formed on, at scan time
};

//--- Traded fully THROUGH the zone since it formed => invalidated
bool ZoneFilled(const MqlRates &r[], const ZoneRec &z)
{
   for(int i = z.shift-1; i >= 1; i--)     // bars after formation, closed only
   {
      if(z.dir ==  1 && r[i].low  <= z.low)  return true;
      if(z.dir == -1 && r[i].high >= z.high) return true;
   }
   return false;
}

//--- Entered the zone at ANY point since it formed => tested and held
bool ZoneTouched(const MqlRates &r[], const ZoneRec &z)
{
   for(int i = z.shift-1; i >= 1; i--)
   {
      if(z.dir ==  1 && r[i].low  <= z.high) return true;
      if(z.dir == -1 && r[i].high >= z.low)  return true;
   }
   return false;
}

//--- Gap fully traded through since it formed?
bool IsFVGFilled(const MqlRates &r[], const FVGRec &f)
{
   ZoneRec z;
   z.high = f.top; z.low = f.bottom; z.dir = f.dir; z.shift = f.shift;
   return ZoneFilled(r, z);
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
   if(CopyBuffer(handle_ATR_DFVG, 0, 0, 2, atr) < 2) return;
   double minGap = atr[1] * InpDFVG_MinATR;   // last CLOSED bar's ATR
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

   // The tap is a BAND, not a rejection test: wick at least TapMin% deep, close no
   // deeper than TapMax%. lvMax sitting below lvMin is therefore intended -- `held`
   // rejects candles that closed straight THROUGH the gap, and a close at 55% depth
   // is a pass by design. This matches the rule as specified in
   // scripts/research_double_fvg.py (which sweeps tap_max = tap_min + 10).
   // Tightening it to "close back above the TapMin line" (set InpDFVG_TapMaxPct =
   // InpDFVG_TapMinPct) was measured on the isolated DFVG path over 2022-2026 and is
   // WORSE: 119 tr PF 1.06 +8.2% -> 111 tr PF 1.04 +5.6%.
   if(dfvgDir == 1)
   {
      double lvMin = dfvgTop - d*InpDFVG_TapMinPct/100.0;  // 50% depth
      double lvMax = dfvgTop - d*InpDFVG_TapMaxPct/100.0;  // 60% depth (deeper)
      bool tapped = (l <= lvMin);                          // reached at least 50% deep
      bool held   = (c >= lvMax);                          // did NOT close past 60%
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

   double sl, tp;
   if(!CalculateSLTP(signal, sl, tp, false)) return;       // H4 double-FVG zone

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
//| LEGACY H1 ZONE LEG                                                |
//| Rewritten 2026-08-09. The previous version had three faults that   |
//| compounded into "enter anywhere, put the stop behind a level price |
//| never visited":                                                    |
//|   1. It scanned MQL indices 1..2 only -- a 4-bar window -- so it   |
//|      could only ever see a gap formed in the last few hours.       |
//|   2. It never checked whether price had already traded THROUGH the |
//|      gap, so a broken level could still be armed and used as SL.   |
//|   3. Its companion mitigation test was VACUOUS for the freshest    |
//|      gap: with zoneHigh set to l[1], `l[1] <= zoneHigh` is true by |
//|      construction and `c[1] > zoneLow` follows from the gap        |
//|      definition, so a gap that formed on the last closed bar       |
//|      passed the gate having never been retested at all.            |
//| Now: scan InpFVGLookback bars, discard gaps price has filled, and  |
//| require the zone to have been TOUCHED since it formed. The touch   |
//| test is what anchors the entry to the zone -- a zone that formed   |
//| on the last closed bar has by definition not been retested yet, so |
//| it is skipped instead of traded at whatever price is now.          |
//|                                                                    |
//| Measured (scripts/simulate_goldhtf_ea.py --no-dfvg, XAUUSD         |
//| 2022-03..2026-07, $1k, strict 0.20/side, legacy path ISOLATED so   |
//| the two paths do not trade each other's position slot):            |
//|   old zone leg        852 tr  PF 1.13  +171%  DD -82%  +0.01R      |
//|   this version        630 tr  PF 1.18  +274%  DD -40%  +0.06R      |
//|   + zone-TF ATR below 475 tr  PF 1.28  +397%  DD -44%  +0.10R      |
//| (DD is peak-relative. Lookback 60 was WORSE than 20: PF 1.15.)     |
//+------------------------------------------------------------------+
int DetectFVG(ENUM_TIMEFRAMES tf)
{
   MqlRates r[];
   ArraySetAsSeries(r, true);
   int copied = CopyRates(_Symbol, tf, 0, InpFVGLookback+3, r);
   if(copied < 6) return 0;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   // NB for 2-digit gold this is dynFVGMinPips * 0.1 USD, i.e. a $0.2-$0.6 gap.
   // That is a LOOSE floor on H1, not a tight one -- the legacy FVG leg fired on
   // ~16% of evaluated bars over the full span, so it is not "dead" and removing
   // the x10 would only make it looser still. Left at the measured value.
   double minGap = dynFVGMinPips * 10 * point;

   FVGRec list[];
   int n = CollectFVGs(r, copied, minGap, list);   // newest -> oldest

   for(int i = 0; i < n; i++)
   {
      ZoneRec z;
      z.high = list[i].top; z.low = list[i].bottom;
      z.dir  = list[i].dir; z.shift = list[i].shift;
      if(ZoneFilled(r, z))   continue;             // already broken
      if(!ZoneTouched(r, z)) continue;             // never retested
      zoneHigh = z.high; zoneLow = z.low; zoneType = z.dir;
      return z.dir;
   }
   return 0;
}

//+------------------------------------------------------------------+
//| Same order-block pattern as before; the one-bar mitigation test is |
//| replaced by the shared unfilled + touched lifetime tests.          |
//+------------------------------------------------------------------+
int DetectOrderBlock(ENUM_TIMEFRAMES tf)
{
   MqlRates r[];
   ArraySetAsSeries(r, true);
   int copied = CopyRates(_Symbol, tf, 0, InpOBLookback+5, r);
   if(copied < InpOBLookback+5) return 0;

   for(int i=2; i<InpOBLookback; i++)
   {
      int dir = 0;
      bool bear = r[i].close < r[i].open;
      bool bullDisp = r[i-1].close > r[i-1].open &&
                      (r[i-1].close-r[i-1].open) > (r[i].open-r[i].close)*0.5;
      bool bull = r[i].close > r[i].open;
      bool bearDisp = r[i-1].close < r[i-1].open &&
                      (r[i-1].open-r[i-1].close) > (r[i].close-r[i].open)*0.5;

      if(bear && bullDisp && r[i-1].close > r[i].open)      dir =  1;
      else if(bull && bearDisp && r[i-1].close < r[i].open) dir = -1;
      else continue;

      ZoneRec z;
      z.high = r[i].high; z.low = r[i].low; z.dir = dir; z.shift = i;
      if(ZoneFilled(r, z))   continue;
      if(!ZoneTouched(r, z)) continue;
      zoneHigh = z.high; zoneLow = z.low; zoneType = dir;
      return dir;
   }
   return 0;
}

//+------------------------------------------------------------------+
bool CheckMTFStructure(int trend)
{
   double h[], l[], c[];
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true); ArraySetAsSeries(c, true);
   if(CopyHigh(_Symbol, InpTF_MTF, 0, 8, h)<8) return false;
   if(CopyLow(_Symbol, InpTF_MTF, 0, 8, l)<8) return false;
   if(CopyClose(_Symbol, InpTF_MTF, 0, 8, c)<8) return false;

   // Everything shifted one bar back: index 1 is the last CLOSED M15 bar, so a BOS is
   // confirmed by a bar that has actually finished rather than by a forming close.
   if(trend==1)
   {
      bool bos = c[1]>h[3] && l[2]>l[3];
      bool hl = l[2]>l[4];
      return (bos || hl);
   }
   else
   {
      bool bos = c[1]<l[3] && h[2]<h[3];
      bool lh = h[2]<h[4];
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

   if(CopyOpen(_Symbol, InpTF_ENTRY, 0, 9, o)<9) return 0;
   if(CopyHigh(_Symbol, InpTF_ENTRY, 0, 9, h)<9) return 0;
   if(CopyLow(_Symbol, InpTF_ENTRY, 0, 9, l)<9) return 0;
   if(CopyClose(_Symbol, InpTF_ENTRY, 0, 9, c)<9) return 0;

   // Shifted one bar back: the pattern sits on bar 2 and the directional confirmation
   // on bar 1, both CLOSED. The original confirmed on the forming bar (index 0), so a
   // signal appeared and vanished as that candle flipped between green and red.
   bool pattern = false;

   if(trend==1)
   {
      if(InpUseHammer && IsHammer(o[2],h[2],l[2],c[2],true)) pattern=true;
      if(InpUseInvHammer && IsInvHammer(o[2],h[2],l[2],c[2],true)) pattern=true;
      if(InpUseEngulfing && IsBullEngulf(o,h,l,c,2)) pattern=true;
      if(InpUsePiercing && IsPiercing(o,h,l,c,2)) pattern=true;
      if(InpUse3Candle && IsMorningStar(o,h,l,c,2)) pattern=true;
      if(InpUseWM && IsW(h,l,c,2)) pattern=true;

      if(pattern && c[1]>o[1]) return 1;
   }
   else
   {
      if(InpUseHammer && IsHammer(o[2],h[2],l[2],c[2],false)) pattern=true;
      if(InpUseInvHammer && IsInvHammer(o[2],h[2],l[2],c[2],false)) pattern=true;
      if(InpUseEngulfing && IsBearEngulf(o,h,l,c,2)) pattern=true;
      if(InpUsePiercing && IsDarkCloud(o,h,l,c,2)) pattern=true;
      if(InpUse3Candle && IsEveningStar(o,h,l,c,2)) pattern=true;
      if(InpUseWM && IsM(h,l,c,2)) pattern=true;

      if(pattern && c[1]<o[1]) return -1;
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
//| `legacyZone` selects the volatility reference for the stop buffer: the H1 ATR
//| for a legacy H1 zone, the M5 ATR otherwise. The DFVG path keeps the M5 ATR --
//| the H4 reference was measured worse there (PF 1.06 -> 1.04, DD -34% -> -89%).
bool CalculateSLTP(int signal, double &sl, double &tp, bool legacyZone)
{
   double atr[];
   ArraySetAsSeries(atr, true);
   // Index 1 = ATR of the last CLOSED bar. On index 0 the ATR expands and contracts as
   // the bar develops, so the same signal produced a different stop on every tick.
   if(CopyBuffer(handle_ATR, 0, 0, 2, atr)<2) return false;

   double point=SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double price=(signal==1)?SymbolInfoDouble(_Symbol,SYMBOL_ASK):SymbolInfoDouble(_Symbol,SYMBOL_BID);

   //--- Entry-quality gate: has price already run away from the zone?
   // Distance is measured from the edge price meets FIRST coming back into the zone,
   // in M5 ATRs -- this gate is about the ENTRY, not about the zone's structure.
   if(InpMaxZoneDistATR > 0 && atr[1] > 0)
   {
      double extension = (signal==1) ? (price - zoneHigh) : (zoneLow - price);
      if(extension > atr[1]*InpMaxZoneDistATR) return false;
   }

   //--- Stop buffer, padded with the ATR of the timeframe the zone lives on
   double bufATR = atr[1];
   if(legacyZone && InpZoneATRBuffer)
   {
      double zatr[];
      ArraySetAsSeries(zatr, true);
      if(CopyBuffer(handle_ATR_ZONE, 0, 0, 2, zatr)<2) return false;
      if(zatr[1] <= 0) return false;
      bufATR = zatr[1];
   }
   double buffer=InpUseATR ? bufATR*dynATRMultiplier : 50*point;
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
//+------------------------------------------------------------------+
//| Lot decimal places, derived from the broker's volume step.        |
//| MQL5 has no SYMBOL_VOLUME_DIGITS -- step 0.01 -> 2, 0.001 -> 3.   |
//+------------------------------------------------------------------+
int VolumeDigits(double step)
{
   if(step<=0.0) return 2;
   int d=0;
   while(d<8 && step<1.0-1e-9) { step*=10.0; d++; }
   return d;
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
   lots=NormalizeDouble(lots, VolumeDigits(lotStep));

   return lots;
}

//+------------------------------------------------------------------+
//| Returns false when the symbol supports neither FOK nor IOC. ORDER_FILLING_RETURN
//| is for PENDING orders and brokers reject it on market execution, so refusing to
//| trade is safer than sending something that cannot fill.
bool GetFillingMode(ENUM_ORDER_TYPE_FILLING &mode)
{
   long modes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) { mode = ORDER_FILLING_FOK; return true; }
   if((modes & SYMBOL_FILLING_IOC) != 0) { mode = ORDER_FILLING_IOC; return true; }
   return false;
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
   ENUM_ORDER_TYPE_FILLING fill;
   if(!GetFillingMode(fill))
   {
      Print("Symbol supports neither FOK nor IOC filling - refusing market order");
      return false;
   }
   req.type_filling=fill;

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
         if(CopyBuffer(handle_ATR,0,0,2,atr)>1)
         {
            double trailDist=atr[1]*InpTrailDist;
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

   // Trail mode is entered on progress. A position can also ARRIVE here already in
   // trail mode (EA restart after the TP was deleted) -- but "no TP" alone is not
   // proof of that: a manually cleared TP would otherwise be read as trail mode and
   // yank the stop from its full risk distance to just under entry, stopping the
   // trade out almost immediately. Require the stop to already be locked in profit,
   // which only the ladder itself can have done.
   bool   trailMode=(progress>=InpTrailStartPct)
                    || (tp==0.0 && InpRemoveTPOnTrail && curSLpct>=InpLockPct);
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
