//+------------------------------------------------------------------+
//|                                          EA_GoldTickScalper.mq5  |
//|                                                   Quant_trading  |
//|                                                                  |
//|  1-MINUTE gold scalper. Evaluates once per completed M1 bar,     |
//|  opens one position, and closes it quickly on a profit target    |
//|  (or an optional first-profit lock).                             |
//|                                                                  |
//|  !! BACKTESTED NEGATIVE -- DEMO ONLY !!                          |
//|                                                                  |
//|  CURRENT SETTINGS = 0.20 lot, hours [0,1,7,8,12,13] (Asia +      |
//|  London + NY opens, 2h each), 60m time stop.                     |
//|                                                                  |
//|  Session choice, 0.20 lot, 128 days, measured with the BEST      |
//|  exit geometry (RR 1.5 / stop 1.3xATR), 1h time stop:            |
//|      13,14 (NY peak, 2h)       PF 0.877    -$84/day              |
//|      12,13 (NY open, 2h)       PF 0.780   -$146/day              |
//|      0,7,12 (opens, 1h ea)     PF 0.735   -$175/day              |
//|      0,1,7,8,12,13 (2h ea)     PF 0.737   -$412/day  <-- set     |
//|      all day                   PF 0.727 -$1,420/day              |
//|  Strictly monotonic: every extra hour traded costs money. The    |
//|  narrowest window is the best one.                               |
//|                                                                  |
//|  NOTE: InpRR is currently 0.50 (chosen for the >50% win rate),   |
//|  NOT the 1.5 those figures were measured at. At RR 0.50 the same |
//|  windows are materially worse -- all-day PF 0.477 (-$1,987/day), |
//|  12-16 PF 0.585 (-$336/day). For the least-bad setup use         |
//|  InpTradeHoursUtc="13,14", InpRR=1.5, InpSlAtrMult=1.3.          |
//|                                                                  |
//|  LOT WARNING: at 0.20 lot one stop-out is ~$70. A daily cap      |
//|  below ~$140 halts the EA after the first loss each day. The cap |
//|  is set to 1000 to match the 20x lot increase -- that is a $1000 |
//|  per day loss allowance. Lower it if that is not intended.       |
//|                                                                  |
//|  Other tick-replay results over real Dukascopy bid/ask:          |
//|      M1 fade,        10 days : PF 0.42   -$751                   |
//|      M1 continuation,10 days : PF 0.34   -$931                   |
//|      M1 fade 13-16 UTC, 126d : PF 0.60   -$3,143 (4,825 trades)  |
//|      288-cell search @ RR1.5 : 0 cells with WR>50% or PF>1.0     |
//|  Earlier tick-granularity version: PF 0.40, and 0 of 40 TP/SL    |
//|  geometries profitable. Still loses at a 0.02 synthetic spread.  |
//|                                                                  |
//|  WHY: median gold spread is 0.69 while the median 20s move is    |
//|  0.435 and the best measured directional edge is 0.05-0.125.     |
//|  The round trip costs more than the move being captured, so no   |
//|  entry rule, exit rule or session filter in this family clears   |
//|  breakeven. See reports/gold_tick_scalper_research.md            |
//|                                                                  |
//|  NOTE ON "3 TRADES PER MINUTE": evaluating once per M1 bar caps  |
//|  entries at ONE per minute by construction. InpMaxPerMinute is   |
//|  retained but can no longer bind above 1 in this mode.           |
//|                                                                  |
//|  Attach to a GOLD chart. Chart timeframe is irrelevant -- the EA |
//|  reads M1 explicitly.                                            |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property version   "2.00"
#property description "XAUUSD 1-minute scalper (one position, close-on-profit)"

#include <Trade/Trade.mqh>

//--- Symbol guard -----------------------------------------------------
input group             "=== Symbol guard ==="
input bool     InpGoldOnly        = true;    // Refuse to run on a non-gold chart

//--- Entry ------------------------------------------------------------
input group             "=== Entry: M1 burst ==="
input bool     InpFade            = true;    // true = fade the burst, false = follow it
input int      InpBarLookback     = 1;       // Burst measured over N completed M1 bars
input double   InpTrigAtrMult     = 0.90;    // Burst size to act on (x M1 ATR)
input double   InpStretchAtrMult  = 0.35;    // Min stretch from the EMA anchor (x M1 ATR)
input int      InpAnchorPeriodM1  = 20;      // M1 EMA anchor period
input int      InpAtrPeriodM1     = 14;      // M1 ATR period

