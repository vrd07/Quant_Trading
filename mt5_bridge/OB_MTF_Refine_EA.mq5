//+------------------------------------------------------------------+
//|                                          OB_MTF_Refine_EA.mq5    |
//|        Multi-timeframe Order Block: 15m -> 5m -> 1m refinement    |
//+------------------------------------------------------------------+
//  THE RULE
//    1. Find an order block on 15m.
//    2. Drill into that 15m candle on 5m, then into that 5m candle on 1m,
//       to find the true origin candle. Each step narrows the zone.
//    3. When price TAPS the refined 1m zone, enter at that moment (market).
//    4. SL beyond the refined zone, TP at 1:3. Fixed 0.2 lots.
//    5. All three zones are drawn on the chart.
//
//  DEFINITIONS I HAD TO PIN DOWN (all are inputs, so change what you disagree with)
//
//  * ORDER BLOCK = the last opposing candle before a displacement leg.
//      Bullish OB : a DOWN 15m candle, after which price closes above that
//                   candle's high by >= InpDisplacementATR * ATR(14) within
//                   InpImpulseBars candles.
//      Bearish OB : mirror.
//    With InpRequireBOS the impulse must ALSO break the last swing high/low,
//    which is what separates a real order block from any old pullback candle.
//
//  * REFINEMENT = inside the 15m OB candle's own time window, take the LAST
//    opposing 5m candle; inside THAT candle's window, take the LAST opposing
//    1m candle. That 1m candle is the refined zone.
//    If a level has no opposing candle, InpRefineFallback decides whether to
//    fall back to the coarser zone or drop the setup.
//
//  * ENTRY = the proximal edge of the refined zone (the high of a bullish
//    zone / the low of a bearish zone). Tap is detected on the TICK, so the
//    fill is "exactly at that moment" -- run the tester on "Every tick based
//    on real ticks", otherwise taps are only seen at 1m boundaries.
//
//  * STOP = the distal edge of the zone chosen by InpSLZone (default: the
//    refined 1m zone) plus InpSLBufferPoints. TP is then set to exactly
//    InpRR x the REALIZED risk, measured off the actual fill price, so the
//    1:3 is exact regardless of slippage.
//
//  WARNING WORTH READING ONCE
//    Refining to a 1m candle can produce a very tight stop -- sometimes a few
//    points. That is the point of refining (small risk, big R), but it also
//    means normal noise stops you out. InpMinStopPoints puts a floor on it,
//    and InpSLZone lets you anchor the stop at the 5m or 15m zone instead
//    while still entering off the 1m tap. Compare all three before believing
//    any single equity curve.
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property version   "1.00"
#property description "15m order block refined on 5m and 1m; market entry on the 1m zone tap; fixed R:R."

//==================================================================
//  INPUTS
//==================================================================
enum ENUM_SL_ZONE
  {
   SL_REFINED_1M = 0,   // Stop beyond the refined 1m zone (tightest)
   SL_REFINED_5M = 1,   // Stop beyond the 5m zone
   SL_ORIGIN_15M = 2    // Stop beyond the original 15m order block
  };

input group           "=== Timeframes ==="
input ENUM_TIMEFRAMES InpTF_HTF          = PERIOD_M15;  // Order block timeframe
input ENUM_TIMEFRAMES InpTF_MID          = PERIOD_M5;   // First refinement
input ENUM_TIMEFRAMES InpTF_LTF          = PERIOD_M1;   // Final refinement / entry

input group           "=== Order block definition ==="
input int             InpImpulseBars     = 3;           // Displacement must complete within N candles
input double          InpDisplacementATR = 0.5;         // Displacement size (x ATR14 of the OB candle)
input bool            InpRequireBOS      = true;        // Impulse must also break the last swing
input int             InpSwingBars       = 3;           // Swing fractal size
input int             InpSwingLookback   = 30;          // How far back to look for the swing
input bool            InpBodyOnly        = false;       // Zone = candle body only (else full range)
input int             InpExpireBars      = 30;          // Drop an untraded OB after N HTF candles

input group           "=== Refinement ==="
input bool            InpRefine5M        = true;        // Refine on the mid timeframe
input bool            InpRefine1M        = true;        // Refine on the entry timeframe
input bool            InpRefineFallback  = true;        // No opposing candle -> keep coarser zone (else skip)

