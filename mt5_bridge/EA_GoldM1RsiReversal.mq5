//+------------------------------------------------------------------+
//|                                      EA_GoldM1RsiReversal.mq5    |
//|                                                   Quant_trading  |
//|                                                                  |
//|  1-minute XAUUSD scalper: Cardwell RSI positive/negative reversal |
//|  restricted to the 13:00-16:00 UTC window.                       |
//|                                                                  |
//|  !! FAILED OUT-OF-SAMPLE VALIDATION -- DEMO ONLY, DO NOT TRADE !! |
//|                                                                  |
//|  On the 56 days it was selected from (May-Jul 2026) this looked   |
//|  strong: PF 1.67, and 12/12 nearby parameter cells positive in    |
//|  both halves. It did NOT survive fresh data:                     |
//|      May-Jul 2026 (selection sample) : PF 1.68  win 51%          |
//|      Jan-Apr 2026 (true holdout)     : PF 0.39  win 22%          |
//|      Full Jan-Jul 2026, 127 trades   : PF 1.07  +0.003R/trade    |
//|  Over the full period it is exactly breakeven -- i.e. nothing.    |
//|  On the holdout NO session window worked (12-16: 0.58, 13-16:     |
//|  0.46, 14-17: 0.77, all-day: 0.69), so the 13-16 UTC window was   |
//|  never a structural feature, just the best-looking slice of one   |
//|  56-day sample.                                                  |
//|                                                                  |
//|  Cause of the false positive: the session window was chosen using |
//|  all 56 days, then "validated" on an IS/OOS split of those SAME   |
//|  days. The selection contaminated both halves, so the split could |
//|  not detect it. A plateau measured on a contaminated sample is    |
//|  still contaminated.                                             |
//|                                                                  |
//|  Kept as a correct reference implementation of the rules, not as  |
//|  a tradeable system. See reports/rsi_reversal_m1_research.md      |
//|                                                                  |
//|  Attach to a GOLD chart. Timeframe of the chart does not matter; |
//|  the EA reads M1 explicitly.                                     |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property version   "1.00"
#property description "XAUUSD M1 RSI reversal scalper, 13-16 UTC window"

#include <Trade/Trade.mqh>

//--- Symbol guard -----------------------------------------------------
input group             "=== Symbol guard ==="
input bool     InpGoldOnly       = true;   // Refuse to run on a non-gold chart

//--- Session ----------------------------------------------------------
input group             "=== Session (THIS IS THE EDGE) ==="
input int      InpSessStartUtc   = 13;     // Start hour UTC (validated 13)
input int      InpSessEndUtc     = 16;     // End hour UTC, exclusive (validated 16)
input int      InpServerGmtOffset= 0;      // Server hours ahead of UTC (e.g. 3 for UTC+3)

//--- Signal -----------------------------------------------------------
input group             "=== Signal ==="
input int      InpRsiPeriod      = 14;     // RSI period
input int      InpEmaFast        = 9;      // Fast EMA (trend)
input int      InpEmaSlow        = 21;     // Slow EMA (trend)
input int      InpPivotK         = 2;      // Bars each side for a confirmed swing
input double   InpRsiFloor       = 40.0;   // Longs: RSI at setup lows must be >= this
input double   InpRsiCeil        = 60.0;   // Shorts: RSI at setup highs must be <= this
input int      InpTriggerWindow  = 6;      // Bars after setup for the trigger to fire

//--- Risk -------------------------------------------------------------
input group             "=== Risk ==="
input double   InpRR             = 1.5;    // Reward:risk (validated 1.5)
input double   InpSlBuffer       = 0.10;   // Buffer beyond the swing (price units)
input int      InpTimeStopBars   = 60;     // Exit after N M1 bars (validated 60)
input double   InpMinRisk        = 0.30;   // Skip if stop is closer than this
input double   InpMaxRisk        = 6.00;   // Skip if stop is wider than this
input double   InpMaxSpread      = 0.90;   // Skip if spread wider than this

//--- Money ------------------------------------------------------------
input group             "=== Money ==="
input double   InpLots           = 0.01;   // Fixed lot size
input double   InpMaxDailyLossUsd= 50.0;   // Stand down for the day past this (0 = off)
input long     InpMagic          = 990213; // Magic number

//--- Globals ----------------------------------------------------------
CTrade    g_trade;
int       g_hRsi  = INVALID_HANDLE;
int       g_hFast = INVALID_HANDLE;
int       g_hSlow = INVALID_HANDLE;

