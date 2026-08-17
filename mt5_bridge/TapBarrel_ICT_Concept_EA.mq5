//+------------------------------------------------------------------+
//|                                    TapBarrel_ICT_Concept_EA.mq5   |
//+------------------------------------------------------------------+
//| IMPORTANT CONTEXT (read before using):                            |
//| This EA does NOT reproduce the paid/invite-only "Tap n Barrel"    |
//| TradingView indicator - that script's source isn't public, so it |
//| can't be copied. This is an original implementation of the       |
//| general concept category it's built from: liquidity sweep ->     |
//| break of structure (BOS) -> Fibonacci OTE retracement entry ->   |
//| fixed R:R exit. These are widely-taught public ICT/SMC concepts, |
//| not proprietary to any single indicator.                         |
//|                                                                    |
//| This is a v1 starting point for backtesting and iteration, not a |
//| validated, proven system. Fractal/sweep/OTE definitions are not  |
//| singular - the choices below are one reasonable interpretation;  |
//| adjust to match how you actually read these concepts.            |
//+------------------------------------------------------------------+
#property copyright "Custom / for testing purposes"
#property version   "1.00"
#property description "Liquidity sweep + BOS + Fibonacci OTE mechanical EA"

#include <Trade\Trade.mqh>
CTrade trade;

//--- Inputs: structure detection
input int              FractalBars     = 5;           // Candles each side to confirm a swing high/low
input ENUM_TIMEFRAMES  StructureTF     = PERIOD_M15;   // Timeframe used for structure / BOS detection
input ENUM_TIMEFRAMES  EntryTF         = PERIOD_M5;    // Timeframe used for OTE entry timing

//--- Inputs: OTE zone
input double            OTE_Min        = 0.618;        // Shallow edge of OTE retracement zone
input double            OTE_Max        = 0.786;        // Deep edge of OTE retracement zone
input int               MaxOTEWaitBars = 48;           // Cancel setup if OTE zone not reached within N EntryTF bars

//--- Inputs: risk / exit
input double            RiskRewardRatio = 3.0;         // Fixed take-profit multiple of stop distance
input double            RiskPercent     = 1.0;         // % of account balance risked per trade
input bool              UseATRBuffer    = true;        // ATR-based SL buffer (recommended - portable across symbols)
input int               ATR_Period      = 14;          // ATR period for buffer calculation
input double            ATR_BufferMult  = 0.15;        // SL buffer = ATR * this multiplier
input int               SL_BufferPoints = 500;         // Fallback fixed buffer (points) if UseATRBuffer = false
input int               MaxSpreadPoints = 300;         // Skip entries if current spread exceeds this
input int                MagicNumber    = 20260817;    // EA magic number

//--- Globals
int      atrHandle        = INVALID_HANDLE;
int      structureBias    = 0;      // 0 = none, 1 = bullish, -1 = bearish
bool     waitingForOTE    = false;
double   oteFibHigh       = 0;
double   oteFibLow        = 0;
double   sweepPoint       = 0;
datetime lastBOSHighUsed  = 0;
datetime lastBOSLowUsed   = 0;
int      oteWaitBars      = 0;
datetime lastBarTime      = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);

   // Filling mode must follow the symbol: CTrade defaults to FOK, which many
   // gold/CFD brokers reject outright (retcode 10030, "unsupported filling mode").
   if(!trade.SetTypeFillingBySymbol(_Symbol))
      Print("TapBarrel EA: could not resolve filling mode for ", _Symbol,
            " - falling back to CTrade default");

   atrHandle = iATR(_Symbol, StructureTF, ATR_Period);
   if(atrHandle == INVALID_HANDLE)
   {
      Print("TapBarrel EA: failed to create ATR handle");
      return(INIT_FAILED);
   }
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(atrHandle != INVALID_HANDLE) IndicatorRelease(atrHandle);
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime currentBarTime = iTime(_Symbol, EntryTF, 0);
   if(currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

   if(HasOpenPosition())
   {
      waitingForOTE = false;
      oteWaitBars   = 0;
      return;
   }

   UpdateStructure();
   CheckForEntry();
}

