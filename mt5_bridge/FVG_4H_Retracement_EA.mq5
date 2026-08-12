//+------------------------------------------------------------------+
//|                                      FVG_4H_Retracement_EA.mq5   |
//|                    MQL5 port of "4H FVG Retracement Strategy"    |
//+------------------------------------------------------------------+
//  RULE (as specified):
//    3-candle Fair Value Gap on the 4H timeframe.
//      Bullish FVG : low(candle3)  > high(candle1)
//      Bearish FVG : high(candle3) < low(candle1)
//
//    LONG  : 4H trend bullish, bullish FVG forms, price retraces DOWN into it.
//            Entry = TOP boundary of the gap    = low(candle3)
//            SL    = low(candle1)
//            TP    = entry + RR * (entry - SL)          (RR = 2 by default)
//
//    SHORT : 4H trend bearish, bearish FVG forms, price rallies UP into it.
//            Entry = BOTTOM boundary of the gap = high(candle3)
//            SL    = high(candle1)
//            TP    = entry - RR * (SL - entry)
//
//    Trend  : EMA(fast) vs EMA(slow) on the FVG timeframe.
//    Expiry : a gap is dropped after InpExpireBars FVG-TF candles.
//    One position at a time (pyramiding 0), one pending order per side.
//
//  ENTRY MECHANICS
//    The retracement entry is a resting BUY LIMIT / SELL LIMIT at the gap
//    boundary, so the fill price is exactly the planned entry and the 1:RR
//    geometry is exact. If price is already through the level when the order
//    would be placed, the EA enters at market instead (InpMarketFallback) but
//    keeps the ORIGINAL SL/TP levels, so the plan is unchanged.
//
//  TWO DIFFERENCES FROM THE PINE SCRIPT — read before comparing results
//    1. DETECTION TIMING. The Pine script evaluates the FVG on the FIRST chart
//       bar of each new 4H candle, using request.security(...,"240",high/low)
//       for the still-forming candle. That makes "candle 3" only the first
//       chart bar of the 4H candle, so the result depends on the chart
//       timeframe and it detects gaps that candle 3 later closes.
//       This EA evaluates on the three CLOSED 4H candles, which is the plain
//       3-candle definition and is timeframe-independent.
//       Set InpPineCompat = true to reproduce the Pine timing bar-for-bar
//       (requires the chart TF to be lower than the FVG TF).
//    2. EXPIRY UNITS. The Pine input is labelled "FVG Expiry (HTF candles)"
//       but the code compares chart bar_index, so on a 15m chart 50 "HTF
//       candles" is really 12.5 hours. This EA counts FVG-TF candles, i.e.
//       what the label says.
//
//  Also deliberate: when a position opens, any leftover pending order on the
//  other side is cancelled. Pine would let it fill and REVERSE the position.
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property version   "1.00"
#property description "4H FVG retracement: limit entry at the gap boundary, SL at the first candle, fixed R:R target."

//==================================================================
//  INPUTS
//==================================================================
enum ENUM_LOT_MODE
  {
   LOT_RISK_PERCENT = 0,   // Risk % of balance per trade
   LOT_FIXED        = 1    // Fixed lot
  };

input group           "=== Fair Value Gap ==="
input ENUM_TIMEFRAMES InpFvgTF          = PERIOD_H4;   // FVG timeframe
input int             InpExpireBars     = 50;          // FVG expiry (FVG-TF candles)
input double          InpRR             = 2.0;         // Risk : Reward

input group           "=== Trend filter (on FVG timeframe) ==="
input bool            InpUseTrendFilter = true;        // Use trend filter
input int             InpFastEMA        = 50;          // Fast EMA
input int             InpSlowEMA        = 200;         // Slow EMA

input group           "=== Execution ==="
input bool            InpPineCompat     = false;       // Pine-compatible bar timing
input bool            InpMarketFallback = true;        // Market entry if price already past the level
input int             InpPendingExpire  = 0;           // Cancel unfilled limit after N FVG-TF candles (0=never)
input int             InpSlippage       = 20;          // Max deviation (points, market fallback only)

