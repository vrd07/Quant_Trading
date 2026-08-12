//+------------------------------------------------------------------+
//|                                          Fib_GoldenZone_EA.mq5   |
//|      Fibonacci retracement entries -- two selectable rule sets    |
//+------------------------------------------------------------------+
//  WHY THERE ARE TWO MODELS IN ONE EA
//
//  This EA was built from fib.md (a Pine Script titled "Wayond Gold
//  Strategy - Fibonacci Golden Zone") and cross-checked against the video
//  it is named after:
//
//      "The Simplest Gold Trading Strategy Using Two Fibonacci Levels"
//      Wayond / "Strategies" episode with Prasad -- youtu.be/yuGZZEArBks
//
//  They are NOT the same strategy. The Pine Script borrowed the title and
//  then implemented something else. The differences are load-bearing:
//
//    LEVELS   video enters at 0.70 and 0.786.  Pine enters 0.382-0.618.
//    FILTERS  video uses NO indicators at all ("indicators are lagging",
//             he dropped RSI/MACD in 2014).  Pine adds RSI(14) + EMA(50).
//    ENTRY    video scales in: half at 0.70, half at 0.786, then adds more
//             when a candle closes in direction.  Pine takes one entry.
//    STOP     video stops at the swing origin, then trails it under the
//             confirmation candle.  Pine stops at 0.786 -- which is the
//             video's SECOND ENTRY. The same number, opposite job.
//    TARGET   video aims at the swing high / next resistance.  Pine aims
//             at a 1.618 extension.
//    FILTER   video's one non-negotiable is an AGGRESSIVE impulse leg;
//             sideways approach into the levels, he says, do not trade it.
//             Pine has no such filter.
//
//  So InpEntryModel picks which one runs. Everything upstream of the entry
//  decision (swing detection, impulse qualification, the first-tap latch)
//  is shared, so an A/B in the Strategy Tester changes only the rule set.
//
//  DEVIATION FROM THE PINE SCRIPT -- READ THIS ONCE
//    The Pine Script's fib stop is inverted in both directions. It computes
//        fib786Long = swingLow + range * 0.786
//    which sits ABOVE a 0.382-0.618 long entry -- a long whose stop is above
//    its entry. Shorts mirror the same error. Ported literally, every trade
//    would close on the spot. The 78.6% retracement of an up-leg is
//        swingHigh - range * 0.786   ( == swingLow + range * 0.214 )
//    which is below the entry zone, and that is what MODEL_PINESCRIPT uses.
//    Its golden zone itself is fine: [lo+0.382R, lo+0.618R] and
//    [hi-0.618R, hi-0.382R] are the same band, only inversely labelled.
//
//  TWO MORE THINGS THE PINE SCRIPT DOES THAT THIS EA DOES NOT
//    * It pairs whatever the latest swing high and swing low happen to be,
//      with no regard for which came first, so it will happily measure a
//      "retracement" backwards. This EA requires low->high->pullback for a
//      long and high->low->pullback for a short.
//    * It re-arms on a bar cooldown. This EA uses a first-tap latch, which
//      is what the video actually describes ("for the first tap at 70 or
//      786 it will work the best").
//
//  WHAT THIS EA IS NOT
//    The video's evidence is a handful of hand-picked charts and a claimed
//    "80% win rate". Nothing here has been walk-forward tested or run
//    against a random-entry control. Treat any equity curve it produces as
//    a hypothesis, not a result -- a rule of this shape in this repo (the
//    H4 double-FVG tap) beat only 58% of matched random draws once it was
//    controlled properly. InpVerbose logs every arm and every skip with a
//    reason so you can confirm the impulse gate is actually rejecting
//    setups rather than silently passing everything through.
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property version   "1.00"
#property description "Fibonacci retracement EA: video model (0.70/0.786 scale-in) or Pine model (0.382-0.618 + RSI/EMA), switchable."

//==================================================================
//  INPUTS
//==================================================================
enum ENUM_ENTRY_MODEL
  {
   MODEL_VIDEO      = 0,  // Video: 0.70 + 0.786 scale-in, no indicators
   MODEL_PINESCRIPT = 1   // Pine: 0.382-0.618 zone + RSI + EMA filter
  };