//+------------------------------------------------------------------+
//| Find the most recent confirmed N-bar fractal high or low          |
//+------------------------------------------------------------------+
bool FindLastFractal(int bars, bool findHigh, double &price, datetime &ftime)
{
   int need = bars*2 + 80;
   double h[], l[];
   ArraySetAsSeries(h, true);
   ArraySetAsSeries(l, true);

   if(CopyHigh(_Symbol, StructureTF, 0, need, h) < need) return false;
   if(CopyLow(_Symbol, StructureTF, 0, need, l)  < need) return false;

   for(int i = bars+1; i < need-bars; i++)
   {
      bool ok = true;
      if(findHigh)
      {
         for(int j = 1; j <= bars && ok; j++)
            if(h[i] <= h[i-j] || h[i] <= h[i+j]) ok = false;
         if(ok) { price = h[i]; ftime = iTime(_Symbol, StructureTF, i); return true; }
      }
      else
      {
         for(int j = 1; j <= bars && ok; j++)
            if(l[i] >= l[i-j] || l[i] >= l[i+j]) ok = false;
         if(ok) { price = l[i]; ftime = iTime(_Symbol, StructureTF, i); return true; }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Detect break of structure off the last confirmed swing point      |
//+------------------------------------------------------------------+
void UpdateStructure()
{
   double fhPrice, flPrice;
   datetime fhTime, flTime;

   bool haveHigh = FindLastFractal(FractalBars, true,  fhPrice, fhTime);
   bool haveLow  = FindLastFractal(FractalBars, false, flPrice, flTime);
   if(!haveHigh || !haveLow) return;

   double lastClose = iClose(_Symbol, StructureTF, 1);

   if(lastClose > fhPrice && fhTime != lastBOSHighUsed)
   {
      structureBias   = 1;
      waitingForOTE   = true;
      oteWaitBars     = 0;
      oteFibLow       = flPrice;   // sweep / leg origin
      oteFibHigh      = lastClose; // breakout confirmation
      sweepPoint      = flPrice;
      lastBOSHighUsed = fhTime;
   }
   else if(lastClose < flPrice && flTime != lastBOSLowUsed)
   {
      structureBias  = -1;
      waitingForOTE  = true;
      oteWaitBars    = 0;
      oteFibHigh     = fhPrice;
      oteFibLow      = lastClose;
      sweepPoint     = fhPrice;
      lastBOSLowUsed = flTime;
   }
}

//+------------------------------------------------------------------+
//| SL buffer beyond the sweep point (ATR-based = portable across      |
//| symbols; this is the piece you'd otherwise have to hand-tune for  |
//| every instrument's point size separately)                        |
//+------------------------------------------------------------------+
double GetSLBuffer()
{
   if(UseATRBuffer)
   {
      double atrBuf[];
      ArraySetAsSeries(atrBuf, true);
      if(CopyBuffer(atrHandle, 0, 1, 1, atrBuf) > 0)
         return atrBuf[0] * ATR_BufferMult;
   }
   return SL_BufferPoints * _Point;
}

//+------------------------------------------------------------------+
//| Broker's minimum distance between market price and a stop level,  |
//| in price units. Sending a closer stop returns 10016 Invalid stops.|
//+------------------------------------------------------------------+
double MinStopDistance()
{
   long level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(level < 0) level = 0;
   return level * _Point;
}

//+------------------------------------------------------------------+
//| Snap a stop level onto the symbol's tick grid, rounding AWAY from |
//| the reference price - so the rounding itself can never pull a     |
//| stop back inside the broker's minimum distance.                   |
//+------------------------------------------------------------------+
double SnapStopAway(double reference, double level)
{
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0) tick = _Point;

   double snapped = (level < reference) ? MathFloor(level/tick)*tick
                                        : MathCeil(level/tick)*tick;
   return NormalizeDouble(snapped, _Digits);
}

//+------------------------------------------------------------------+
//| Check for price reaching the OTE retracement zone and enter       |
//+------------------------------------------------------------------+
void CheckForEntry()
{
   if(!waitingForOTE) return;

   oteWaitBars++;
   if(oteWaitBars > MaxOTEWaitBars) { waitingForOTE = false; return; }
   if(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > MaxSpreadPoints) return;

   double range = MathAbs(oteFibHigh - oteFibLow);
   if(range <= 0) { waitingForOTE = false; return; }

   double buffer  = GetSLBuffer();
   double minDist = MinStopDistance();

   if(structureBias == 1)
   {
      double price     = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_ASK), _Digits);
      double oteTop    = oteFibHigh - range*OTE_Min;
      double oteBottom = oteFibHigh - range*OTE_Max;

      if(price <= oteTop && price >= oteBottom)
      {
         // Stop sits behind the sweep point, pushed out to the broker's floor
         // if that sits further. Widening the stop shrinks the lot (sizing reads
         // the final distance), so risk % holds; TP is derived from the FINAL
         // stop distance so the R:R stays exact either way.
         double sl = sweepPoint - buffer;
         if(price - sl < minDist) sl = price - minDist;
         sl = SnapStopAway(price, sl);

         double slDist = price - sl;
         if(slDist > 0)
         {
            double tp = price + slDist*RiskRewardRatio;
            if(tp - price < minDist) tp = price + minDist;   // only bites if RR < 1
            tp = SnapStopAway(price, tp);

            if(OpenTrade(ORDER_TYPE_BUY, price, sl, tp)) waitingForOTE = false;
         }
      }
   }
   else if(structureBias == -1)
   {
      double price     = NormalizeDouble(SymbolInfoDouble(_Symbol, SYMBOL_BID), _Digits);
      double oteBottom = oteFibLow + range*OTE_Min;
      double oteTop    = oteFibLow + range*OTE_Max;

      if(price >= oteBottom && price <= oteTop)
      {
         double sl = sweepPoint + buffer;
         if(sl - price < minDist) sl = price + minDist;
         sl = SnapStopAway(price, sl);

         double slDist = sl - price;
         if(slDist > 0)
         {
            double tp = price - slDist*RiskRewardRatio;
            if(price - tp < minDist) tp = price - minDist;   // only bites if RR < 1
            tp = SnapStopAway(price, tp);

            if(OpenTrade(ORDER_TYPE_SELL, price, sl, tp)) waitingForOTE = false;
         }
      }
   }
}