input group           "=== Entry / exit ==="
input bool            InpEntryOnTick     = true;        // Enter the instant price taps (else at 1m close)
input ENUM_SL_ZONE    InpSLZone          = SL_REFINED_1M;
input double          InpRR              = 3.0;         // Reward : Risk
input int             InpSLBufferPoints  = 0;           // Extra points beyond the distal edge
input int             InpMinStopPoints   = 0;           // Floor on stop distance (0 = off)
input int             InpMaxPositions    = 1;           // Concurrent positions
input bool            InpOneTradePerOB   = true;        // One entry per order block

input group           "=== Money management ==="
input double          InpFixedLot        = 0.20;        // Fixed lot

input group           "=== Misc ==="
input long            InpMagic           = 20260813;    // Magic number
input int             InpSlippage        = 30;          // Max deviation (points)
input string          InpComment         = "OBMTF";     // Order comment
input bool            InpShowZones       = true;        // Draw the order blocks
input bool            InpVerbose         = true;        // Log decisions

//==================================================================
//  STATE
//==================================================================
struct OrderBlock
  {
   bool     valid;
   bool     bullish;
   bool     traded;
   datetime obTime;       // open time of the 15m OB candle
   double   hiHTF, loHTF; // 15m zone
   double   hiMID, loMID; // 5m refined zone
   double   hiLTF, loLTF; // 1m refined zone
   double   entryLevel;   // proximal edge of the refined zone
   double   slLevel;      // distal edge (+buffer) of the zone selected by InpSLZone
  };

OrderBlock g_obs[];

int       g_hATR        = INVALID_HANDLE;
datetime  g_lastHTFBar  = 0;
datetime  g_lastLTFBar  = 0;
ulong     g_syncedTicket = 0;    // position whose TP has already been trued up to the real fill
const string OBJ_PREFIX = "OBMTF_";

//==================================================================
//  INIT / DEINIT
//==================================================================
int OnInit()
  {
   if(InpRR <= 0.0)                    { Print("[OBMTF] R:R must be > 0");            return(INIT_PARAMETERS_INCORRECT); }
   if(InpImpulseBars < 1)              { Print("[OBMTF] Impulse bars must be >= 1");  return(INIT_PARAMETERS_INCORRECT); }
   if(InpFixedLot <= 0.0)              { Print("[OBMTF] Lot must be > 0");            return(INIT_PARAMETERS_INCORRECT); }
   if(PeriodSeconds(InpTF_MID) >= PeriodSeconds(InpTF_HTF) ||
      PeriodSeconds(InpTF_LTF) >= PeriodSeconds(InpTF_MID))
     {
      Print("[OBMTF] Timeframes must descend: HTF > MID > LTF");
      return(INIT_PARAMETERS_INCORRECT);
     }

   g_hATR = iATR(_Symbol, InpTF_HTF, 14);
   if(g_hATR == INVALID_HANDLE)
     {
      Print("[OBMTF] Failed to create ATR handle, err=", GetLastError());
      return(INIT_FAILED);
     }

   ArrayFree(g_obs);
   PrintFormat("[OBMTF] init  %s  OB=%s refine=%s/%s  RR=1:%.1f  lot=%.2f  SLzone=%s",
               _Symbol, EnumToString(InpTF_HTF), EnumToString(InpTF_MID),
               EnumToString(InpTF_LTF), InpRR, InpFixedLot, EnumToString(InpSLZone));
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_hATR != INVALID_HANDLE) IndicatorRelease(g_hATR);
   ObjectsDeleteAll(0, OBJ_PREFIX);
  }

//==================================================================
//  MAIN LOOP
//==================================================================
void OnTick()
  {
   SyncTargetsToFill();

   const datetime htfBar = iTime(_Symbol, InpTF_HTF, 0);
   if(htfBar > 0 && htfBar != g_lastHTFBar)
     {
      g_lastHTFBar = htfBar;
      ScanForOrderBlocks();
      ExpireBlocks();
     }

   const datetime ltfBar = iTime(_Symbol, InpTF_LTF, 0);
   const bool newLTFBar  = (ltfBar > 0 && ltfBar != g_lastLTFBar);
   if(newLTFBar) g_lastLTFBar = ltfBar;

   if(newLTFBar) InvalidateBrokenBlocks();

   if(InpEntryOnTick) CheckTapTick();
   else if(newLTFBar) CheckTapBarClose();
  }