input group             "=== Model ==="
input ENUM_ENTRY_MODEL  InpEntryModel     = MODEL_VIDEO;   // Which rule set to run
input ENUM_TIMEFRAMES   InpFibTF          = PERIOD_H1;     // Timeframe the Fibonacci is drawn on
input ENUM_TIMEFRAMES   InpConfirmTF      = PERIOD_M15;    // Confirmation TF (one below the fib TF)
input string            InpSymbolGate     = "";            // Only run if symbol starts with this ("" = any)

input group             "=== Swing detection ==="
input int               InpSwingBars      = 5;             // Fractal size (bars each side of a pivot)
input bool              InpUseWicks       = true;          // Anchor on wick extremes (video: yes)
input int               InpExpireBars     = 40;            // Drop an untraded setup after N fib bars

input group             "=== Impulse gate (the video's non-negotiable) ==="
input double            InpImpulseATR     = 2.0;           // Leg must span >= this x ATR(14). 0 = off
input int               InpImpulseBars    = 10;            // ...and form within this many bars. 0 = off
input int               InpATRPeriod      = 14;            // ATR period for the impulse gate

input group             "=== VIDEO model: levels and scale-in ==="
input double            InpFibEntry1      = 0.700;         // First entry retracement
input double            InpFibEntry2      = 0.786;         // Second entry retracement
input double            InpLotLeg1        = 0.05;          // Lot at entry 1
input double            InpLotLeg2        = 0.05;          // Lot at entry 2
input double            InpLotAdd         = 0.10;          // Lot added on the confirmation close
input bool              InpUseConfirmAdd  = true;          // Add a leg when a candle closes in direction
input bool              InpTrailOnConfirm = true;          // Trail the stop under each confirmation candle
input double            InpMinRR          = 1.5;           // Skip the setup below this reward:risk

input group             "=== PINESCRIPT model: zone and filters ==="
input double            InpFibZoneStart   = 0.382;         // Golden zone near edge
input double            InpFibZoneEnd     = 0.618;         // Golden zone far edge
input double            InpFibStop        = 0.786;         // Stop retracement
input double            InpFibTarget      = 1.618;         // Take-profit extension
input double            InpPineLot        = 0.10;          // Lot for the single entry
input bool              InpUseTrendFilter = true;          // EMA trend filter
input int               InpTrendLen       = 50;            // Trend EMA length
input bool              InpUseRSI         = true;          // RSI confirmation
input int               InpRSILen         = 14;            // RSI period
input int               InpRSILongMax     = 50;            // Longs need RSI below this
input int               InpRSIShortMin    = 50;            // Shorts need RSI above this

input group             "=== Risk / execution ==="
input int               InpSLBufferPoints = 0;             // Extra points beyond the stop anchor
input int               InpMaxPositions   = 3;             // Concurrent positions from this EA
input bool              InpEntryOnTick    = true;          // Fill taps intrabar (else at bar close)

input group             "=== Misc ==="
input long              InpMagic          = 20260812;      // Magic number
input int               InpSlippage       = 30;            // Max deviation (points)
input string            InpComment        = "FIBGZ";       // Order comment
input bool              InpShowLevels     = true;          // Draw the fib levels
input bool              InpVerbose        = true;          // Log every arm and skip

//==================================================================
//  STATE
//==================================================================
struct FibSetup
  {
   bool     active;
   bool     bullish;        // true  = long  (impulse up, buy the pullback)
                            // false = short (impulse down, sell the rally)
   datetime originTime;     // the swing the move started FROM (fib 1.0)
   datetime endTime;        // the swing the move ended AT   (fib 0.0)
   double   origin;         // price at fib 1.0
   double   end;            // price at fib 0.0
   double   range;          // |end - origin|
   double   lvl1, lvl2;     // entry levels (video) / zone edges (pine)
   double   sl;             // current stop for the whole setup
   double   tp;
   bool     leg1Done, leg2Done, addDone;
   bool     anyFilled;
   bool     sawPosition;    // a fill has actually shown up in the book
   datetime firstFillTime;
   datetime armedAt;
  };

FibSetup g_setup;

// Last leg we rejected, so a failing setup logs its reason once rather than
// on every bar until the pivots move.
datetime g_rejOriginTime = 0, g_rejEndTime = 0;

// Most recent confirmed pivots on the fib TF
double   g_swingHi      = 0.0,  g_swingLo      = 0.0;
datetime g_swingHiTime  = 0,    g_swingLoTime  = 0;