//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
double CalculateLotSize(double price, double sl)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * RiskPercent / 100.0;
   double slDistance = MathAbs(price - sl);
   if(slDistance <= 0) return 0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0 || tickSize <= 0) return 0;

   double lossPerLot = (slDistance / tickSize) * tickValue;
   if(lossPerLot <= 0) return 0;

   double lots = riskAmount / lossPerLot;

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;

   lots = MathFloor(lots/lotStep) * lotStep;
   lots = MathMax(minLot, MathMin(maxLot, lots));

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
bool OpenTrade(ENUM_ORDER_TYPE type, double price, double sl, double tp)
{
   double lots = CalculateLotSize(price, sl);
   if(lots <= 0) return false;

   bool result = false;
   if(type == ORDER_TYPE_BUY)
      result = trade.Buy(lots, _Symbol, price, sl, tp, "TapBarrel_BOS_OTE");
   else
      result = trade.Sell(lots, _Symbol, price, sl, tp, "TapBarrel_BOS_OTE");

   if(result)
      PrintFormat("TapBarrel EA: %s opened, lots=%.2f price=%.*f sl=%.*f tp=%.*f",
                  (type==ORDER_TYPE_BUY?"BUY":"SELL"), lots,
                  _Digits, price, _Digits, sl, _Digits, tp);
   else
      PrintFormat("TapBarrel EA: %s REJECTED retcode=%u (%s) lots=%.2f price=%.*f sl=%.*f tp=%.*f",
                  (type==ORDER_TYPE_BUY?"BUY":"SELL"),
                  trade.ResultRetcode(), trade.ResultRetcodeDescription(), lots,
                  _Digits, price, _Digits, sl, _Digits, tp);

   return result;
}
//+------------------------------------------------------------------+