datetime  g_lastBar   = 0;
datetime  g_lockoutTo = 0;   // no new entry until here (matches backtest re-arm rule)
datetime  g_dayStamp  = 0;
double    g_dayStartEq= 0.0;
bool      g_dayHalted = false;

#define LOOKBACK 300

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
      g_dayStamp   = today;
      g_dayStartEq = AccountInfoDouble(ACCOUNT_EQUITY);
      g_dayHalted  = false;
     }
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpGoldOnly && !IsGoldSymbol(_Symbol))
     {
      Print("EA_GoldM1RsiReversal: refusing to run on '", _Symbol,
            "' -- attach to a GOLD chart (XAUUSD / XAUUSDs).");
      return(INIT_FAILED);
     }

   g_hRsi  = iRSI(_Symbol, PERIOD_M1, InpRsiPeriod, PRICE_CLOSE);
   g_hFast = iMA(_Symbol, PERIOD_M1, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
   g_hSlow = iMA(_Symbol, PERIOD_M1, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);

   if(g_hRsi == INVALID_HANDLE || g_hFast == INVALID_HANDLE || g_hSlow == INVALID_HANDLE)
     {
      Print("EA_GoldM1RsiReversal: indicator handle failed");
      return(INIT_FAILED);
     }

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetDeviationInPoints(20);

   PrintFormat("EA_GoldM1RsiReversal on %s | session %02d-%02d UTC (server offset %+d) | "
               "RR 1:%.1f | time stop %d bars | lots %.2f",
               _Symbol, InpSessStartUtc, InpSessEndUtc, InpServerGmtOffset,
               InpRR, InpTimeStopBars, InpLots);
   PrintFormat("  server time now = %s  -> UTC hour %d",
               TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES), UtcHour(TimeCurrent()));
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   if(g_hRsi  != INVALID_HANDLE) IndicatorRelease(g_hRsi);
   if(g_hFast != INVALID_HANDLE) IndicatorRelease(g_hFast);
   if(g_hSlow != INVALID_HANDLE) IndicatorRelease(g_hSlow);
  }

//+------------------------------------------------------------------+
//| Time stop for the open position (SL/TP sit broker-side)          |
//+------------------------------------------------------------------+
void ManageOpen(const ulong ticket)
  {
   if(!PositionSelectByTicket(ticket))
      return;
   if(InpTimeStopBars <= 0)
      return;
   datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
   if(TimeCurrent() - opened >= InpTimeStopBars * 60)
      g_trade.PositionClose(ticket);
  }

