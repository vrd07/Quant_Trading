//+------------------------------------------------------------------+
//|                                      GoldenChart_Liquidity.mq5    |
//|   Marks XAUUSD liquidity pools and ranks them by P(hit first).    |
//|   Level detection is a port of                                    |
//|   src/microstructure/liquidity_levels.py — the two MUST agree.    |
//|   scripts/check_liquidity_parity.py is what proves they do.       |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

#include "liquidity_coefficients.mqh"

//--- Detection (defaults ARE the calibrated values — changing them
//    invalidates the baked coefficients) ------------------------------
input ENUM_TIMEFRAMES InpDetectTF        = PERIOD_M15; // Detection timeframe (calibrated: M15)
input int    InpPivotN                   = 5;      // Swing pivot width N
input double InpEqTolATR                 = 0.10;   // Equal-cluster tolerance (x ATR)
input double InpMergeTolATR              = 0.10;   // Merge tolerance (x ATR)
input int    InpLevelsPerSide            = 6;      // Levels kept per side
input double InpMaxDistATR               = 8.0;    // Max distance from price (x ATR)
input double InpFormingBandATR           = 0.25;   // Forming-extreme dead band (x ATR)
input double InpTouchBandATR             = 0.25;   // Touch-count band (x ATR)
input int    InpScanBars                 = 1000;   // Pivot scan window (bars)
input int    InpTouchLookback            = 500;    // Touch-count lookback (bars)
input int    InpATRPeriod                = 14;     // ATR period
input int    InpATRPctileWindow          = 500;    // ATR percentile window
input int    InpEMAPeriod                = 50;     // Trend EMA period
input int    InpEMASlopeBars             = 3;      // EMA slope lookback
input int    InpHistoryBars              = 2500;   // Bars loaded (scan + pctile + warmup)
//--- Display -------------------------------------------------------
input int    InpRefreshSec               = 5;      // Intra-bar refresh (seconds)
input bool   InpShowPanel                = true;   // Show the ranking panel
input color  InpBuySideColor             = clrDodgerBlue;  // Levels above price
input color  InpSellSideColor            = clrTomato;      // Levels below price
//--- Parity harness ------------------------------------------------
input bool   InpExportCSV                = false;  // Export bars + levels for parity
input int    InpExportSnapshots          = 400;    // Snapshots to export (most recent)

const string PFX = "GC_LQ_";

//--- kind codes; KindName() returns the exact Python string --------
#define K_SWING_HIGH   0
#define K_SWING_LOW    1
#define K_EQUAL_HIGHS  2
#define K_EQUAL_LOWS   3
#define K_ASIA_HIGH    4
#define K_ASIA_LOW     5
#define K_LONDON_HIGH  6
#define K_LONDON_LOW   7
#define K_NY_HIGH      8
#define K_NY_LOW       9
#define K_PD_HIGH     10
#define K_PD_LOW      11
#define K_PW_HIGH     12
#define K_PW_LOW      13

string KindName(const int k)
{
   switch(k)
   {
      case K_SWING_HIGH:  return "swing_high";
      case K_SWING_LOW:   return "swing_low";
      case K_EQUAL_HIGHS: return "equal_highs";
      case K_EQUAL_LOWS:  return "equal_lows";
      case K_ASIA_HIGH:   return "asia_high";
      case K_ASIA_LOW:    return "asia_low";
      case K_LONDON_HIGH: return "london_high";
      case K_LONDON_LOW:  return "london_low";
      case K_NY_HIGH:     return "ny_high";
      case K_NY_LOW:      return "ny_low";
      case K_PD_HIGH:     return "pd_high";
      case K_PD_LOW:      return "pd_low";
      case K_PW_HIGH:     return "pw_high";
      case K_PW_LOW:      return "pw_low";
   }
   return "unknown";
}

string KindTag(const int k)
{
   switch(k)
   {
      case K_SWING_HIGH:  return "SwH";
      case K_SWING_LOW:   return "SwL";
      case K_EQUAL_HIGHS: return "EQH";
      case K_EQUAL_LOWS:  return "EQL";
      case K_ASIA_HIGH:   return "ASIAH";
      case K_ASIA_LOW:    return "ASIAL";
      case K_LONDON_HIGH: return "LONH";
      case K_LONDON_LOW:  return "LONL";
      case K_NY_HIGH:     return "NYH";
      case K_NY_LOW:      return "NYL";
      case K_PD_HIGH:     return "PDH";
      case K_PD_LOW:      return "PDL";
      case K_PW_HIGH:     return "PWH";
      case K_PW_LOW:      return "PWL";
   }
   return "?";
}