int      g_hATR   = INVALID_HANDLE;
int      g_hRSI   = INVALID_HANDLE;
int      g_hEMA   = INVALID_HANDLE;

datetime g_lastFibBar     = 0;
datetime g_lastConfirmBar = 0;

const string OBJ_PREFIX = "FIBGZ_";

//==================================================================
//  INIT / DEINIT
//==================================================================
int OnInit()
  {
   if(InpSymbolGate != "" && StringFind(_Symbol, InpSymbolGate) != 0)
     {
      PrintFormat("[FIBGZ] Symbol gate '%s' blocks %s -- EA idle.", InpSymbolGate, _Symbol);
      return(INIT_FAILED);
     }

   if(InpSwingBars < 1)
     { Print("[FIBGZ] Swing bars must be >= 1"); return(INIT_PARAMETERS_INCORRECT); }
   if(PeriodSeconds(InpConfirmTF) > PeriodSeconds(InpFibTF))
     { Print("[FIBGZ] Confirmation TF must not be higher than the fib TF"); return(INIT_PARAMETERS_INCORRECT); }
   if(InpMinRR < 0.0)
     { Print("[FIBGZ] Min R:R must be >= 0"); return(INIT_PARAMETERS_INCORRECT); }

   if(InpEntryModel == MODEL_VIDEO)
     {
      if(InpFibEntry1 <= 0.0 || InpFibEntry2 <= 0.0 || InpFibEntry1 >= 1.0 || InpFibEntry2 >= 1.0)
        { Print("[FIBGZ] Entry retracements must be between 0 and 1"); return(INIT_PARAMETERS_INCORRECT); }
      if(InpFibEntry2 <= InpFibEntry1)
        { Print("[FIBGZ] Entry 2 must be a deeper retracement than entry 1"); return(INIT_PARAMETERS_INCORRECT); }
      if(InpLotLeg1 <= 0.0 && InpLotLeg2 <= 0.0)
        { Print("[FIBGZ] At least one entry leg needs a lot size"); return(INIT_PARAMETERS_INCORRECT); }
     }
   else
     {
      if(InpFibZoneEnd <= InpFibZoneStart)
        { Print("[FIBGZ] Zone end must be deeper than zone start"); return(INIT_PARAMETERS_INCORRECT); }
      if(InpFibStop <= InpFibZoneEnd)
        { Print("[FIBGZ] Stop retracement must be deeper than the zone"); return(INIT_PARAMETERS_INCORRECT); }
      if(InpFibTarget <= 1.0)
        { Print("[FIBGZ] Target extension must be > 1.0"); return(INIT_PARAMETERS_INCORRECT); }
      if(InpPineLot <= 0.0)
        { Print("[FIBGZ] Pine lot must be > 0"); return(INIT_PARAMETERS_INCORRECT); }
     }

   if(InpImpulseATR > 0.0)
     {
      g_hATR = iATR(_Symbol, InpFibTF, InpATRPeriod);
      if(g_hATR == INVALID_HANDLE) { Print("[FIBGZ] ATR handle failed"); return(INIT_FAILED); }
     }

   // The video model creates no indicator handles at all -- that is the point of it.
   if(InpEntryModel == MODEL_PINESCRIPT)
     {
      if(InpUseRSI)
        {
         g_hRSI = iRSI(_Symbol, InpFibTF, InpRSILen, PRICE_CLOSE);
         if(g_hRSI == INVALID_HANDLE) { Print("[FIBGZ] RSI handle failed"); return(INIT_FAILED); }
        }
      if(InpUseTrendFilter)
        {
         g_hEMA = iMA(_Symbol, InpFibTF, InpTrendLen, 0, MODE_EMA, PRICE_CLOSE);
         if(g_hEMA == INVALID_HANDLE) { Print("[FIBGZ] EMA handle failed"); return(INIT_FAILED); }
        }
     }

   ClearSetup();

   PrintFormat("[FIBGZ] %s on %s  fib=%s confirm=%s  impulse>=%.2fxATR within %d bars",
               (InpEntryModel == MODEL_VIDEO ? "VIDEO (0.70/0.786 scale-in)"
                                             : "PINESCRIPT (0.382-0.618 + RSI/EMA)"),
               _Symbol, EnumToString(InpFibTF), EnumToString(InpConfirmTF),
               InpImpulseATR, InpImpulseBars);

   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_hATR != INVALID_HANDLE) IndicatorRelease(g_hATR);
   if(g_hRSI != INVALID_HANDLE) IndicatorRelease(g_hRSI);
   if(g_hEMA != INVALID_HANDLE) IndicatorRelease(g_hEMA);
   ObjectsDeleteAll(0, OBJ_PREFIX);
  }