//+------------------------------------------------------------------+
//| Confirmed fractal pivots over a chronological array              |
//| lo[i] true when arr[i] is a strict local min with k bars a side  |
//+------------------------------------------------------------------+
void FindPivots(const double &arr[], const int n, const int k, bool &lo[], bool &hi[])
  {
   ArrayResize(lo, n);
   ArrayResize(hi, n);
   ArrayInitialize(lo, false);
   ArrayInitialize(hi, false);

   for(int i = k; i < n - k; i++)
     {
      double v = arr[i];
      bool isLo = true, isHi = true;
      for(int j = i - k; j < i; j++)
        {
         if(arr[j] <= v) isLo = false;
         if(arr[j] >= v) isHi = false;
        }
      for(int j = i + 1; j <= i + k; j++)
        {
         if(arr[j] < v)  isLo = false;
         if(arr[j] > v)  isHi = false;
        }
      lo[i] = isLo;
      hi[i] = isHi;
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   RollDay();

   ulong ticket = 0;
   if(HasPosition(ticket))
     {
      ManageOpen(ticket);
      return;                       // strictly one position at a time
     }

   //--- act once per completed M1 bar
   datetime barTime = (datetime)SeriesInfoInteger(_Symbol, PERIOD_M1, SERIES_LASTBAR_DATE);
   if(barTime == g_lastBar)
      return;
   g_lastBar = barTime;

   if(InpMaxDailyLossUsd > 0.0 && !g_dayHalted)
     {
      if(g_dayStartEq - AccountInfoDouble(ACCOUNT_EQUITY) >= InpMaxDailyLossUsd)
        {
         g_dayHalted = true;
         Print("EA_GoldM1RsiReversal: daily loss cap hit, standing down.");
        }
     }
   if(g_dayHalted)
      return;

   //--- re-arm lockout.
   //    The validated backtest blocks a new entry for InpTimeStopBars bars after
   //    EACH entry, even when that trade exited early on SL/TP. Without this the
   //    EA would take extra trades the research never measured.
   if(TimeCurrent() < g_lockoutTo)
      return;

   //--- session gate: the core of the edge
   int h = UtcHour(TimeCurrent());
   if(h < InpSessStartUtc || h >= InpSessEndUtc)
      return;

   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk))
      return;
   if((tk.ask - tk.bid) > InpMaxSpread)
      return;

   //--- pull completed bars (shift 1 = last closed bar)
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int n = CopyRates(_Symbol, PERIOD_M1, 1, LOOKBACK, rates);
   if(n < InpEmaSlow + InpRsiPeriod + 4 * InpPivotK + 5)
      return;

   double rsi[], emaF[], emaS[];
   ArraySetAsSeries(rsi,  false);
   ArraySetAsSeries(emaF, false);
   ArraySetAsSeries(emaS, false);
   if(CopyBuffer(g_hRsi,  0, 1, n, rsi)  != n) return;
   if(CopyBuffer(g_hFast, 0, 1, n, emaF) != n) return;
   if(CopyBuffer(g_hSlow, 0, 1, n, emaS) != n) return;

   double highs[], lows[];
   ArrayResize(highs, n);
   ArrayResize(lows,  n);
   for(int i = 0; i < n; i++)
     {
      highs[i] = rates[i].high;
      lows[i]  = rates[i].low;
     }

   bool pLo[], pHiDummy[], pHi[], pLoDummy[];
   FindPivots(lows,  n, InpPivotK, pLo, pHiDummy);
   FindPivots(highs, n, InpPivotK, pLoDummy, pHi);

   int t = n - 1;                       // last completed bar
   int cutoff = t - InpPivotK;          // pivots confirmed on or before t
   if(cutoff < InpPivotK + 1)
      return;

   bool up = emaF[t] > emaS[t];
   bool dn = emaF[t] < emaS[t];
   if(!up && !dn)
      return;

   bool green = (rates[t].close > rates[t].open) && (rates[t].high > rates[t - 1].high);
   bool red   = (rates[t].close < rates[t].open) && (rates[t].low  < rates[t - 1].low);

   int dir = 0;
   double stop = 0.0;

   if(up && green)
     {
      int b = -1, a = -1;
      for(int i = cutoff; i >= 0 && b < 0; i--) if(pLo[i]) b = i;
      if(b > 0) for(int i = b - 1; i >= 0 && a < 0; i--) if(pLo[i]) a = i;
      if(a >= 0 && b >= 0 && (t - b) <= InpTriggerWindow)
        {
         if(lows[b] > lows[a] && rsi[b] < rsi[a] && rsi[b] >= InpRsiFloor)
           {
            dir  = 1;
            stop = lows[b] - InpSlBuffer;
           }
        }
     }

   if(dir == 0 && dn && red)
     {
      int b = -1, a = -1;
      for(int i = cutoff; i >= 0 && b < 0; i--) if(pHi[i]) b = i;
      if(b > 0) for(int i = b - 1; i >= 0 && a < 0; i--) if(pHi[i]) a = i;
      if(a >= 0 && b >= 0 && (t - b) <= InpTriggerWindow)
        {
         if(highs[b] < highs[a] && rsi[b] > rsi[a] && rsi[b] <= InpRsiCeil)
           {
            dir  = -1;
            stop = highs[b] + InpSlBuffer;
           }
        }
     }

   if(dir == 0)
      return;

   //--- size the trade off the structural stop
   double entry = (dir == 1) ? tk.ask : tk.bid;
   double risk  = (dir == 1) ? (entry - stop) : (stop - entry);
   if(risk < InpMinRisk || risk > InpMaxRisk)
      return;

   double tp = (dir == 1) ? entry + InpRR * risk : entry - InpRR * risk;

   long   stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStop    = (double)stopsLevel * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(minStop > 0.0 && risk < minStop)
      return;

   int    dg   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double lots = NormalizeLots(InpLots);

   bool ok;
   if(dir == 1)
      ok = g_trade.Buy(lots, _Symbol, 0.0,
                       NormalizeDouble(stop, dg), NormalizeDouble(tp, dg), "rsirev");
   else
      ok = g_trade.Sell(lots, _Symbol, 0.0,
                        NormalizeDouble(stop, dg), NormalizeDouble(tp, dg), "rsirev");

   if(ok)
      g_lockoutTo = TimeCurrent() + InpTimeStopBars * 60;
   else
      PrintFormat("EA_GoldM1RsiReversal: order failed ret=%d %s",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
  }
//+------------------------------------------------------------------+