input group           "=== Money management ==="
input ENUM_LOT_MODE   InpLotMode        = LOT_FIXED;
input double          InpFixedLot       = 0.20;        // Fixed lot (LOT_FIXED mode)
input double          InpRiskPercent    = 1.0;         // Risk per trade (% of balance, LOT_RISK_PERCENT mode)
input double          InpMaxLot         = 10.0;        // Lot cap

input group           "=== Misc ==="
input long            InpMagic          = 20260812;    // Magic number
input string          InpComment        = "FVG4H";     // Order comment
input bool            InpShowZones      = true;        // Draw FVG zones
input bool            InpVerbose        = true;        // Log decisions

//==================================================================
//  STATE
//==================================================================
struct FvgZone
  {
   bool     active;
   bool     bullish;
   double   top;        // upper boundary of the gap
   double   bottom;     // lower boundary of the gap
   double   sl;         // extreme of candle 1
   datetime created;    // open time of the FVG-TF candle it was detected on
  };

FvgZone   g_bull;
FvgZone   g_bear;

int       g_hFast          = INVALID_HANDLE;
int       g_hSlow          = INVALID_HANDLE;
datetime  g_lastFvgBar     = 0;
datetime  g_lastChartBar   = 0;
datetime  g_pendingZoneBuy = 0;   // creation time of the zone behind the resting BUY LIMIT
datetime  g_pendingZoneSell= 0;
const string OBJ_PREFIX    = "FVG4H_";