//--- Exit -------------------------------------------------------------
input group             "=== Exit ==="
input double   InpRR              = 0.50;    // Reward:risk. TP = InpRR x stop distance.
                                             //   RR 0.50 -> ~51% win rate (measured),
                                             //   but breakeven at RR 0.50 needs 66.7%.
                                             //   Lowering RR buys win rate, NOT profit.
input double   InpSlAtrMult       = 0.90;    // Stop loss (x M1 ATR)
input double   InpSlSpreadFloor   = 1.20;    // SL floor (x spread) -- tighter = instant stop
input double   InpTpSpreadFloor   = 1.80;    // TP floor (x live spread) -- cost guard
input double   InpProfitLockUsd   = 0.0;     // >0: close the moment floating P/L >= this ($)
                                             //     measured WORSE than the plain TP
                                             //     (PF 0.27 vs 0.40) -- see report
input int      InpMaxHoldSec      = 3600;    // Time stop (seconds). 3600 = 1h, 7200 = 2h.
                                             //   1h and 2h measured near-identical.

//--- Rate limits ------------------------------------------------------
input group             "=== Rate limits ==="
input int      InpMaxPerMinute    = 3;       // Cannot bind above 1 in M1 mode
input int      InpCooldownSec     = 8;       // Min gap between entries
input bool     InpOnePositionOnly = true;    // Never hold two at once

//--- Filters ----------------------------------------------------------
input group             "=== Filters ==="
input double   InpMaxSpread       = 0.90;    // Skip if spread wider (price units)
input string   InpTradeHoursUtc   = "0,1,7,8,12,13";  // Explicit UTC hours to trade.
                                             //   Empty string -> use Start/End below.
                                             //   "0,1,7,8,12,13" = Asia+London+NY opens, 2h each
                                             //   "13,14"         = best measured (PF 0.877)
input int      InpSessStartUtc    = 12;      // Fallback start hour UTC (if hours string empty)
input int      InpSessEndUtc      = 16;      // Fallback end hour UTC, exclusive
input int      InpServerGmtOffset = 0;       // Server hours ahead of UTC (e.g. 3 for UTC+3)

//--- Money ------------------------------------------------------------
input group             "=== Money ==="
input double   InpLots            = 0.20;    // Fixed lot size
input double   InpMaxDailyLossUsd = 1000.0;  // Halt for the day past this loss (0 = off).
                                             //   MUST scale with lot: at 0.20 lot a single
                                             //   stop-out costs ~$70, so a $50 cap would
                                             //   halt after the FIRST loss every day.
input long     InpMagic           = 990117;  // Magic number

//--- Hard safety rails ------------------------------------------------
input group             "=== SAFETY RAILS (backtest is negative -- keep these on) ==="
input double   InpEquityFloorUsd  = 0.0;     // Stop trading permanently below this equity (0 = off)
input int      InpMaxTradesPerDay = 40;      // Cap entries per day (0 = off)
input int      InpMaxConsecLosses = 5;       // Stand down for the day after N losses in a row

//--- Globals ----------------------------------------------------------
CTrade         g_trade;

int            g_hAtr    = INVALID_HANDLE;
int            g_hAnchor = INVALID_HANDLE;

datetime       g_lastBar    = 0;
datetime       g_entryTimes[64];
int            g_entryCount = 0;
datetime       g_lastEntry  = 0;

datetime       g_dayStamp   = 0;
double         g_dayStartEq = 0.0;
bool           g_dayHalted  = false;

int            g_tradesToday    = 0;
int            g_consecLosses   = 0;
bool           g_deadForever    = false;
int            g_lastDealsTotal = 0;
datetime       g_lastHistCheck  = 0;

double         g_point      = 0.0;
long           g_stopsLevel = 0;

bool           g_hourMask[24];      // which UTC hours are tradeable
bool           g_useHourMask = false;