// equal-cluster (0) > session/period (1) > solo swing (2)
int KindPriority(const int k)
{
   if(k == K_EQUAL_HIGHS || k == K_EQUAL_LOWS) return 0;
   if(k == K_SWING_HIGH  || k == K_SWING_LOW)  return 2;
   return 1;
}

struct LqLevel
{
   double price;
   int    kind;
   int    formation;   // buffer index of the bar that set the level
   int    up;          // 1 above close, 0 below
   double feat[12];
   double score;
   double prob;
};

//--- frame state ---------------------------------------------------
MqlRates gR[];
int      gN = 0;
double   gATR[], gEMA[], gSlope[], gPct[];
datetime gUTC[];
int      gHour[];
long     gDay[], gWeek[];
long     gTZOffset = 0;
datetime gLastBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "GoldenChart Liquidity Race");
   if(InpDetectTF != PERIOD_M15)
      Print("GC_LQ WARNING: detection TF is not M15. The baked coefficients were ",
            "calibrated on M15 — the probabilities on this chart are not the ",
            "probabilities the model was fitted for.");
   Print("GC_LQ ", LQ_BANNER, "  [", LQ_CALIB_WINDOW, "]");
   EventSetTimer(MathMax(1, InpRefreshSec));
   Recompute();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, PFX);
   ChartRedraw();
}

int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
{
   return(rates_total);
}

void OnTimer()
{
   Recompute();
}

//+------------------------------------------------------------------+
//| UTC handling: derive the broker offset from the terminal's own    |
//| clocks rather than trusting server time (see project_broker_tz_fix)|
//+------------------------------------------------------------------+
void RefreshTZOffset()
{
   long diff = (long)TimeCurrent() - (long)TimeGMT();
   gTZOffset = (long)MathRound((double)diff / 3600.0) * 3600;
}

//+------------------------------------------------------------------+
bool LoadBars()
{
   int want = MathMax(InpHistoryBars, InpScanBars + InpATRPctileWindow + 200);
   ArraySetAsSeries(gR, false);            // index 0 = OLDEST, matches Python
   gN = CopyRates(_Symbol, InpDetectTF, 0, want, gR);
   if(gN < InpScanBars + 50)
   {
      Print("GC_LQ: only ", gN, " bars available, need ", InpScanBars + 50);
      return false;
   }
   RefreshTZOffset();
   ArrayResize(gUTC, gN); ArrayResize(gHour, gN);
   ArrayResize(gDay, gN); ArrayResize(gWeek, gN);
   for(int i = 0; i < gN; i++)
   {
      long t = (long)gR[i].time - gTZOffset;
      gUTC[i] = (datetime)t;
      MqlDateTime dt;
      TimeToStruct(gUTC[i], dt);
      gHour[i] = dt.hour;
      gDay[i]  = (long)MathFloor((double)t / 86400.0);
      // +259200 shifts the Thursday epoch so week boundaries land on Monday 00:00
      // UTC — the same grouping Python's ISO week gives.
      gWeek[i] = (long)MathFloor(((double)t + 259200.0) / 604800.0);
   }
   return true;
}

//+------------------------------------------------------------------+
void ComputeIndicators()
{
   ArrayResize(gATR, gN); ArrayResize(gEMA, gN);
   ArrayResize(gSlope, gN); ArrayResize(gPct, gN);

   double alpha = 1.0 / (double)InpATRPeriod;
   double acc = gR[0].high - gR[0].low;      // seed with bar 0's own true range
   gATR[0] = acc;
   for(int i = 1; i < gN; i++)
   {
      double pc = gR[i - 1].close;
      double tr = MathMax(gR[i].high - gR[i].low,
                          MathMax(MathAbs(gR[i].high - pc), MathAbs(gR[i].low - pc)));
      acc += alpha * (tr - acc);
      gATR[i] = acc;
   }

   double ea = 2.0 / ((double)InpEMAPeriod + 1.0);
   double e = gR[0].close;
   gEMA[0] = e;
   for(int i = 1; i < gN; i++)
   {
      e += ea * (gR[i].close - e);
      gEMA[i] = e;
   }
   for(int i = 0; i < gN; i++)
      gSlope[i] = (i >= InpEMASlopeBars) ? gEMA[i] - gEMA[i - InpEMASlopeBars] : 0.0;

   for(int i = 0; i < gN; i++)
   {
      int lo = MathMax(0, i - InpATRPctileWindow + 1);
      int cnt = 0, tot = 0;
      for(int j = lo; j <= i; j++) { tot++; if(gATR[j] <= gATR[i]) cnt++; }
      gPct[i] = (double)cnt / (double)tot;
   }
}