//==================================================================
//  INIT / DEINIT
//==================================================================
int OnInit()
  {
   if(InpRR <= 0.0)
     {
      Print("[FVG4H] Risk:Reward must be > 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpExpireBars < 1)
     {
      Print("[FVG4H] FVG expiry must be >= 1");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpUseTrendFilter && InpFastEMA >= InpSlowEMA)
      Print("[FVG4H] WARNING: fast EMA (", InpFastEMA, ") >= slow EMA (", InpSlowEMA, ")");

   if(InpPineCompat && PeriodSeconds(PERIOD_CURRENT) >= PeriodSeconds(InpFvgTF))
     {
      Print("[FVG4H] Pine-compatible mode needs a chart timeframe LOWER than the FVG timeframe.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   if(InpUseTrendFilter)
     {
      g_hFast = iMA(_Symbol, InpFvgTF, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
      g_hSlow = iMA(_Symbol, InpFvgTF, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
      if(g_hFast == INVALID_HANDLE || g_hSlow == INVALID_HANDLE)
        {
         Print("[FVG4H] Failed to create EMA handles, err=", GetLastError());
         return(INIT_FAILED);
        }
     }

   ZeroMemory(g_bull);
   ZeroMemory(g_bear);

   PrintFormat("[FVG4H] init  symbol=%s chartTF=%s fvgTF=%s RR=%.2f trendFilter=%s pineCompat=%s",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)Period()), EnumToString(InpFvgTF),
               InpRR, (InpUseTrendFilter ? "on" : "off"), (InpPineCompat ? "on" : "off"));
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_hFast != INVALID_HANDLE) IndicatorRelease(g_hFast);
   if(g_hSlow != INVALID_HANDLE) IndicatorRelease(g_hSlow);
   ObjectsDeleteAll(0, OBJ_PREFIX);
  }

//==================================================================
//  MAIN LOOP
//==================================================================
void OnTick()
  {
   // A position of ours is open -> no leftover pendings may survive to reverse it.
   if(CountOurPositions() > 0)
     {
      CancelAllPendings();
      return;
     }

   const datetime fvgBar = iTime(_Symbol, InpFvgTF, 0);
   if(fvgBar > 0 && fvgBar != g_lastFvgBar)
     {
      g_lastFvgBar = fvgBar;
      OnNewFvgBar();
     }

   if(InpPineCompat)
     {
      const datetime chartBar = iTime(_Symbol, PERIOD_CURRENT, 0);
      if(chartBar > 0 && chartBar != g_lastChartBar)
        {
         g_lastChartBar = chartBar;
         OnNewChartBar();
        }
     }
   else
     {
      // Standard mode: the resting limit order IS the "wait for the retracement",
      // so a zone is armed as soon as it exists and we are flat.
      TryArm(g_bull);
      TryArm(g_bear);
     }
  }

//------------------------------------------------------------------
//  New candle on the FVG timeframe
//------------------------------------------------------------------
void OnNewFvgBar()
  {
   ExpireZones();
   ExpirePendings();
   if(!InpPineCompat)
      DetectStandard();
  }

//------------------------------------------------------------------
//  New candle on the chart timeframe (Pine-compatible mode only)
//------------------------------------------------------------------
void OnNewChartBar()
  {
   // The bar that just closed.
   const datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   if(barTime <= 0) return;

   DetectPine(barTime);

   const double barHigh = iHigh(_Symbol, PERIOD_CURRENT, 1);
   const double barLow  = iLow(_Symbol, PERIOD_CURRENT, 1);
   if(barHigh <= 0.0 || barLow <= 0.0) return;

   // Pine: longTouch = low <= bullTop and high >= bullBottom  (bar range overlaps the gap)
   if(g_bull.active && barLow <= g_bull.top && barHigh >= g_bull.bottom)
      TryArm(g_bull);

   if(g_bear.active && barHigh >= g_bear.bottom && barLow <= g_bear.top)
      TryArm(g_bear);
  }

//==================================================================
//  DETECTION
//==================================================================
//  Standard: the three candles that just closed.
//    shift 3 = candle 1 (oldest)   shift 2 = candle 2   shift 1 = candle 3
//------------------------------------------------------------------
void DetectStandard()
  {
   if(iBars(_Symbol, InpFvgTF) < 4) return;

   const double h1 = iHigh(_Symbol, InpFvgTF, 3);
   const double l1 = iLow (_Symbol, InpFvgTF, 3);
   const double h3 = iHigh(_Symbol, InpFvgTF, 1);
   const double l3 = iLow (_Symbol, InpFvgTF, 1);
   if(h1 <= 0.0 || l1 <= 0.0 || h3 <= 0.0 || l3 <= 0.0) return;

   const datetime created = iTime(_Symbol, InpFvgTF, 0);

   if(l3 > h1)                                   // bullish gap between candle 1 and candle 3
      SetZone(g_bull, true, l3, h1, l1, created);

   if(h3 < l1)                                   // bearish gap
      SetZone(g_bear, false, l1, h3, h1, created);
  }

//------------------------------------------------------------------
//  Pine-compatible: fires on the first chart bar of a new FVG-TF candle.
//  "candle 3" is the still-forming FVG-TF candle, whose high/low at that
//  moment are simply the high/low of that first chart bar.
//------------------------------------------------------------------
void DetectPine(const datetime barTime)
  {
   const int shiftNow  = iBarShift(_Symbol, InpFvgTF, barTime, false);
   const int shiftPrev = iBarShift(_Symbol, InpFvgTF, iTime(_Symbol, PERIOD_CURRENT, 2), false);
   if(shiftNow < 0 || shiftPrev < 0) return;
   if(shiftNow == shiftPrev) return;             // not the first chart bar of a new FVG-TF candle

   const int idx1 = shiftNow + 2;                // candle 1 = two closed candles back
   if(iBars(_Symbol, InpFvgTF) <= idx1) return;

   const double h1 = iHigh(_Symbol, InpFvgTF, idx1);
   const double l1 = iLow (_Symbol, InpFvgTF, idx1);
   const double h3 = iHigh(_Symbol, PERIOD_CURRENT, 1);
   const double l3 = iLow (_Symbol, PERIOD_CURRENT, 1);
   if(h1 <= 0.0 || l1 <= 0.0 || h3 <= 0.0 || l3 <= 0.0) return;

   const datetime created = iTime(_Symbol, InpFvgTF, shiftNow);

   if(l3 > h1)
      SetZone(g_bull, true, l3, h1, l1, created);

   if(h3 < l1)
      SetZone(g_bear, false, l1, h3, h1, created);
  }

//------------------------------------------------------------------
void SetZone(FvgZone &z, const bool bullish,
             const double top, const double bottom, const double sl,
             const datetime created)
  {
   z.active  = true;
   z.bullish = bullish;
   z.top     = top;
   z.bottom  = bottom;
   z.sl      = sl;
   z.created = created;

   if(InpVerbose)
      PrintFormat("[FVG4H] %s FVG  zone=[%s .. %s]  SL=%s",
                  (bullish ? "BULL" : "BEAR"),
                  DoubleToString(bottom, _Digits), DoubleToString(top, _Digits),
                  DoubleToString(sl, _Digits));

   DrawZone(z);
  }

//------------------------------------------------------------------
void ExpireZones()
  {
   if(g_bull.active && ZoneAge(g_bull) > InpExpireBars)
     {
      if(InpVerbose) Print("[FVG4H] bullish FVG expired");
      g_bull.active = false;
     }
   if(g_bear.active && ZoneAge(g_bear) > InpExpireBars)
     {
      if(InpVerbose) Print("[FVG4H] bearish FVG expired");
      g_bear.active = false;
     }
  }

int ZoneAge(const FvgZone &z)
  {
   const int shift = iBarShift(_Symbol, InpFvgTF, z.created, false);
   return(shift < 0 ? 0 : shift);
  }

//==================================================================
//  ARMING / ORDER PLACEMENT
//==================================================================
void TryArm(FvgZone &z)
  {
   if(!z.active) return;
   if(CountOurPositions() > 0) return;

   if(InpUseTrendFilter)
     {
      int dir = TrendDirection();
      if(dir == 0) return;
      if(z.bullish  && dir < 0) return;
      if(!z.bullish && dir > 0) return;
     }

   const double entry = z.bullish ? z.top : z.bottom;
   const double sl    = z.sl;
   const double risk  = z.bullish ? (entry - sl) : (sl - entry);
   if(risk <= 0.0) return;                       // Pine: longValidSL / shortValidSL

   const double tp = z.bullish ? (entry + risk * InpRR) : (entry - risk * InpRR);

   if(PlaceEntry(z.bullish, entry, sl, tp, z.created))
      z.active = false;                          // Pine nulls the gap once an order is sent
  }

//------------------------------------------------------------------
bool PlaceEntry(const bool isLong, const double rawEntry, const double rawSL,
                const double rawTP, const datetime zoneTime)
  {
   const double entry = NormPrice(rawEntry);
   const double sl    = NormPrice(rawSL);
   const double tp    = NormPrice(rawTP);

   const double lot = CalcLot(entry, sl);
   if(lot <= 0.0)
     {
      if(InpVerbose) Print("[FVG4H] lot resolved to 0 - entry skipped");
      return(false);
     }

   const double point   = _Point;
   const long   stopLvl = (long)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   const double minDist = stopLvl * point;
   const double ask     = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double bid     = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0.0 || bid <= 0.0) return(false);

   // SL/TP must clear the broker's stop level relative to the entry.
   if(MathAbs(entry - sl) <= minDist || MathAbs(tp - entry) <= minDist)
     {
      if(InpVerbose)
         PrintFormat("[FVG4H] gap too small for broker stop level (%d pts) - skipped", (int)stopLvl);
      return(false);
     }

   // Replace any resting order on the same side (Pine: same-ID strategy.entry replaces).
   const ENUM_ORDER_TYPE limitType = isLong ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;
   const ulong oldTicket = FindPending(limitType);
   if(oldTicket > 0) DeletePending(oldTicket);

   const bool priceThrough = isLong ? (ask - entry < minDist) : (entry - bid < minDist);

   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req);
   ZeroMemory(res);

   req.symbol    = _Symbol;
   req.volume    = lot;
   req.sl        = sl;
   req.tp        = tp;
   req.magic     = InpMagic;
   req.comment   = InpComment;
   req.deviation = InpSlippage;

   if(priceThrough)
     {
      if(!InpMarketFallback)
        {
         if(InpVerbose) Print("[FVG4H] price already past the level and market fallback is off - skipped");
         return(false);
        }
      // Market entry, but keep the PLANNED stop and target levels.
      const double mktPrice = isLong ? ask : bid;
      if(MathAbs(mktPrice - sl) <= minDist || MathAbs(tp - mktPrice) <= minDist)
        {
         if(InpVerbose) Print("[FVG4H] market fallback violates stop level - skipped");
         return(false);
        }
      req.action = TRADE_ACTION_DEAL;
      req.type   = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      req.price  = NormPrice(mktPrice);
     }
   else
     {
      req.action    = TRADE_ACTION_PENDING;
      req.type      = limitType;
      req.price     = entry;
      req.type_time = ORDER_TIME_GTC;
     }

   if(!SendRequest(req, res))
     {
      PrintFormat("[FVG4H] order rejected retcode=%d (%s)", res.retcode, res.comment);
      return(false);
     }

   if(isLong) g_pendingZoneBuy  = zoneTime;
   else       g_pendingZoneSell = zoneTime;

   PrintFormat("[FVG4H] %s %s  lot=%.2f entry=%s SL=%s TP=%s  (R=%s, RR=%.2f)",
               (priceThrough ? "MARKET" : "LIMIT"),
               (isLong ? "BUY" : "SELL"), lot,
               DoubleToString(req.price, _Digits),
               DoubleToString(sl, _Digits),
               DoubleToString(tp, _Digits),
               DoubleToString(MathAbs(entry - sl), _Digits), InpRR);
   return(true);
  }

//==================================================================
//  PENDING ORDER HOUSEKEEPING
//==================================================================
void ExpirePendings()
  {
   if(InpPendingExpire <= 0) return;

   ulong t = FindPending(ORDER_TYPE_BUY_LIMIT);
   if(t > 0 && g_pendingZoneBuy > 0)
     {
      const int age = iBarShift(_Symbol, InpFvgTF, g_pendingZoneBuy, false);
      if(age > InpPendingExpire) { DeletePending(t); g_pendingZoneBuy = 0; }
     }

   t = FindPending(ORDER_TYPE_SELL_LIMIT);
   if(t > 0 && g_pendingZoneSell > 0)
     {
      const int age = iBarShift(_Symbol, InpFvgTF, g_pendingZoneSell, false);
      if(age > InpPendingExpire) { DeletePending(t); g_pendingZoneSell = 0; }
     }
  }

void CancelAllPendings()
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      DeletePending(ticket);
     }
   g_pendingZoneBuy  = 0;
   g_pendingZoneSell = 0;
  }