//+------------------------------------------------------------------+
//| Parse "0,1,7,8,12,13" into the hour mask. Empty -> range fallback |
//+------------------------------------------------------------------+
void BuildHourMask()
  {
   ArrayInitialize(g_hourMask, false);
   string s = InpTradeHoursUtc;
   StringTrimLeft(s);
   StringTrimRight(s);

   if(StringLen(s) == 0)
     {
      g_useHourMask = false;
      for(int h = 0; h < 24; h++)
         g_hourMask[h] = (h >= InpSessStartUtc && h < InpSessEndUtc);
      return;
     }

   string parts[];
   int k = StringSplit(s, ',', parts);
   int added = 0;
   for(int i = 0; i < k; i++)
     {
      string p = parts[i];
      StringTrimLeft(p);
      StringTrimRight(p);
      if(StringLen(p) == 0)
         continue;
      int h = (int)StringToInteger(p);
      if(h >= 0 && h < 24)
        {
         g_hourMask[h] = true;
         added++;
        }
     }
   if(added == 0)   // malformed string -> fail safe to the range
     {
      Print("EA_GoldTickScalper: InpTradeHoursUtc unparseable, falling back to Start/End.");
      for(int h = 0; h < 24; h++)
         g_hourMask[h] = (h >= InpSessStartUtc && h < InpSessEndUtc);
      g_useHourMask = false;
      return;
     }
   g_useHourMask = true;
  }

//+------------------------------------------------------------------+
bool IsGoldSymbol(const string s)
  {
   string u = s;
   StringToUpper(u);
   return (StringFind(u, "XAU") >= 0 || StringFind(u, "GOLD") >= 0);
  }

int UtcHour(const datetime serverTime)
  {
   datetime utc = serverTime - (datetime)(InpServerGmtOffset * 3600);
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return dt.hour;
  }

int EntriesLastMinute(const datetime now)
  {
   int n = 0;
   for(int i = 0; i < g_entryCount; i++)
      if(now - g_entryTimes[i] < 60)
         n++;
   return(n);
  }

void RecordEntry(const datetime now)
  {
   int w = 0;
   for(int i = 0; i < g_entryCount; i++)
      if(now - g_entryTimes[i] < 60)
        {
         g_entryTimes[w] = g_entryTimes[i];
         w++;
        }
   g_entryCount = w;
   if(g_entryCount < 64)
     {
      g_entryTimes[g_entryCount] = now;
      g_entryCount++;
     }
   g_lastEntry = now;
  }

bool HasPosition(ulong &ticket)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
        {
         ticket = t;
         return(true);
        }
     }
   return(false);
  }

double NormalizeLots(double lots)
  {
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      step = 0.01;
   lots = MathFloor(lots / step) * step;
   return(NormalizeDouble(MathMax(mn, MathMin(mx, lots)), 2));
  }

void RollDay()
  {
   MqlDateTime dt;
   datetime now = TimeCurrent();
   TimeToStruct(now, dt);
   datetime today = (datetime)(now - (dt.hour * 3600 + dt.min * 60 + dt.sec));
   if(today != g_dayStamp)
     {
      g_dayStamp     = today;
      g_dayStartEq   = AccountInfoDouble(ACCOUNT_EQUITY);
      g_dayHalted    = false;
      g_tradesToday  = 0;
      g_consecLosses = 0;
     }
  }

