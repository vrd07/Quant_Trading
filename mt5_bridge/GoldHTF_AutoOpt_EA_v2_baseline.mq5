//+------------------------------------------------------------------+
//|                                   GoldHTF_AutoOpt_EA_v2_baseline.mq5   |
//|            Multi-TF ICT Strategy with Dynamic Regime Detection   |
//+------------------------------------------------------------------+
#property copyright "Auto-Optimizer Edition"
#property version   "2.00"   // FROZEN v2 baseline for A/B testing — do not edit

//--- Standard Input Groups
input group "=== RISK MANAGEMENT ==="
input double   InpLotSize       = 0.01;        // Fixed Lot Size (0 = use Risk%)
input double   InpRiskPercent    = 1.0;        // Risk % per trade
input int      InpMaxSpread     = 50;          // Max Spread (points)
input int      InpSlippage      = 30;          // Max Slippage

input group "=== TIMEFRAME SETTINGS ==="
input ENUM_TIMEFRAMES InpTF_HTF1  = PERIOD_H4;  // HTF1 - Direction (4H)
input ENUM_TIMEFRAMES InpTF_HTF2  = PERIOD_H1;  // HTF2 - FVG/OB Zone (1H)
input ENUM_TIMEFRAMES InpTF_MTF   = PERIOD_M15; // MTF - Structure (15min)
input ENUM_TIMEFRAMES InpTF_ENTRY = PERIOD_M5;  // Entry TF (5min)

input group "=== FVG & ORDER BLOCK ==="
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
input int      InpStartHour     = 8;
input int      InpEndHour       = 20;
input bool     InpUseATR        = true;
input int      InpATRPeriod     = 14;

input group "=== TRADE MANAGEMENT ==="
input bool     InpUseBreakeven  = true;
input double   InpBETrigger     = 1.0;
input bool     InpUseTrailing   = true;
input double   InpTrailDist     = 2.0;

input group "=== AUTO-OPTIMIZER ==="
input bool     InpUseAutoOpt    = true;        // Enable Auto-Optimization
input double   InpMaxRR         = 2.5;         // Max R:R (Strong Trend)
input double   InpMinRR         = 0.8;         // Min R:R (Dry Market)
input double   InpMaxATRMult    = 3.0;         // Max ATR Multiplier
input double   InpMinATRMult    = 1.0;         // Min ATR Multiplier
input double   InpDryLotFactor  = 0.5;         // Lot reduction in dry markets

//--- Indicator Handles
int handle_MA_Fast, handle_MA_Slow, handle_RSI, handle_ATR, handle_ADX, handle_BB;

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
double dynRR            = 1.5;
double dynLotMultiplier = 1.0;
string regimeText       = "Ranging";

//--- Zone Tracking
double zoneHigh = 0, zoneLow = 0;
bool   zoneActive = false;
int    zoneType = 0; // 1=Bullish, -1=Bearish

//--- Trade Control
int    magicNumber = 123457;   // distinct from v3 so the two never manage each other
datetime lastTradeTime = 0;
datetime lastRegimeCheck = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   handle_MA_Fast = iMA(_Symbol, InpTF_HTF1, InpMAFast, 0, MODE_EMA, PRICE_CLOSE);
   handle_MA_Slow = iMA(_Symbol, InpTF_HTF1, InpMASlow, 0, MODE_EMA, PRICE_CLOSE);
   handle_RSI     = iRSI(_Symbol, InpTF_HTF1, InpRSIPeriod, PRICE_CLOSE);
   handle_ATR     = iATR(_Symbol, InpTF_ENTRY, InpATRPeriod);
   handle_ADX     = iADX(_Symbol, InpTF_HTF1, 14);
   handle_BB      = iBands(_Symbol, InpTF_HTF1, 20, 0, 2.0, PRICE_CLOSE);
   
   if(handle_MA_Fast==INVALID_HANDLE || handle_MA_Slow==INVALID_HANDLE ||
      handle_RSI==INVALID_HANDLE || handle_ATR==INVALID_HANDLE ||
      handle_ADX==INVALID_HANDLE || handle_BB==INVALID_HANDLE)
   {
      Print("Indicator initialization failed");
      return(INIT_FAILED);
   }
   
   Print("EA Initialized. Auto-Optimizer: ", InpUseAutoOpt ? "ON" : "OFF");
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
      // Use static defaults
      dynRR = InpMaxRR;
      dynATRMultiplier = 1.5;
      dynFVGMinPips = InpFVGMinPips;
      dynLotMultiplier = 1.0;
      currentRegime = REGIME_TRENDING_WEAK;
   }
   
   //--- Basic Filters
   if(!IsTradeAllowed()) return;
   if(IsPositionOpen()) { ManageOpenPositions(); return; }
   if(InpUseSession && !IsTradingSession()) return;
   
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > InpMaxSpread) return;
   
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
   
   OpenTrade(entrySignal, lots, sl, tp);
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
   
   //--- Dashboard Comment
   Comment("Regime: ", regimeText, 
           " | RR: ", DoubleToString(dynRR,1),
           " | ATRx: ", DoubleToString(dynATRMultiplier,1),
           " | FVG: ", DoubleToString(dynFVGMinPips,1),
           " | Lot%: ", DoubleToString(dynLotMultiplier*100,0));
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