ulong FindPending(const ENUM_ORDER_TYPE type)
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      const ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE) != type) continue;
      return(ticket);
     }
   return(0);
  }

bool DeletePending(const ulong ticket)
  {
   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req);
   ZeroMemory(res);
   req.action = TRADE_ACTION_REMOVE;
   req.order  = ticket;
   if(!OrderSend(req, res) || (res.retcode != TRADE_RETCODE_DONE && res.retcode != TRADE_RETCODE_PLACED))
     {
      PrintFormat("[FVG4H] failed to delete order #%I64u retcode=%d", ticket, res.retcode);
      return(false);
     }
   return(true);
  }

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

//==================================================================
//  TREND
//==================================================================
//  Returns +1 bullish, -1 bearish, 0 unknown.
//  Reads shift 0 (the forming FVG-TF candle) to match the Pine script.
//------------------------------------------------------------------
int TrendDirection()
  {
   if(!InpUseTrendFilter) return(0);

   double fast[1], slow[1];
   if(CopyBuffer(g_hFast, 0, 0, 1, fast) != 1) return(0);
   if(CopyBuffer(g_hSlow, 0, 0, 1, slow) != 1) return(0);
   if(fast[0] == slow[0]) return(0);
   return(fast[0] > slow[0] ? 1 : -1);
  }