//+------------------------------------------------------------------+
//| Track consecutive losing closes on our magic (throttled)         |
//+------------------------------------------------------------------+
void UpdateConsecLosses()
  {
   datetime now = TimeCurrent();
   if(now - g_lastHistCheck < 5)
      return;
   g_lastHistCheck = now;

   if(!HistorySelect(g_dayStamp, now))
      return;
   int total = HistoryDealsTotal();
   if(total == g_lastDealsTotal)
      return;

   for(int i = g_lastDealsTotal; i < total; i++)
     {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0)
         continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != InpMagic)
         continue;
      if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol)
         continue;
      if(HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT)
         continue;

      double pl = HistoryDealGetDouble(t, DEAL_PROFIT)
                + HistoryDealGetDouble(t, DEAL_SWAP)
                + HistoryDealGetDouble(t, DEAL_COMMISSION);
      if(pl < 0.0)
         g_consecLosses++;
      else
         g_consecLosses = 0;
     }
   g_lastDealsTotal = total;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpGoldOnly && !IsGoldSymbol(_Symbol))
     {
      Print("EA_GoldTickScalper: refusing to run on '", _Symbol,
            "' -- attach to a GOLD chart (XAUUSD / XAUUSDs).");
      return(INIT_FAILED);
     }

   g_point      = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);

   g_hAtr    = iATR(_Symbol, PERIOD_M1, InpAtrPeriodM1);
   g_hAnchor = iMA(_Symbol, PERIOD_M1, InpAnchorPeriodM1, 0, MODE_EMA, PRICE_CLOSE);
   if(g_hAtr == INVALID_HANDLE || g_hAnchor == INVALID_HANDLE)
     {
      Print("EA_GoldTickScalper: indicator handle failed");
      return(INIT_FAILED);
     }

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetDeviationInPoints(20);

   g_entryCount = 0;
   g_dayStamp   = 0;
   BuildHourMask();

   PrintFormat("EA_GoldTickScalper v2 (1-MINUTE mode) on %s | %s | RR %.2f | lots %.2f",
               _Symbol, (InpFade ? "fade" : "follow"), InpRR, InpLots);

   //--- CHECK THIS LINE ON EVERY ATTACH.
   //    If the derived UTC hour does not match real UTC, InpServerGmtOffset is
   //    wrong and the session filter is trading the wrong hours entirely.
   datetime srv = TimeCurrent();
   PrintFormat("  server time %s | InpServerGmtOffset %+d -> derived UTC hour %d",
               TimeToString(srv, TIME_DATE | TIME_MINUTES), InpServerGmtOffset,
               UtcHour(srv));
   string hrs = "";
   for(int h = 0; h < 24; h++)
      if(g_hourMask[h])
         hrs += (StringLen(hrs) ? "," : "") + IntegerToString(h);
   PrintFormat("  trading UTC hours [%s] (%s) | time stop %ds | max %d trades/day",
               hrs, (g_useHourMask ? "explicit list" : "start/end range"),
               InpMaxHoldSec, InpMaxTradesPerDay);
   PrintFormat("  equity floor %.2f | daily loss cap %.2f", InpEquityFloorUsd,
               InpMaxDailyLossUsd);

   //--- a cap smaller than one stop-out silences the EA after the first loss
   double approxStop = 3.5;                       // typical M1 gold stop, points
   double oneLoss = approxStop * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE)
                    * InpLots;
   if(InpMaxDailyLossUsd > 0.0 && InpMaxDailyLossUsd < 2.0 * oneLoss)
      PrintFormat("  WARNING: daily cap %.2f is less than 2 stop-outs (~%.2f each) "
                  "at %.2f lot -- the EA will halt almost immediately each day.",
                  InpMaxDailyLossUsd, oneLoss, InpLots);
   if(InpEquityFloorUsd <= 0.0)
      Print("  WARNING: InpEquityFloorUsd is 0 -- no hard floor is armed.");
   Print("  Backtested NEGATIVE. All-day/all-session is the worst setting tested.");
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_hAtr    != INVALID_HANDLE) IndicatorRelease(g_hAtr);
   if(g_hAnchor != INVALID_HANDLE) IndicatorRelease(g_hAnchor);
  }