//==================================================================
//  MAIN LOOP
//==================================================================
void OnTick()
  {
   const datetime fibBar = (datetime)iTime(_Symbol, InpFibTF, 0);
   if(fibBar == 0) return;

   if(fibBar != g_lastFibBar)
     {
      g_lastFibBar = fibBar;
      DetectPivots();
      ExpireSetup();
      TryArmSetup();
      DrawSetup();

      // Pine takes its single entry on a closed fib bar; the video model
      // fills on the tap, so it only needs the bar event for housekeeping.
      if(InpEntryModel == MODEL_PINESCRIPT) CheckPineEntry();
     }

   const datetime confirmBar = (datetime)iTime(_Symbol, InpConfirmTF, 0);
   if(confirmBar != 0 && confirmBar != g_lastConfirmBar)
     {
      g_lastConfirmBar = confirmBar;
      if(InpEntryModel == MODEL_VIDEO)
        {
         // With tick fills off, the confirmation TF close is the entry
         // opportunity -- otherwise the taps would never be checked at all.
         if(!InpEntryOnTick) CheckVideoTaps();
         HandleConfirmation();
        }
     }

   if(InpEntryModel == MODEL_VIDEO && InpEntryOnTick) CheckVideoTaps();

   ManageInvalidation();
  }

//==================================================================
//  SWING DETECTION
//    Fractal pivots on the fib TF, confirmed InpSwingBars bars late so
//    nothing here can see the future. Anchored on wicks by default --
//    "wherever you see the low of it, even if it has a wick, you'll have
//    to start with the wick."
//==================================================================
void DetectPivots()
  {
   const int need = InpSwingBars * 2 + 2;
   if(Bars(_Symbol, InpFibTF) < need + InpATRPeriod) return;

   const int c = InpSwingBars + 1;   // candidate shift: fully closed, fully surrounded

   double cHi = PivotPrice(c, true);
   double cLo = PivotPrice(c, false);

   bool isHigh = true, isLow = true;
   for(int k = 1; k <= InpSwingBars; k++)
     {
      if(PivotPrice(c - k, true)  >= cHi) isHigh = false;
      if(PivotPrice(c + k, true)  >  cHi) isHigh = false;
      if(PivotPrice(c - k, false) <= cLo) isLow  = false;
      if(PivotPrice(c + k, false) <  cLo) isLow  = false;
     }

   const datetime t = (datetime)iTime(_Symbol, InpFibTF, c);

   if(isHigh && t != g_swingHiTime)
     {
      g_swingHi = cHi; g_swingHiTime = t;
     }
   if(isLow && t != g_swingLoTime)
     {
      g_swingLo = cLo; g_swingLoTime = t;
     }
  }

double PivotPrice(const int shift, const bool wantHigh)
  {
   if(InpUseWicks)
      return(wantHigh ? iHigh(_Symbol, InpFibTF, shift) : iLow(_Symbol, InpFibTF, shift));

   const double o = iOpen(_Symbol, InpFibTF, shift);
   const double c = iClose(_Symbol, InpFibTF, shift);
   return(wantHigh ? MathMax(o, c) : MathMin(o, c));
  }