//==================================================================
//  SIZING / PRICE HELPERS
//==================================================================
double CalcLot(const double entry, const double sl)
  {
   const double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   const double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   const double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double lot;
   if(InpLotMode == LOT_FIXED)
     {
      lot = InpFixedLot;
     }
   else
     {
      const double dist = MathAbs(entry - sl);
      const double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      const double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(dist <= 0.0 || tickValue <= 0.0 || tickSize <= 0.0) return(0.0);

      const double lossPerLot = (dist / tickSize) * tickValue;
      if(lossPerLot <= 0.0) return(0.0);

      const double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
      lot = riskMoney / lossPerLot;
     }

   if(lotStep > 0.0) lot = MathFloor(lot / lotStep) * lotStep;
   if(InpMaxLot > 0.0) lot = MathMin(lot, InpMaxLot);
   lot = MathMin(lot, maxLot);
   if(lot < minLot) lot = (InpLotMode == LOT_FIXED ? minLot : 0.0);

   // Kill float dust introduced by the step rounding.
   const int lotDigits = (lotStep >= 1.0 ? 0 : (lotStep >= 0.1 ? 1 : 2));
   return(NormalizeDouble(lot, lotDigits));
  }

double NormPrice(const double price)
  {
   const double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double p = price;
   if(tickSize > 0.0) p = MathRound(price / tickSize) * tickSize;
   return(NormalizeDouble(p, _Digits));
  }