//==================================================================
//  ORDER BLOCK DETECTION (HTF)
//==================================================================
void ScanForOrderBlocks()
  {
   const int bars = iBars(_Symbol, InpTF_HTF);
   if(bars < InpImpulseBars + InpSwingLookback + InpSwingBars + 5) return;

   // Candidate OB candles are far enough back that their impulse has completed.
   for(int c = InpImpulseBars + 1; c >= 1; c--)
     {
      const datetime t = iTime(_Symbol, InpTF_HTF, c);
      if(t <= 0 || AlreadyRegistered(t)) continue;

      if(IsOrderBlock(c, true))       RegisterBlock(c, true);
      else if(IsOrderBlock(c, false)) RegisterBlock(c, false);
     }
  }

//------------------------------------------------------------------
bool IsOrderBlock(const int c, const bool bullish)
  {
   const double o = iOpen (_Symbol, InpTF_HTF, c);
   const double h = iHigh (_Symbol, InpTF_HTF, c);
   const double l = iLow  (_Symbol, InpTF_HTF, c);
   const double cl= iClose(_Symbol, InpTF_HTF, c);
   if(o <= 0.0 || h <= 0.0 || l <= 0.0 || cl <= 0.0) return(false);

   // The order block is the last OPPOSING candle before the move.
   if(bullish  && cl >= o) return(false);
   if(!bullish && cl <= o) return(false);

   double atr[1];
   if(CopyBuffer(g_hATR, 0, c, 1, atr) != 1 || atr[0] <= 0.0) return(false);
   const double need = InpDisplacementATR * atr[0];

   // Displacement: within InpImpulseBars candles after c, price must CLOSE
   // beyond the OB candle's extreme by at least `need`.
   bool displaced = false;
   double impulseHigh = -DBL_MAX, impulseLow = DBL_MAX;
   for(int k = c - 1; k >= MathMax(c - InpImpulseBars, 1); k--)
     {
      const double kh = iHigh (_Symbol, InpTF_HTF, k);
      const double kl = iLow  (_Symbol, InpTF_HTF, k);
      const double kc = iClose(_Symbol, InpTF_HTF, k);
      if(kh <= 0.0 || kl <= 0.0) continue;
      impulseHigh = MathMax(impulseHigh, kh);
      impulseLow  = MathMin(impulseLow,  kl);
      if(bullish  && kc > h + need) displaced = true;
      if(!bullish && kc < l - need) displaced = true;
     }
   if(!displaced) return(false);

   // The impulse must not have already run back through the block.
   if(bullish  && impulseLow  < l) return(false);
   if(!bullish && impulseHigh > h) return(false);

   if(InpRequireBOS)
     {
      const double swing = FindSwing(c, bullish);
      if(swing == 0.0) return(false);
      if(bullish  && impulseHigh <= swing) return(false);
      if(!bullish && impulseLow  >= swing) return(false);
     }
   return(true);
  }

//------------------------------------------------------------------
//  Most recent swing high (bullish) / low (bearish) formed BEFORE candle c.
//------------------------------------------------------------------
double FindSwing(const int c, const bool bullish)
  {
   const int bars = iBars(_Symbol, InpTF_HTF);
   for(int k = c + 1; k <= c + InpSwingLookback; k++)
     {
      if(k + InpSwingBars >= bars) break;

      const double pivot = bullish ? iHigh(_Symbol, InpTF_HTF, k) : iLow(_Symbol, InpTF_HTF, k);
      if(pivot <= 0.0) continue;

      bool isPivot = true;
      for(int j = 1; j <= InpSwingBars && isPivot; j++)
        {
         const double left  = bullish ? iHigh(_Symbol, InpTF_HTF, k + j) : iLow(_Symbol, InpTF_HTF, k + j);
         const double right = bullish ? iHigh(_Symbol, InpTF_HTF, k - j) : iLow(_Symbol, InpTF_HTF, k - j);
         if(left <= 0.0 || right <= 0.0) { isPivot = false; break; }
         if(bullish  && (left >= pivot || right >= pivot)) isPivot = false;
         if(!bullish && (left <= pivot || right <= pivot)) isPivot = false;
        }
      if(isPivot) return(pivot);
     }
   return(0.0);
  }