//==================================================================
//  SETUP ARMING
//==================================================================
void TryArmSetup()
  {
   // A setup that has already been traded owns the slot until it resolves.
   if(g_setup.active && g_setup.anyFilled) return;
   if(g_swingHiTime == 0 || g_swingLoTime == 0) return;
   if(g_swingHi <= g_swingLo) return;

   // Direction comes from which pivot completed the leg.
   const bool bullish = (g_swingLoTime < g_swingHiTime);

   const double origin     = bullish ? g_swingLo     : g_swingHi;
   const double endPx      = bullish ? g_swingHi     : g_swingLo;
   const datetime originT  = bullish ? g_swingLoTime : g_swingHiTime;
   const datetime endT     = bullish ? g_swingHiTime : g_swingLoTime;

   // Already armed on this exact leg? nothing to do.
   if(g_setup.active && g_setup.originTime == originT && g_setup.endTime == endT) return;

   // Already rejected this exact leg? re-check silently -- the pivots only
   // move when a new one confirms, so otherwise every bar re-logs the skip.
   const bool alreadyRejected = (g_rejOriginTime == originT && g_rejEndTime == endT);
   const bool talk            = (InpVerbose && !alreadyRejected);

   const double range = MathAbs(endPx - origin);
   if(range <= 0.0) return;

   if(!ImpulseQualifies(range, originT, endT, talk))
     {
      g_rejOriginTime = originT; g_rejEndTime = endT;
      return;
     }

   FibSetup s;
   ZeroMemory(s);
   s.active     = true;
   s.bullish    = bullish;
   s.origin     = origin;
   s.end        = endPx;
   s.originTime = originT;
   s.endTime    = endT;
   s.range      = range;
   s.armedAt    = (datetime)iTime(_Symbol, InpFibTF, 0);

   const double buf = InpSLBufferPoints * _Point;

   if(InpEntryModel == MODEL_VIDEO)
     {
      // Retracement measured back from the end of the leg.
      s.lvl1 = RetraceLevel(s, InpFibEntry1);
      s.lvl2 = RetraceLevel(s, InpFibEntry2);
      s.sl   = bullish ? origin - buf : origin + buf;   // the swing the move came from
      s.tp   = endPx;                                    // the swing high / low it came to
     }
   else
     {
      s.lvl1 = RetraceLevel(s, InpFibZoneStart);         // near edge of the golden zone
      s.lvl2 = RetraceLevel(s, InpFibZoneEnd);           // far edge
      s.sl   = RetraceLevel(s, InpFibStop);              // corrected: deeper than the zone
      s.sl   = bullish ? s.sl - buf : s.sl + buf;
      // 1.618 extension beyond the end of the leg.
      s.tp   = bullish ? endPx + (InpFibTarget - 1.0) * range
                       : endPx - (InpFibTarget - 1.0) * range;
     }

   // Worst-case R:R is the shallower entry, so gate on lvl1.
   const double risk   = MathAbs(s.lvl1 - s.sl);
   const double reward = MathAbs(s.tp   - s.lvl1);
   if(risk <= 0.0)
     {
      if(talk) Print("[FIBGZ] skip: degenerate stop distance");
      g_rejOriginTime = originT; g_rejEndTime = endT;
      return;
     }
   const double rr = reward / risk;
   if(rr < InpMinRR)
     {
      if(talk)
         PrintFormat("[FIBGZ] skip %s setup: R:R %.2f below floor %.2f",
                     (bullish ? "LONG" : "SHORT"), rr, InpMinRR);
      g_rejOriginTime = originT; g_rejEndTime = endT;
      return;
     }

   // Do not arm a leg price has already retraced straight through.
   const double px = SymbolInfoDouble(_Symbol, bullish ? SYMBOL_BID : SYMBOL_ASK);
   if((bullish && px <= s.sl) || (!bullish && px >= s.sl))
     {
      if(talk) Print("[FIBGZ] skip: price already beyond the setup origin");
      g_rejOriginTime = originT; g_rejEndTime = endT;
      return;
     }

   g_setup = s;

   if(InpVerbose)
      PrintFormat("[FIBGZ] ARM %s  origin=%s end=%s range=%s  L1=%s L2=%s SL=%s TP=%s  R:R %.2f",
                  (bullish ? "LONG" : "SHORT"),
                  DoubleToString(origin, _Digits), DoubleToString(endPx, _Digits),
                  DoubleToString(range, _Digits),
                  DoubleToString(s.lvl1, _Digits), DoubleToString(s.lvl2, _Digits),
                  DoubleToString(s.sl, _Digits),   DoubleToString(s.tp, _Digits), rr);
  }

// Retracement price at ratio r, measured back from the end of the leg
// toward its origin. r=0 is the end of the move, r=1 is where it started.
double RetraceLevel(const FibSetup &s, const double r)
  {
   return(s.bullish ? s.end - r * s.range : s.end + r * s.range);
  }