//------------------------------------------------------------------
//  OrderSend with a filling-mode retry (brokers differ on what they accept).
//------------------------------------------------------------------
bool SendRequest(MqlTradeRequest &req, MqlTradeResult &res)
  {
   ENUM_ORDER_TYPE_FILLING order[3];
   int n = 0;
   const long modes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);

   if(req.action == TRADE_ACTION_PENDING)
     {
      order[n++] = ORDER_FILLING_RETURN;
      if((modes & SYMBOL_FILLING_IOC) != 0) order[n++] = ORDER_FILLING_IOC;
      if((modes & SYMBOL_FILLING_FOK) != 0) order[n++] = ORDER_FILLING_FOK;
     }
   else
     {
      if((modes & SYMBOL_FILLING_FOK) != 0) order[n++] = ORDER_FILLING_FOK;
      if((modes & SYMBOL_FILLING_IOC) != 0) order[n++] = ORDER_FILLING_IOC;
      order[n++] = ORDER_FILLING_RETURN;
     }

   for(int i = 0; i < n; i++)
     {
      req.type_filling = order[i];
      ZeroMemory(res);
      const bool ok = OrderSend(req, res);
      if(ok && (res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_PLACED))
         return(true);
      if(res.retcode != TRADE_RETCODE_INVALID_FILL)
         return(false);
     }
   return(false);
  }

//==================================================================
//  DRAWING
//==================================================================
void DrawZone(const FvgZone &z)
  {
   if(!InpShowZones) return;
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE)) return;

   const string name = OBJ_PREFIX + (z.bullish ? "B_" : "S_") + IntegerToString((long)z.created);
   const datetime t2 = z.created + (datetime)(PeriodSeconds(InpFvgTF) * InpExpireBars);

   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, z.created, z.top, t2, z.bottom);
   else
     {
      ObjectSetDouble(0, name, OBJPROP_PRICE, 0, z.top);
      ObjectSetDouble(0, name, OBJPROP_PRICE, 1, z.bottom);
      ObjectSetInteger(0, name, OBJPROP_TIME, 0, z.created);
      ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
     }

   ObjectSetInteger(0, name, OBJPROP_COLOR, z.bullish ? clrSeaGreen : clrFireBrick);
   ObjectSetInteger(0, name, OBJPROP_FILL, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }
//+------------------------------------------------------------------+