//==================================================================
//  REGISTRATION + REFINEMENT
//==================================================================
void RegisterBlock(const int c, const bool bullish)
  {
   OrderBlock ob;
   ZeroMemory(ob);
   ob.valid   = true;
   ob.bullish = bullish;
   ob.traded  = false;
   ob.obTime  = iTime(_Symbol, InpTF_HTF, c);

   ZoneOfBar(InpTF_HTF, c, ob.hiHTF, ob.loHTF);

   // ---- refine on MID inside the HTF candle's window ----
   ob.hiMID = ob.hiHTF;  ob.loMID = ob.loHTF;
   datetime midTime = 0;
   if(InpRefine5M)
     {
      if(!RefineInto(InpTF_MID, ob.obTime, PeriodSeconds(InpTF_HTF), bullish,
                     ob.hiMID, ob.loMID, midTime) && !InpRefineFallback)
        {
         if(InpVerbose) Print("[OBMTF] no ", EnumToString(InpTF_MID), " refinement - setup dropped");
         return;
        }
     }

   // ---- refine on LTF inside the MID candle's window ----
   ob.hiLTF = ob.hiMID;  ob.loLTF = ob.loMID;
   if(InpRefine1M)
     {
      const datetime base = (midTime > 0 ? midTime : ob.obTime);
      const int      span = (midTime > 0 ? PeriodSeconds(InpTF_MID) : PeriodSeconds(InpTF_HTF));
      datetime dummy = 0;
      if(!RefineInto(InpTF_LTF, base, span, bullish, ob.hiLTF, ob.loLTF, dummy) && !InpRefineFallback)
        {
         if(InpVerbose) Print("[OBMTF] no ", EnumToString(InpTF_LTF), " refinement - setup dropped");
         return;
        }
     }

   // ---- entry level: proximal edge of the refined zone ----
   ob.entryLevel = bullish ? ob.hiLTF : ob.loLTF;

   // ---- stop: distal edge of the selected zone ----
   double distal;
   switch(InpSLZone)
     {
      case SL_ORIGIN_15M: distal = bullish ? ob.loHTF : ob.hiHTF; break;
      case SL_REFINED_5M: distal = bullish ? ob.loMID : ob.hiMID; break;
      default:            distal = bullish ? ob.loLTF : ob.hiLTF; break;
     }
   const double buf = InpSLBufferPoints * _Point;
   ob.slLevel = bullish ? (distal - buf) : (distal + buf);

   if(bullish  && ob.slLevel >= ob.entryLevel) return;
   if(!bullish && ob.slLevel <= ob.entryLevel) return;

   const int n = ArraySize(g_obs);
   ArrayResize(g_obs, n + 1);
   g_obs[n] = ob;

   if(InpVerbose)
      PrintFormat("[OBMTF] %s OB @%s  15m[%s-%s] 5m[%s-%s] 1m[%s-%s]  entry=%s SL=%s (risk %s)",
                  (bullish ? "BULL" : "BEAR"), TimeToString(ob.obTime),
                  DoubleToString(ob.loHTF, _Digits), DoubleToString(ob.hiHTF, _Digits),
                  DoubleToString(ob.loMID, _Digits), DoubleToString(ob.hiMID, _Digits),
                  DoubleToString(ob.loLTF, _Digits), DoubleToString(ob.hiLTF, _Digits),
                  DoubleToString(ob.entryLevel, _Digits), DoubleToString(ob.slLevel, _Digits),
                  DoubleToString(MathAbs(ob.entryLevel - ob.slLevel), _Digits));

   DrawBlock(n);
  }

//------------------------------------------------------------------
//  Zone of a single bar: full range, or body only.
//------------------------------------------------------------------
void ZoneOfBar(const ENUM_TIMEFRAMES tf, const int shift, double &hi, double &lo)
  {
   if(InpBodyOnly)
     {
      const double o = iOpen(_Symbol, tf, shift);
      const double c = iClose(_Symbol, tf, shift);
      hi = MathMax(o, c);
      lo = MathMin(o, c);
     }
   else
     {
      hi = iHigh(_Symbol, tf, shift);
      lo = iLow (_Symbol, tf, shift);
     }
  }