//+------------------------------------------------------------------+
//| Pivots: high[i] >= max(high[i-N..i+N]), confirming N bars late.    |
//+------------------------------------------------------------------+
bool IsPivotHigh(const int i)
{
   if(i < InpPivotN || i >= gN - InpPivotN) return false;
   for(int j = i - InpPivotN; j <= i + InpPivotN; j++)
      if(gR[j].high > gR[i].high) return false;
   return true;
}

bool IsPivotLow(const int i)
{
   if(i < InpPivotN || i >= gN - InpPivotN) return false;
   for(int j = i - InpPivotN; j <= i + InpPivotN; j++)
      if(gR[j].low < gR[i].low) return false;
   return true;
}

// un-swept: nothing in (i, t] reached the level. Ties sweep, per the spec.
bool UnsweptHigh(const int i, const int t)
{
   for(int j = i + 1; j <= t; j++) if(gR[j].high >= gR[i].high) return false;
   return true;
}

bool UnsweptLow(const int i, const int t)
{
   for(int j = i + 1; j <= t; j++) if(gR[j].low <= gR[i].low) return false;
   return true;
}

//+------------------------------------------------------------------+
void AppendLevel(LqLevel &arr[], int &n, const double price, const int kind,
                 const int formation, const double close)
{
   if(price == close) return;
   if(ArraySize(arr) < n + 1) ArrayResize(arr, n + 32);
   arr[n].price = price;
   arr[n].kind = kind;
   arr[n].formation = formation;
   arr[n].up = (price > close) ? 1 : 0;
   n++;
}

// ascending sort by (price, priority, formation) — the order both sides cluster in
void SortLevels(LqLevel &a[], const int n)
{
   for(int i = 1; i < n; i++)
   {
      LqLevel key = a[i];
      int j = i - 1;
      while(j >= 0 && (a[j].price > key.price ||
            (a[j].price == key.price &&
             (KindPriority(a[j].kind) > KindPriority(key.kind) ||
              (KindPriority(a[j].kind) == KindPriority(key.kind) &&
               a[j].formation > key.formation)))))
      { a[j + 1] = a[j]; j--; }
      a[j + 1] = key;
   }
}

//+------------------------------------------------------------------+
//| Solo swings -> equal clusters. A 2+ cluster is priced at the       |
//| EXTREME (max for highs, min for lows) and carries the most recent  |
//| constituent's formation bar. Constituents are removed.             |
//+------------------------------------------------------------------+
int ClusterEqual(LqLevel &src[], const int n, const bool highs, const double tol,
                 const double close, LqLevel &out[], int &outN)
{
   LqLevel fam[];
   int m = 0;
   ArrayResize(fam, MathMax(n, 1));
   int want = highs ? K_SWING_HIGH : K_SWING_LOW;
   for(int i = 0; i < n; i++)
      if(src[i].kind == want) { fam[m] = src[i]; m++; }
   if(m == 0) return 0;
   SortLevels(fam, m);

   int start = 0;
   for(int i = 1; i <= m; i++)
   {
      bool brk = (i == m) || ((fam[i].price - fam[i - 1].price) > tol);
      if(!brk) continue;
      int size = i - start;
      if(size >= 2)
      {
         double ext = fam[start].price;
         int form = fam[start].formation;
         for(int j = start; j < i; j++)
         {
            if(highs ? (fam[j].price > ext) : (fam[j].price < ext)) ext = fam[j].price;
            if(fam[j].formation > form) form = fam[j].formation;
         }
         AppendLevel(out, outN, ext, highs ? K_EQUAL_HIGHS : K_EQUAL_LOWS, form, close);
      }
      else
         AppendLevel(out, outN, fam[start].price, fam[start].kind,
                     fam[start].formation, close);
      start = i;
   }
   return outN;
}

//+------------------------------------------------------------------+
//| Session / period extremes.                                        |
//+------------------------------------------------------------------+
bool InSession(const int i, const int h0, const int h1)
{
   return (gHour[i] >= h0 && gHour[i] < h1);
}