//+------------------------------------------------------------------+
//| Profit lock + time stop (SL/TP sit broker-side)                  |
//+------------------------------------------------------------------+
void ManageOpen(const ulong ticket)
  {
   if(!PositionSelectByTicket(ticket))
      return;

   if(InpProfitLockUsd > 0.0)
     {
      double pl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(pl >= InpProfitLockUsd)
        {
         g_trade.PositionClose(ticket);
         return;
        }
     }

   if(InpMaxHoldSec > 0)
     {
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      if(TimeCurrent() - opened >= InpMaxHoldSec)
         g_trade.PositionClose(ticket);
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   RollDay();
   UpdateConsecLosses();

   //--- equity floor (permanent)
   if(InpEquityFloorUsd > 0.0 && !g_deadForever)
     {
      if(AccountInfoDouble(ACCOUNT_EQUITY) <= InpEquityFloorUsd)
        {
         g_deadForever = true;
         PrintFormat("EA_GoldTickScalper: EQUITY FLOOR %.2f breached -- trading disabled.",
                     InpEquityFloorUsd);
        }
     }

   //--- consecutive-loss breaker
   if(InpMaxConsecLosses > 0 && g_consecLosses >= InpMaxConsecLosses && !g_dayHalted)
     {
      g_dayHalted = true;
      PrintFormat("EA_GoldTickScalper: %d consecutive losses -- standing down for the day.",
                  g_consecLosses);
     }

   //--- daily loss guard
   if(InpMaxDailyLossUsd > 0.0 && !g_dayHalted)
     {
      if(g_dayStartEq - AccountInfoDouble(ACCOUNT_EQUITY) >= InpMaxDailyLossUsd)
        {
         g_dayHalted = true;
         Print("EA_GoldTickScalper: daily loss cap hit, standing down for the day.");
        }
     }

   //--- manage the open position on EVERY tick (fast exits still matter)
   ulong ticket = 0;
   if(HasPosition(ticket))
     {
      ManageOpen(ticket);
      if(InpOnePositionOnly)
         return;
     }

   if(g_dayHalted || g_deadForever)
      return;
   if(InpMaxTradesPerDay > 0 && g_tradesToday >= InpMaxTradesPerDay)
      return;

   //--- ENTRIES ARE EVALUATED ONCE PER COMPLETED M1 BAR
   datetime barTime = (datetime)SeriesInfoInteger(_Symbol, PERIOD_M1, SERIES_LASTBAR_DATE);
   if(barTime == g_lastBar)
      return;
   g_lastBar = barTime;

   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk))
      return;
   if(tk.bid <= 0.0 || tk.ask <= 0.0)
      return;
   double spread = tk.ask - tk.bid;
   if(spread > InpMaxSpread)
      return;

   int hh = UtcHour(tk.time);
   if(hh < 0 || hh > 23 || !g_hourMask[hh])
      return;

   if(InpCooldownSec > 0 && (tk.time - g_lastEntry) < InpCooldownSec)
      return;
   if(EntriesLastMinute(tk.time) >= InpMaxPerMinute)
      return;

   //--- M1 context off completed bars only (shift 1 = last closed)
   double a[], m[];
   if(CopyBuffer(g_hAtr,    0, 1, 1, a) != 1) return;
   if(CopyBuffer(g_hAnchor, 0, 1, 1, m) != 1) return;
   double atr = a[0], anchor = m[0];
   if(atr <= 0.0)
      return;

   int need = InpBarLookback + 2;
   double cl[];
   ArraySetAsSeries(cl, true);
   if(CopyClose(_Symbol, PERIOD_M1, 1, need, cl) != need)
      return;

   //--- burst across the last completed bar(s); cl[0] = last closed bar
   double velFull = cl[0] - cl[InpBarLookback];
   double stretch = anchor - cl[0];

   double trig     = InpTrigAtrMult * atr;
   double stretchN = InpStretchAtrMult * atr;

   bool buy, sell;
   if(InpFade)
     {
      buy  = (velFull <= -trig) && (stretch >=  stretchN);
      sell = (velFull >=  trig) && (stretch <= -stretchN);
     }
   else
     {
      buy  = (velFull >=  trig) && (stretch <= -stretchN);
      sell = (velFull <= -trig) && (stretch >=  stretchN);
     }

   if(!buy && !sell)
      return;

   //--- stop first, then target as RR x stop (matches the tested geometry).
   //    A stop tighter than the spread is filled the instant it opens, because a
   //    long is entered at ask but stopped on bid -- already one spread below.
   double slDist = MathMax(InpSlAtrMult * atr, InpSlSpreadFloor * spread);
   double tpDist = MathMax(InpRR * slDist, InpTpSpreadFloor * spread);

   double minStop = (double)g_stopsLevel * g_point;
   if(minStop > 0.0)
     {
      tpDist = MathMax(tpDist, minStop);
      slDist = MathMax(slDist, minStop);
     }

   double lots = NormalizeLots(InpLots);
   int    dg   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   bool ok;
   if(buy)
     {
      double px = tk.ask;
      ok = g_trade.Buy(lots, _Symbol, 0.0,
                       NormalizeDouble(px - slDist, dg),
                       NormalizeDouble(px + tpDist, dg), "m1scalp");
     }
   else
     {
      double px = tk.bid;
      ok = g_trade.Sell(lots, _Symbol, 0.0,
                        NormalizeDouble(px + slDist, dg),
                        NormalizeDouble(px - tpDist, dg), "m1scalp");
     }

   if(ok)
     {
      RecordEntry(tk.time);
      g_tradesToday++;
     }
   else
      PrintFormat("EA_GoldTickScalper: order failed ret=%d %s",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
  }
//+------------------------------------------------------------------+