//------------------------------------------------------------------
//  Inside [start, start+span) on `tf`, find the LAST opposing candle.
//------------------------------------------------------------------
bool RefineInto(const ENUM_TIMEFRAMES tf, const datetime start, const int span,
                const bool bullish, double &hi, double &lo, datetime &foundTime)
  {
   const int bars = iBars(_Symbol, tf);
   if(bars <= 0) return(false);

   int s = iBarShift(_Symbol, tf, start + span - 1, false);
   if(s < 0) return(false);

   for(; s < bars; s++)
     {
      const datetime t = iTime(_Symbol, tf, s);
      if(t <= 0) return(false);
      if(t >= start + span) continue;   // still newer than the window
      if(t < start) break;              // walked past the window

      const double o = iOpen(_Symbol, tf, s);
      const double c = iClose(_Symbol, tf, s);
      if(o <= 0.0 || c <= 0.0) continue;

      const bool opposing = bullish ? (c < o) : (c > o);
      if(!opposing) continue;

      ZoneOfBar(tf, s, hi, lo);
      foundTime = t;
      return(true);
     }
   return(false);
  }

//------------------------------------------------------------------
bool AlreadyRegistered(const datetime t)
  {
   for(int i = ArraySize(g_obs) - 1; i >= 0; i--)
      if(g_obs[i].obTime == t) return(true);
   return(false);
  }

//==================================================================
//  LIFECYCLE
//==================================================================
void ExpireBlocks()
  {
   for(int i = ArraySize(g_obs) - 1; i >= 0; i--)
     {
      if(!g_obs[i].valid) continue;
      const int age = iBarShift(_Symbol, InpTF_HTF, g_obs[i].obTime, false);
      if(age > InpExpireBars)
        {
         g_obs[i].valid = false;
         if(InpVerbose) Print("[OBMTF] OB @", TimeToString(g_obs[i].obTime), " expired");
         FadeBlock(i);
        }
     }
   CompactBlocks();
  }

//  Drop dead blocks so the per-tick scan stays O(live blocks). Entries young
//  enough to still be re-tested by ScanForOrderBlocks are kept for dedupe.
void CompactBlocks()
  {
   const int n = ArraySize(g_obs);
   const int keepAge = InpImpulseBars + 2;
   int w = 0;
   for(int i = 0; i < n; i++)
     {
      const int age = iBarShift(_Symbol, InpTF_HTF, g_obs[i].obTime, false);
      if(!g_obs[i].valid && age > keepAge) continue;
      if(w != i) g_obs[w] = g_obs[i];
      w++;
     }
   if(w != n) ArrayResize(g_obs, w);
  }

//  Once price has closed beyond our stop level the block is dead.
void InvalidateBrokenBlocks()
  {
   const double c = iClose(_Symbol, InpTF_LTF, 1);
   if(c <= 0.0) return;

   for(int i = ArraySize(g_obs) - 1; i >= 0; i--)
     {
      if(!g_obs[i].valid) continue;
      const bool broken = g_obs[i].bullish ? (c < g_obs[i].slLevel) : (c > g_obs[i].slLevel);
      if(broken)
        {
         g_obs[i].valid = false;
         if(InpVerbose) Print("[OBMTF] OB @", TimeToString(g_obs[i].obTime), " invalidated (traded through)");
         FadeBlock(i);
        }
     }
  }

//==================================================================
//  ENTRY
//==================================================================
void CheckTapTick()
  {
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(bid <= 0.0) return;

   for(int i = 0; i < ArraySize(g_obs); i++)
     {
      if(!g_obs[i].valid || g_obs[i].traded) continue;
      if(CountOurPositions() >= InpMaxPositions) return;

      const bool tapped = g_obs[i].bullish
                          ? (bid <= g_obs[i].entryLevel && bid > g_obs[i].slLevel)
                          : (bid >= g_obs[i].entryLevel && bid < g_obs[i].slLevel);
      if(tapped) FireEntry(i);
     }
  }

void CheckTapBarClose()
  {
   const double h = iHigh(_Symbol, InpTF_LTF, 1);
   const double l = iLow (_Symbol, InpTF_LTF, 1);
   if(h <= 0.0 || l <= 0.0) return;

   for(int i = 0; i < ArraySize(g_obs); i++)
     {
      if(!g_obs[i].valid || g_obs[i].traded) continue;
      if(CountOurPositions() >= InpMaxPositions) return;

      const bool tapped = g_obs[i].bullish
                          ? (l <= g_obs[i].entryLevel && l > g_obs[i].slLevel)
                          : (h >= g_obs[i].entryLevel && h < g_obs[i].slLevel);
      if(tapped) FireEntry(i);
     }
  }