void AddExtreme(const int lo, const int hi, const int t, const bool wantHigh,
                const int kind, const bool forming, const double close,
                const double atr, LqLevel &out[], int &outN)
{
   if(hi < lo) return;
   int k = lo;
   for(int j = lo; j <= hi; j++)
   {
      if(wantHigh ? (gR[j].high > gR[k].high) : (gR[j].low < gR[k].low)) k = j;
   }
   double price = wantHigh ? gR[k].high : gR[k].low;
   for(int j = k + 1; j <= t; j++)
   {
      if(wantHigh ? (gR[j].high >= price) : (gR[j].low <= price)) return;  // swept
   }
   if(forming && MathAbs(price - close) < InpFormingBandATR * atr) return;
   AppendLevel(out, outN, price, kind, k, close);
}

void SessionRuns(const long &ids[], const int lo, const int hi,
                 int &pLo, int &pHi, int &cLo, int &cHi)
{
   pLo = pHi = cLo = cHi = -1;
   int lastLo = -1, lastHi = -1, prevLo = -1, prevHi = -1;
   int i = lo;
   while(i <= hi)
   {
      if(ids[i] < 0) { i++; continue; }
      int j = i;
      while(j + 1 <= hi && ids[j + 1] == ids[i]) j++;
      prevLo = lastLo; prevHi = lastHi;
      lastLo = i; lastHi = j;
      i = j + 1;
   }
   if(lastHi == hi) { cLo = lastLo; cHi = lastHi; pLo = prevLo; pHi = prevHi; }
   else             { pLo = lastLo; pHi = lastHi; }
}

void SessionLevels(const int t, const double close, const double atr,
                   LqLevel &out[], int &outN)
{
   int lo = MathMax(0, t - InpScanBars + 1);
   int h0[3] = {0, 7, 13};
   int h1[3] = {7, 16, 21};
   int kHi[3] = {K_ASIA_HIGH, K_LONDON_HIGH, K_NY_HIGH};
   int kLo[3] = {K_ASIA_LOW,  K_LONDON_LOW,  K_NY_LOW};

   long ids[];
   ArrayResize(ids, gN);
   for(int s = 0; s < 3; s++)
   {
      for(int i = lo; i <= t; i++)
         ids[i] = InSession(i, h0[s], h1[s]) ? gDay[i] : -1;
      int pL, pH, cL, cH;
      SessionRuns(ids, lo, t, pL, pH, cL, cH);
      if(pL >= 0)
      {
         AddExtreme(pL, pH, t, true,  kHi[s], false, close, atr, out, outN);
         AddExtreme(pL, pH, t, false, kLo[s], false, close, atr, out, outN);
      }
      if(cL >= 0)
      {
         AddExtreme(cL, cH, t, true,  kHi[s], true, close, atr, out, outN);
         AddExtreme(cL, cH, t, false, kLo[s], true, close, atr, out, outN);
      }
   }

   for(int p = 0; p < 2; p++)
   {
      for(int i = lo; i <= t; i++) ids[i] = (p == 0) ? gDay[i] : gWeek[i];
      int pL, pH, cL, cH;
      SessionRuns(ids, lo, t, pL, pH, cL, cH);
      // a calendar period always contains t, so the "prior" run is the completed one
      if(pL < 0) continue;
      AddExtreme(pL, pH, t, true,  (p == 0) ? K_PD_HIGH : K_PW_HIGH,
                 false, close, atr, out, outN);
      AddExtreme(pL, pH, t, false, (p == 0) ? K_PD_LOW : K_PW_LOW,
                 false, close, atr, out, outN);
   }
}