bool IsPositionOpen()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket<=0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol && 
         PositionGetInteger(POSITION_MAGIC)==magicNumber)
         return true;
   }
   return false;
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
   
   bool maBull = maFast[0] > maSlow[0];
   bool maBear = maFast[0] < maSlow[0];
   
   bool rsiBull = rsi[0]>40 && rsi[0]<80;
   bool rsiBear = rsi[0]>20 && rsi[0]<60;
   
   if((hh && hl) || (maBull && rsiBull)) return 1;
   if((lh && ll) || (maBear && rsiBear)) return -1;
   return 0;
}

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
      if(l[i+2] > h[i] + minGap)
      {
         zoneHigh = h[i]; zoneLow = l[i+2]; zoneType = 1;
         return 1;
      }
      if(h[i+2] < l[i] - minGap)
      {
         zoneHigh = l[i]; zoneLow = h[i+2]; zoneType = -1;
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
   
   if(signal==1)
   {
      sl=zoneLow-buffer;
      tp=price+(price-sl)*dynRR;
   }
   else
   {
      sl=zoneHigh+buffer;
      tp=price-(sl-price)*dynRR;
   }
   
   double minSL=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   if(MathAbs(price-sl)<minSL) return false;
   return true;
}

//+------------------------------------------------------------------+
double CalculateLotSize(double sl)
{
   double lots;
   if(InpRiskPercent<=0) lots=InpLotSize;
   else
   {
      double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
      double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
      double lotStep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
      double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
      double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
      
      if(tickSize==0) return InpLotSize;
      
      double entry=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      double slDist=MathAbs(entry-sl);
      double riskAmt=AccountInfoDouble(ACCOUNT_BALANCE)*InpRiskPercent/100.0;
      double ticksAtRisk=slDist/tickSize;
      lots=riskAmt/(ticksAtRisk*tickValue);
      
      lots=MathFloor(lots/lotStep)*lotStep;
      lots=MathMax(minLot,MathMin(maxLot,lots));
   }
   
   // Apply Auto-Optimizer lot multiplier (dry market = smaller size)
   lots *= dynLotMultiplier;
   
   // Ensure we don't go below broker minimum
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   if(lots < minLot) lots = minLot;
   
   return lots;
}

//+------------------------------------------------------------------+
void OpenTrade(int signal, double lots, double sl, double tp)
{
   MqlTradeRequest req={};
   MqlTradeResult res={};
   
   req.action=TRADE_ACTION_DEAL;
   req.symbol=_Symbol;
   req.volume=lots;
   req.deviation=InpSlippage;
   req.magic=magicNumber;
   req.sl=sl;
   req.tp=tp;
   
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
   
   if(!OrderSend(req,res))
      Print("OrderSend failed: ", GetLastError());
   else
   {
      Print("Trade opened #",res.order," | Regime: ",regimeText," | Lots: ",lots," | RR: ",dynRR);
      lastTradeTime=TimeCurrent();
   }
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
      
      double openPrice=PositionGetDouble(POSITION_PRICE_OPEN);
      double currSL=PositionGetDouble(POSITION_SL);
      double currTP=PositionGetDouble(POSITION_TP);
      double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
      double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      int type=(int)PositionGetInteger(POSITION_TYPE);
      
      //--- Breakeven
      if(InpUseBreakeven && currSL!=openPrice)
      {
         double triggerDist=MathAbs(currTP-openPrice)/dynRR*InpBETrigger;
         if(type==POSITION_TYPE_BUY && bid>=openPrice+triggerDist)
            ModifySL(ticket,openPrice);
         else if(type==POSITION_TYPE_SELL && ask<=openPrice-triggerDist)
            ModifySL(ticket,openPrice);
      }
      
      //--- Trailing Stop (uses dynamic ATR)
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
               if(newSL>currSL) ModifySL(ticket,newSL);
            }
            else if(type==POSITION_TYPE_SELL && currSL-ask>trailDist*2)
            {
               double newSL=NormalizeDouble(ask+trailDist,_Digits);
               if(newSL<currSL) ModifySL(ticket,newSL);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
void ModifySL(ulong ticket, double newSL)
{
   MqlTradeRequest req={};
   MqlTradeResult res={};
   req.action=TRADE_ACTION_SLTP;
   req.position=ticket;
   req.symbol=_Symbol;
   req.sl=newSL;
   req.tp=PositionGetDouble(POSITION_TP);
   OrderSend(req,res);
}
//+------------------------------------------------------------------+