//------------------------------------------------------------------
void FireEntry(const int idx)
  {
   const bool   isLong = g_obs[idx].bullish;
   const double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0.0 || bid <= 0.0) return;

   const double price = isLong ? ask : bid;
   double sl = NormPrice(g_obs[idx].slLevel);

   // Broker minimum distance, and the optional user floor.
   const double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   const double floorPt = InpMinStopPoints * _Point;
   double risk = MathAbs(price - sl);

   const double required = MathMax(minDist + _Point, floorPt);
   if(risk < required)
     {
      risk = required;
      sl = NormPrice(isLong ? price - risk : price + risk);
      if(InpVerbose)
         PrintFormat("[OBMTF] stop widened to broker/user minimum (%s)", DoubleToString(risk, _Digits));
     }
   if(risk <= 0.0) return;

   const double tp = NormPrice(isLong ? price + risk * InpRR : price - risk * InpRR);
   if(MathAbs(tp - price) <= minDist)
     {
      if(InpVerbose) Print("[OBMTF] target inside broker stop level - entry skipped");
      return;
     }

   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req);
   ZeroMemory(res);
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = _Symbol;
   req.volume    = NormLot(InpFixedLot);
   req.type      = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   req.price     = NormPrice(price);
   req.sl        = sl;
   req.tp        = tp;
   req.deviation = InpSlippage;
   req.magic     = InpMagic;
   req.comment   = InpComment;

   if(req.volume <= 0.0) { Print("[OBMTF] invalid lot - entry skipped"); return; }

   if(!SendRequest(req, res))
     {
      PrintFormat("[OBMTF] entry rejected retcode=%d (%s)", res.retcode, res.comment);
      return;
     }

   if(InpOneTradePerOB) g_obs[idx].traded = true;
   g_syncedTicket = 0;   // force a TP true-up against the real fill

   PrintFormat("[OBMTF] %s %.2f @%s  SL=%s TP=%s  risk=%s  RR=1:%.1f  (OB @%s)",
               (isLong ? "BUY" : "SELL"), req.volume,
               DoubleToString(req.price, _Digits), DoubleToString(sl, _Digits),
               DoubleToString(tp, _Digits), DoubleToString(risk, _Digits),
               InpRR, TimeToString(g_obs[idx].obTime));

   MarkEntry(idx, req.price);
  }

//------------------------------------------------------------------
//  Re-cut the TP off the ACTUAL fill so the realized R:R is exactly InpRR.
//------------------------------------------------------------------
void SyncTargetsToFill()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(ticket == g_syncedTicket) continue;

      const double open = PositionGetDouble(POSITION_PRICE_OPEN);
      const double sl   = PositionGetDouble(POSITION_SL);
      const double tp   = PositionGetDouble(POSITION_TP);
      if(open <= 0.0 || sl <= 0.0) { g_syncedTicket = ticket; continue; }

      const bool   isLong = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      const double risk   = MathAbs(open - sl);
      const double want   = NormPrice(isLong ? open + risk * InpRR : open - risk * InpRR);

      const double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(MathAbs(want - tp) > tickSize * 0.5)
        {
         MqlTradeRequest req;
         MqlTradeResult  res;
         ZeroMemory(req);
         ZeroMemory(res);
         req.action   = TRADE_ACTION_SLTP;
         req.symbol   = _Symbol;
         req.position = ticket;
         req.sl       = sl;
         req.tp       = want;
         if(OrderSend(req, res) && res.retcode == TRADE_RETCODE_DONE)
           {
            if(InpVerbose)
               PrintFormat("[OBMTF] TP trued up to %s (fill %s, risk %s)",
                           DoubleToString(want, _Digits), DoubleToString(open, _Digits),
                           DoubleToString(risk, _Digits));
           }
         else
            PrintFormat("[OBMTF] TP sync failed retcode=%d", res.retcode);
        }
      g_syncedTicket = ticket;
     }
  }

//==================================================================
//  HELPERS
//==================================================================
int CountOurPositions()
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      n++;
     }
   return(n);
  }

double NormPrice(const double price)
  {
   const double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double p = price;
   if(tickSize > 0.0) p = MathRound(price / tickSize) * tickSize;
   return(NormalizeDouble(p, _Digits));
  }