//+------------------------------------------------------------------+
//| The choice set: distance cap -> merge -> 6 nearest per side.       |
//| Output order is (up-side first, then ascending distance, then      |
//| price) — the parity harness compares positionally.                 |
//+------------------------------------------------------------------+
int BuildChoiceSet(const int t, LqLevel &out[])
{
   double atr = gATR[t];
   double close = gR[t].close;
   int outN = 0;
   ArrayResize(out, 64);
   if(atr <= 0.0) return 0;

   LqLevel raw[];
   int rawN = 0;
   ArrayResize(raw, 64);
   int lo = MathMax(0, t - InpScanBars + 1);
   int hi = t - InpPivotN;
   for(int i = lo; i <= hi; i++)
   {
      if(IsPivotHigh(i) && UnsweptHigh(i, t))
         AppendLevel(raw, rawN, gR[i].high, K_SWING_HIGH, i, close);
      if(IsPivotLow(i) && UnsweptLow(i, t))
         AppendLevel(raw, rawN, gR[i].low, K_SWING_LOW, i, close);
   }

   LqLevel merged[];
   int mN = 0;
   ArrayResize(merged, 64);
   ClusterEqual(raw, rawN, true,  InpEqTolATR * atr, close, merged, mN);
   ClusterEqual(raw, rawN, false, InpEqTolATR * atr, close, merged, mN);
   SessionLevels(t, close, atr, merged, mN);

   // distance cap
   LqLevel kept[];
   int kN = 0;
   ArrayResize(kept, 64);
   double maxd = InpMaxDistATR * atr;
   for(int i = 0; i < mN; i++)
   {
      double d = MathAbs(merged[i].price - close);
      if(d > 0.0 && d <= maxd) { kept[kN] = merged[i]; kN++; }
   }
   if(kN == 0) return 0;

   // merge within tolerance, keeping (priority, formation, price)
   SortLevels(kept, kN);
   LqLevel mg[];
   int gN2 = 0;
   ArrayResize(mg, kN);
   double tol = InpMergeTolATR * atr;
   int start = 0;
   for(int i = 1; i <= kN; i++)
   {
      bool brk = (i == kN) || ((kept[i].price - kept[i - 1].price) > tol);
      if(!brk) continue;
      int best = start;
      for(int j = start; j < i; j++)
      {
         int pb = KindPriority(kept[best].kind), pj = KindPriority(kept[j].kind);
         if(pj < pb || (pj == pb && (kept[j].formation < kept[best].formation ||
            (kept[j].formation == kept[best].formation &&
             kept[j].price < kept[best].price))))
            best = j;
      }
      mg[gN2] = kept[best];
      mg[gN2].up = (mg[gN2].price > close) ? 1 : 0;
      gN2++;
      start = i;
   }

   // 6 nearest per side, up side first
   for(int side = 1; side >= 0; side--)
   {
      int taken = 0;
      while(taken < InpLevelsPerSide)
      {
         int best = -1;
         double bd = 0.0;
         for(int i = 0; i < gN2; i++)
         {
            if(mg[i].up != side || mg[i].kind < 0) continue;
            double d = MathAbs(mg[i].price - close);
            if(best < 0 || d < bd || (d == bd && mg[i].price < mg[best].price))
            { best = i; bd = d; }
         }
         if(best < 0) break;
         out[outN] = mg[best];
         outN++;
         mg[best].kind = -1;          // consumed
         taken++;
      }
   }
   return outN;
}

//+------------------------------------------------------------------+
//| Features — order is a hard contract with FEATURE_NAMES in          |
//| src/microstructure/liquidity_levels.py. Raw values; z-scoring is   |
//| applied at scoring time with the baked LQ_MEAN / LQ_STD.           |
//+------------------------------------------------------------------+
double TouchCount(const int t, const LqLevel &lv, const double band)
{
   int start = MathMax(lv.formation + 1, MathMax(t - InpTouchLookback + 1, 0));
   int c = 0;
   for(int j = start; j <= t; j++)
   {
      if(lv.up == 1)
      { if(gR[j].high >= lv.price - band && gR[j].high < lv.price) c++; }
      else
      { if(gR[j].low <= lv.price + band && gR[j].low > lv.price) c++; }
   }
   return (double)c;
}

void ComputeFeatures(const int t, LqLevel &lv[], const int n)
{
   double atr = gATR[t];
   double close = gR[t].close;
   double pct = gPct[t];
   double band = InpTouchBandATR * atr;
   double slope = gSlope[t];
   int h = gHour[t];
   double sLon = (h >= 7 && h < 16) ? 1.0 : 0.0;
   double sNY  = (h >= 13 && h < 21) ? 1.0 : 0.0;

   for(int i = 0; i < n; i++)
   {
      double di = MathAbs(lv[i].price - close);
      int closer = 0;
      for(int j = 0; j < n; j++)
      {
         if(j == i || lv[j].up != lv[i].up) continue;
         if(MathAbs(lv[j].price - close) < di) closer++;
      }
      double logd = MathLog(1.0 + di / atr);
      bool aligned = ((lv[i].up == 1 && slope > 0.0) || (lv[i].up == 0 && slope < 0.0));
      lv[i].feat[0]  = logd;
      lv[i].feat[1]  = (double)closer;
      lv[i].feat[2]  = (double)lv[i].up;
      lv[i].feat[3]  = (lv[i].kind == K_EQUAL_HIGHS || lv[i].kind == K_EQUAL_LOWS) ? 1.0 : 0.0;
      lv[i].feat[4]  = (KindPriority(lv[i].kind) == 1) ? 1.0 : 0.0;
      lv[i].feat[5]  = MathLog(1.0 + (double)MathMax(0, t - lv[i].formation));
      lv[i].feat[6]  = TouchCount(t, lv[i], band);
      lv[i].feat[7]  = aligned ? 1.0 : 0.0;
      lv[i].feat[8]  = pct;
      lv[i].feat[9]  = sLon;
      lv[i].feat[10] = sNY;
      lv[i].feat[11] = logd * pct;
   }
}