bool ImpulseQualifies(const double range, const datetime originT, const datetime endT,
                      const bool talk)
  {
   if(InpImpulseATR > 0.0)
     {
      const double atr = CurrentATR();
      if(atr <= 0.0) return(false);
      if(range < InpImpulseATR * atr)
        {
         if(talk)
            PrintFormat("[FIBGZ] skip: leg %s < %.2f x ATR %s (not aggressive)",
                        DoubleToString(range, _Digits), InpImpulseATR, DoubleToString(atr, _Digits));
         return(false);
        }
     }

   if(InpImpulseBars > 0)
     {
      const int iOrigin = iBarShift(_Symbol, InpFibTF, originT, true);
      const int iEnd    = iBarShift(_Symbol, InpFibTF, endT,    true);
      if(iOrigin < 0 || iEnd < 0) return(false);
      const int legBars = MathAbs(iOrigin - iEnd);
      if(legBars > InpImpulseBars)
        {
         if(talk)
            PrintFormat("[FIBGZ] skip: leg took %d bars > %d (too slow to be aggressive)",
                        legBars, InpImpulseBars);
         return(false);
        }
     }

   return(true);
  }

double CurrentATR()
  {
   if(g_hATR == INVALID_HANDLE) return(0.0);
   double buf[1];
   if(CopyBuffer(g_hATR, 0, 1, 1, buf) != 1) return(0.0);
   return(buf[0]);
  }

void ExpireSetup()
  {
   if(!g_setup.active || g_setup.anyFilled) return;
   if(InpExpireBars <= 0) return;

   // -1 means the arming bar has fallen out of history; drop the setup
   // rather than let it sit armed forever on a stale reference.
   const int armedShift = iBarShift(_Symbol, InpFibTF, g_setup.armedAt, true);
   if(armedShift < 0 || armedShift >= InpExpireBars)
     {
      if(InpVerbose) Print("[FIBGZ] setup expired untraded");
      ClearSetup();
     }
  }

void ClearSetup()
  {
   ZeroMemory(g_setup);
   ObjectsDeleteAll(0, OBJ_PREFIX);
  }

//==================================================================
//  VIDEO MODEL -- tap entries at 0.70 and 0.786
//==================================================================
void CheckVideoTaps()
  {
   if(!g_setup.active) return;
   if(CountOurPositions() >= InpMaxPositions) return;

   const double px = SymbolInfoDouble(_Symbol, g_setup.bullish ? SYMBOL_ASK : SYMBOL_BID);
   if(px <= 0.0) return;

   // A long pulls DOWN into its levels, a short rallies UP into them.
   if(!g_setup.leg1Done && InpLotLeg1 > 0.0 && Reached(px, g_setup.lvl1))
     {
      if(OpenLeg(InpLotLeg1, "L1"))
        {
         g_setup.leg1Done = true;
         MarkFilled();
        }
     }

   if(!g_setup.leg2Done && InpLotLeg2 > 0.0 && Reached(px, g_setup.lvl2))
     {
      if(CountOurPositions() < InpMaxPositions && OpenLeg(InpLotLeg2, "L2"))
        {
         g_setup.leg2Done = true;
         MarkFilled();
        }
     }
  }

bool Reached(const double px, const double level)
  {
   return(g_setup.bullish ? px <= level : px >= level);
  }

void MarkFilled()
  {
   if(!g_setup.anyFilled)
     {
      g_setup.anyFilled     = true;
      g_setup.firstFillTime = TimeCurrent();
     }
  }

//==================================================================
//  VIDEO MODEL -- confirmation candle: add a leg, then trail
//    "Once you have a candle closing bullish here, that's when you need to
//     shift the stop loss below this."  The add happens once; every later
//     confirmation candle just drags the stop along.
//==================================================================
void HandleConfirmation()
  {
   if(!g_setup.active || !g_setup.anyFilled) return;

   const datetime barT = (datetime)iTime(_Symbol, InpConfirmTF, 1);
   if(barT == 0 || barT < g_setup.firstFillTime) return;   // must close AFTER we were in

   const double o = iOpen (_Symbol, InpConfirmTF, 1);
   const double c = iClose(_Symbol, InpConfirmTF, 1);
   const double h = iHigh (_Symbol, InpConfirmTF, 1);
   const double l = iLow  (_Symbol, InpConfirmTF, 1);

   const bool inDirection = g_setup.bullish ? (c > o) : (c < o);
   if(!inDirection) return;

   if(InpUseConfirmAdd && !g_setup.addDone && InpLotAdd > 0.0 &&
      CountOurPositions() < InpMaxPositions)
     {
      if(OpenLeg(InpLotAdd, "ADD"))
        {
         g_setup.addDone = true;
         if(InpVerbose) Print("[FIBGZ] confirmation close -> added leg");
        }
     }

   if(InpTrailOnConfirm)
     {
      const double buf    = InpSLBufferPoints * _Point;
      const double anchor = g_setup.bullish ? l - buf : h + buf;
      TightenStops(anchor);
     }
  }