double NormLot(const double lot)
  {
   const double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   const double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   const double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double v = lot;
   if(lotStep > 0.0) v = MathRound(v / lotStep) * lotStep;
   v = MathMax(v, minLot);
   v = MathMin(v, maxLot);

   const int digits = (lotStep >= 1.0 ? 0 : (lotStep >= 0.1 ? 1 : 2));
   return(NormalizeDouble(v, digits));
  }

bool SendRequest(MqlTradeRequest &req, MqlTradeResult &res)
  {
   ENUM_ORDER_TYPE_FILLING order[3];
   int n = 0;
   const long modes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) order[n++] = ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC) != 0) order[n++] = ORDER_FILLING_IOC;
   order[n++] = ORDER_FILLING_RETURN;

   for(int i = 0; i < n; i++)
     {
      req.type_filling = order[i];
      ZeroMemory(res);
      if(OrderSend(req, res) &&
         (res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_PLACED))
         return(true);
      if(res.retcode != TRADE_RETCODE_INVALID_FILL) return(false);
     }
   return(false);
  }

//==================================================================
//  DRAWING
//==================================================================
bool CanDraw()
  {
   if(!InpShowZones) return(false);
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE)) return(false);
   return(true);
  }

void DrawBlock(const int idx)
  {
   if(!CanDraw()) return;

   const OrderBlock ob = g_obs[idx];
   const string tag = IntegerToString((long)ob.obTime);
   const datetime t2 = ob.obTime + (datetime)(PeriodSeconds(InpTF_HTF) * InpExpireBars);
   const color base = ob.bullish ? clrDodgerBlue : clrOrangeRed;

   DrawRect(OBJ_PREFIX + "H_" + tag, ob.obTime, t2, ob.hiHTF, ob.loHTF, base, true,  1);
   DrawRect(OBJ_PREFIX + "M_" + tag, ob.obTime, t2, ob.hiMID, ob.loMID, base, false, 2);
   DrawRect(OBJ_PREFIX + "L_" + tag, ob.obTime, t2, ob.hiLTF, ob.loLTF,
            (ob.bullish ? clrLime : clrRed), false, 3);

   const string lbl = OBJ_PREFIX + "T_" + tag;
   if(ObjectCreate(0, lbl, OBJ_TEXT, 0, ob.obTime, ob.bullish ? ob.hiHTF : ob.loHTF))
     {
      ObjectSetString(0, lbl, OBJPROP_TEXT,
                      (ob.bullish ? "OB+ " : "OB- ") + DoubleToString(ob.entryLevel, _Digits));
      ObjectSetInteger(0, lbl, OBJPROP_COLOR, base);
      ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, ob.bullish ? ANCHOR_LOWER : ANCHOR_UPPER);
      ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, true);
     }
  }

void DrawRect(const string name, const datetime t1, const datetime t2,
              const double p1, const double p2, const color clr,
              const bool filled, const int width)
  {
   if(ObjectFind(0, name) < 0)
      if(!ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2)) return;

   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FILL, filled);
   ObjectSetInteger(0, name, OBJPROP_BACK, filled);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

//  Grey out a zone that expired or was invalidated, so the chart keeps history.
void FadeBlock(const int idx)
  {
   if(!CanDraw()) return;
   const string tag = IntegerToString((long)g_obs[idx].obTime);
   const string names[4] = {"H_", "M_", "L_", "T_"};
   for(int i = 0; i < 4; i++)
     {
      const string n = OBJ_PREFIX + names[i] + tag;
      if(ObjectFind(0, n) >= 0)
        {
         ObjectSetInteger(0, n, OBJPROP_COLOR, clrDimGray);
         ObjectSetInteger(0, n, OBJPROP_FILL, false);
        }
     }
  }

void MarkEntry(const int idx, const double price)
  {
   if(!CanDraw()) return;
   const string n = OBJ_PREFIX + "E_" + IntegerToString((long)g_obs[idx].obTime);
   if(ObjectCreate(0, n, g_obs[idx].bullish ? OBJ_ARROW_BUY : OBJ_ARROW_SELL,
                   0, TimeCurrent(), price))
     {
      ObjectSetInteger(0, n, OBJPROP_COLOR, g_obs[idx].bullish ? clrLime : clrRed);
      ObjectSetInteger(0, n, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
     }
  }
//+------------------------------------------------------------------+