//+------------------------------------------------------------------+
//| Parity export. Writes the WHOLE bar buffer plus one row per        |
//| (snapshot, level) so check_liquidity_parity.py can rebuild the     |
//| Python context from the identical bars — recursive ATR/EMA seeds   |
//| and the 500-bar ATR rank make any other comparison unreliable.     |
//+------------------------------------------------------------------+
void ExportParity()
{
   int fb = FileOpen(PFX + "bars.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fb == INVALID_HANDLE) { Print("GC_LQ: cannot write bars csv ", GetLastError()); return; }
   FileWrite(fb, "time_utc", "open", "high", "low", "close", "volume");
   for(int i = 0; i < gN; i++)
      FileWrite(fb, TimeToString(gUTC[i], TIME_DATE | TIME_MINUTES | TIME_SECONDS),
                DoubleToString(gR[i].open, 5), DoubleToString(gR[i].high, 5),
                DoubleToString(gR[i].low, 5), DoubleToString(gR[i].close, 5),
                (string)gR[i].tick_volume);
   FileClose(fb);

   int fl = FileOpen(PFX + "levels.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fl == INVALID_HANDLE) { Print("GC_LQ: cannot write levels csv ", GetLastError()); return; }
   string hdr = "snapshot_time_utc,row,price,kind,formation_time_utc,side,dist_atr";
   for(int f = 0; f < LQ_N_FEATURES; f++) hdr += ",f" + IntegerToString(f);
   hdr += ",score,prob";
   FileWrite(fl, hdr);

   int lastT = gN - 1;
   int firstT = MathMax(InpScanBars, lastT - InpExportSnapshots + 1);
   LqLevel lv[];
   for(int t = firstT; t <= lastT; t++)
   {
      int n = BuildChoiceSet(t, lv);
      if(n == 0) continue;
      ComputeFeatures(t, lv, n);
      ScoreLevels(lv, n);
      for(int i = 0; i < n; i++)
      {
         string row = TimeToString(gUTC[t], TIME_DATE | TIME_MINUTES | TIME_SECONDS)
                    + "," + IntegerToString(i)
                    + "," + DoubleToString(lv[i].price, 5)
                    + "," + KindName(lv[i].kind)
                    + "," + TimeToString(gUTC[lv[i].formation],
                                         TIME_DATE | TIME_MINUTES | TIME_SECONDS)
                    + "," + (lv[i].up == 1 ? "up" : "down")
                    + "," + DoubleToString((lv[i].price - gR[t].close) / gATR[t], 8);
         for(int f = 0; f < LQ_N_FEATURES; f++)
            row += "," + DoubleToString(lv[i].feat[f], 10);
         row += "," + DoubleToString(lv[i].score, 10)
              + "," + DoubleToString(lv[i].prob, 10);
         FileWrite(fl, row);
      }
   }
   FileClose(fl);
   Print("GC_LQ: parity export written to MQL5/Files/", PFX, "bars.csv and ",
         PFX, "levels.csv");
}

//+------------------------------------------------------------------+
void Recompute()
{
   if(!LoadBars()) return;
   ComputeIndicators();

   bool newBar = (gUTC[gN - 1] != gLastBar);
   gLastBar = gUTC[gN - 1];

   int t = gN - 1;
   LqLevel lv[];
   int n = BuildChoiceSet(t, lv);
   if(n > 0)
   {
      ComputeFeatures(t, lv, n);
      ScoreLevels(lv, n);
   }
   Render(lv, n, t);

   if(InpExportCSV && newBar) ExportParity();
}

//--- TEMPORARY STUBS, replaced in Task 12 --------------------------
void ScoreLevels(LqLevel &lv[], const int n)
{
   for(int i = 0; i < n; i++) { lv[i].score = 0.0; lv[i].prob = 0.0; }
}

void Render(LqLevel &lv[], const int n, const int t)
{
   Comment("GC_LQ: ", n, " levels detected (rendering lands in Task 12)");
}