//==================================================================
//  PINESCRIPT MODEL -- single entry inside the golden zone
//==================================================================
void CheckPineEntry()
  {
   if(!g_setup.active || g_setup.anyFilled) return;
   if(CountOurPositions() >= InpMaxPositions) return;

   const double o = iOpen (_Symbol, InpFibTF, 1);
   const double c = iClose(_Symbol, InpFibTF, 1);
   const double h = iHigh (_Symbol, InpFibTF, 1);
   const double l = iLow  (_Symbol, InpFibTF, 1);

   // Bar range overlaps the zone, exactly as the Pine Script tests it.
   const double zHi = MathMax(g_setup.lvl1, g_setup.lvl2);
   const double zLo = MathMin(g_setup.lvl1, g_setup.lvl2);
   if(!(l <= zHi && h >= zLo)) return;

   const bool candleOK = g_setup.bullish ? (c > o) : (c < o);
   if(!candleOK) return;

   if(InpUseTrendFilter && g_hEMA != INVALID_HANDLE)
     {
      double ema[1];
      if(CopyBuffer(g_hEMA, 0, 1, 1, ema) != 1) return;
      const bool trendOK = g_setup.bullish ? (c > ema[0]) : (c < ema[0]);
      if(!trendOK) return;
     }

   if(InpUseRSI && g_hRSI != INVALID_HANDLE)
     {
      double rsi[1];
      if(CopyBuffer(g_hRSI, 0, 1, 1, rsi) != 1) return;
      const bool rsiOK = g_setup.bullish ? (rsi[0] < InpRSILongMax) : (rsi[0] > InpRSIShortMin);
      if(!rsiOK) return;
     }

   if(OpenLeg(InpPineLot, "PINE"))
      MarkFilled();
  }

//==================================================================
//  INVALIDATION
//==================================================================
void ManageInvalidation()
  {
   if(!g_setup.active) return;

   // Once every leg is resolved and nothing is open, the setup is spent --
   // the first-tap latch means we do not re-enter the same leg.
   // Only after a fill has actually appeared in the book: a broker that
   // answers PLACED rather than DONE leaves a tick or two where we are
   // filled on paper but flat in the terminal, and clearing there would
   // drop the latch and re-arm the same leg.
   if(g_setup.anyFilled)
     {
      if(CountOurPositions() > 0)
         g_setup.sawPosition = true;
      else if(g_setup.sawPosition)
        {
         if(InpVerbose) Print("[FIBGZ] setup resolved (flat) -- latch cleared");
         ClearSetup();
         return;
        }
     }

   if(!g_setup.anyFilled)
     {
      const double px = SymbolInfoDouble(_Symbol, g_setup.bullish ? SYMBOL_BID : SYMBOL_ASK);
      if(px > 0.0 && ((g_setup.bullish && px < g_setup.sl) || (!g_setup.bullish && px > g_setup.sl)))
        {
         if(InpVerbose) Print("[FIBGZ] setup invalidated -- price broke the origin before entry");
         ClearSetup();
        }
     }
  }

//==================================================================
//  ORDER PLUMBING
//==================================================================
bool OpenLeg(const double lot, const string tag)
  {
   const double vol = NormLot(lot);
   if(vol <= 0.0) return(false);

   const bool   isLong = g_setup.bullish;
   const double price  = SymbolInfoDouble(_Symbol, isLong ? SYMBOL_ASK : SYMBOL_BID);
   if(price <= 0.0) return(false);

   double sl = NormPrice(g_setup.sl);
   double tp = NormPrice(g_setup.tp);

   if(!StopsAreSane(isLong, price, sl, tp))
     {
      if(InpVerbose)
         PrintFormat("[FIBGZ] %s rejected: stop/target too close to market", tag);
      return(false);
     }

   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req);
   ZeroMemory(res);
   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = _Symbol;
   req.volume       = vol;
   req.type         = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   req.price        = NormPrice(price);
   req.sl           = sl;
   req.tp           = tp;
   req.deviation    = InpSlippage;
   req.magic        = InpMagic;
   req.comment      = InpComment + "_" + tag;

   if(!SendRequest(req, res))
     {
      PrintFormat("[FIBGZ] %s order failed retcode=%d", tag, res.retcode);
      return(false);
     }

   if(InpVerbose)
      PrintFormat("[FIBGZ] %s %s %.2f @ %s  SL=%s TP=%s",
                  tag, (isLong ? "BUY" : "SELL"), vol,
                  DoubleToString(res.price > 0.0 ? res.price : price, _Digits),
                  DoubleToString(sl, _Digits), DoubleToString(tp, _Digits));
   return(true);
  }

// Move stops toward the anchor. Never loosens an existing stop, and never
// sets one the broker would reject for sitting inside the freeze band.
void TightenStops(const double anchor)
  {
   const double stopLvl = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)  continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)   continue;

      const bool   isLong = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      const double curSL  = PositionGetDouble(POSITION_SL);
      const double tp     = PositionGetDouble(POSITION_TP);
      const double want   = NormPrice(anchor);

      if(isLong  && curSL > 0.0 && want <= curSL) continue;   // would loosen
      if(!isLong && curSL > 0.0 && want >= curSL) continue;

      const double mkt = SymbolInfoDouble(_Symbol, isLong ? SYMBOL_BID : SYMBOL_ASK);
      if(isLong  && want >= mkt - stopLvl) continue;
      if(!isLong && want <= mkt + stopLvl) continue;

      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);
      req.action   = TRADE_ACTION_SLTP;
      req.symbol   = _Symbol;
      req.position = ticket;
      req.sl       = want;
      req.tp       = tp;

      if(OrderSend(req, res) && res.retcode == TRADE_RETCODE_DONE)
        {
         g_setup.sl = want;
         if(InpVerbose)
            PrintFormat("[FIBGZ] stop trailed to %s on #%I64u", DoubleToString(want, _Digits), ticket);
        }
      else
         PrintFormat("[FIBGZ] stop trail failed on #%I64u retcode=%d", ticket, res.retcode);
     }
  }

bool StopsAreSane(const bool isLong, const double price, const double sl, const double tp)
  {
   const double stopLvl = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;

   if(isLong)
     {
      if(sl >= price - stopLvl) return(false);
      if(tp <= price + stopLvl) return(false);
     }
   else
     {
      if(sl <= price + stopLvl) return(false);
      if(tp >= price - stopLvl) return(false);
     }
   return(true);
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
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)  continue;
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
   if(!InpShowLevels) return(false);
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE)) return(false);
   return(true);
  }

void DrawSetup()
  {
   if(!CanDraw()) return;
   ObjectsDeleteAll(0, OBJ_PREFIX);
   if(!g_setup.active) return;

   const datetime t1 = MathMin(g_setup.originTime, g_setup.endTime);
   const datetime t2 = t1 + (datetime)(PeriodSeconds(InpFibTF) * (InpExpireBars + InpImpulseBars));

   DrawLine("origin", t1, t2, g_setup.origin, clrDimGray,   STYLE_DOT);
   DrawLine("end",    t1, t2, g_setup.end,    clrDimGray,   STYLE_DOT);
   DrawLine("lvl1",   t1, t2, g_setup.lvl1,   clrDodgerBlue, STYLE_SOLID);
   DrawLine("lvl2",   t1, t2, g_setup.lvl2,   clrMediumPurple, STYLE_SOLID);
   DrawLine("sl",     t1, t2, g_setup.sl,     clrOrangeRed, STYLE_DASH);
   DrawLine("tp",     t1, t2, g_setup.tp,     clrForestGreen, STYLE_DASH);
  }

void DrawLine(const string tag, const datetime t1, const datetime t2,
              const double price, const color clr, const ENUM_LINE_STYLE style)
  {
   const string name = OBJ_PREFIX + tag;
   if(!ObjectCreate(0, name, OBJ_TREND, 0, t1, price, t2, price)) return;
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
  }
//+------------------------------------------------------------------+
