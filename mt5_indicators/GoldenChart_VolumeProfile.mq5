//+------------------------------------------------------------------+
//|                                 GoldenChart_VolumeProfile.mq5    |
//|   XAUUSD volume profile: VAH/VAL/VPOC plus the AMT context read  |
//|   (P/b/D shape, open type, balance regime, IB, composite).       |
//|                                                                  |
//|   This is a PORT of two Python modules and must agree with them: |
//|     src/microstructure/volume_profile.py                         |
//|     src/microstructure/profile_context.py                        |
//|   scripts/check_volume_profile_parity.py is what proves it does. |
//|                                                                  |
//|   WHAT THIS MEASURES: a gold CFD has no traded volume. MT5's     |
//|   volume/volume_real are zero or synthetic for XAUUSD, so every  |
//|   number here is a TICK DENSITY, never a contract count. No      |
//|   label in this file may imply otherwise.                        |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//--- Profile geometry ----------------------------------------------
input double InpRowSize            = 0.10;   // Row height (USD, not points)
input double InpValueAreaPct       = 0.70;   // Value area fraction
input int    InpVAAlgorithm        = 0;      // 0=single-row (TradingView), 1=two-row (Steidlmayer)
input int    InpProfileDays        = 10;     // Completed sessions to draw
input int    InpCompositeDays      = 5;      // Sessions in the composite
input int    InpSessionUTCOverride = -1;     // -1 = use broker D1 boundary
//--- Tick handling -------------------------------------------------
input double InpMaxSpreadUSD       = 1.00;   // Reject ticks wider than this
input int    InpTickPriceMode      = 0;      // 0=mid, 1=bid, 2=ask
input int    InpMinSessionTicks    = 5000;   // Below this, skip the session
input int    InpTickRetryLimit     = 12;     // Retries before falling back to M1
//--- Context -------------------------------------------------------
input double InpSkewThreshold      = 0.0;    // 0 = UNCALIBRATED (panel will warn)
input int    InpMinRowsForShape    = 5;      // Fewer rows => UNCLASSIFIED
input double InpRegimeMinElapsed   = 0.50;   // Developing regime reports FORMING below this
input int    InpIBMinutes          = 60;     // Initial balance window
//--- Nodes (UNCALIBRATED display heuristics) -----------------------
input bool   InpShowLVN            = true;   // Low-volume nodes (absorption zones)
input bool   InpShowHVN            = false;  // High-volume nodes
input double InpHVNProminencePct   = 0.15;
input double InpLVNRatio           = 0.50;
input int    InpNodeMinSepRows     = 10;
//--- Display -------------------------------------------------------
input int    InpRefreshSec         = 5;
input bool   InpShowPanel          = true;
input int    InpHistogramProfiles  = 2;      // Sessions drawn as a histogram
input double InpHistogramWidthPct  = 0.35;
input int    InpMaxObjects         = 3000;
input color  InpVPOCColor          = clrGold;
input color  InpVAHColor           = clrDeepSkyBlue;
input color  InpVALColor           = clrTomato;
input color  InpNakedPOCColor      = clrMagenta;
input color  InpCompositeColor     = clrOrchid;
input color  InpIBColor            = clrSlateGray;
input color  InpLVNColor           = clrDarkOrange;
input color  InpHVNColor           = clrDarkSlateGray;
//--- Alerts --------------------------------------------------------
input bool   InpAlertsOn           = false;
input bool   InpAlertDeveloping    = false;
input bool   InpAlertLVN           = false;
input double InpAlertRearmUSD      = 0.50;
input bool   InpSendNotifications  = false;
//--- Parity harness ------------------------------------------------
input bool   InpExportCSV          = false;

const string PFX = "GC_VP_";

#define SRC_PENDING 0
#define SRC_TICK    1
#define SRC_M1      2

#define SHAPE_UNKNOWN 0
#define SHAPE_P       1
#define SHAPE_B       2
#define SHAPE_D       3

string ShapeName(const int s)
{
   switch(s)
   {
      case SHAPE_P: return "P";
      case SHAPE_B: return "b";
      case SHAPE_D: return "D";
   }
   return "UNCLASSIFIED";
}

string SourceName(const int s)
{
   switch(s)
   {
      case SRC_TICK: return "TICK";
      case SRC_M1:   return "M1";
   }
   return "PENDING";
}

struct SessionProfile
{
   datetime start;
   datetime end;
   int      min_row;
   double   volumes[];
   int      accepted;
   int      rejected;
   int      source;
   int      retries;
   ulong    cursor_msc;      // retain-and-replay cursor (developing only)
   // resolved
   bool     valid;
   int      poc_row, val_row, vah_row;
   double   vpoc, val, vah, low, high;
   double   skew;
   bool     has_skew;
   int      shape;
   double   ib_low, ib_high;
   bool     has_ib;
   double   open_price;
};

//--- Grid. Absolute, never session-anchored: a price maps to the same
//    row in every session, so VPOCs stay comparable across days.
//
//    NOT a plain floor. 4493.20 / 0.10 evaluates to 44931.99999999999 in
//    IEEE754, so floor() drops the price a whole row -- precisely at the
//    round numbers price gravitates to. This snap rule is byte-identical to
//    _rows_from_quotients() in volume_profile.py; if you change one, change
//    both or the parity harness will start failing at round prices.
#define VP_SNAP 1e-6

int RowIndex(const double price)
{
   double q = price / InpRowSize;
   double r = MathRound(q);
   if(MathAbs(q - r) < VP_SNAP) return (int)r;
   return (int)MathFloor(q);
}

double RowLow (const int row)       { return row * InpRowSize; }
double RowMid (const int row)       { return (row + 0.5) * InpRowSize; }
double RowHigh(const int row)       { return (row + 1) * InpRowSize; }

//--- POC: max volume; ties resolve toward the middle of the occupied
//    range, then to the lower index. Matches poc_index() in Python.
int POCIndex(const double &v[])
{
   int n = ArraySize(v);
   if(n == 0) return -1;
   double vmax = 0.0;
   for(int i = 0; i < n; i++) if(v[i] > vmax) vmax = v[i];
   if(vmax <= 0.0) return -1;

   int first = -1, last = -1;
   for(int i = 0; i < n; i++) if(v[i] > 0.0) { if(first < 0) first = i; last = i; }
   double mid = (first + last) / 2.0;

   int best = -1; double bestDist = 0.0;
   for(int i = 0; i < n; i++)
   {
      if(v[i] != vmax) continue;
      double d = MathAbs(i - mid);
      if(best < 0 || d < bestDist) { best = i; bestDist = d; }  // strict < keeps the LOWER index on a tie
   }
   return best;
}

//--- Value area. Mirrors value_area() in Python, including both tie
//    branches: nearer-to-POC wins, and equidistant takes the UPPER row.
void ValueArea(const double &v[], const int poc_i, double target_frac,
               const int algorithm, int &out_lo, int &out_hi)
{
   int n = ArraySize(v);
   out_lo = poc_i; out_hi = poc_i;
   if(n == 0 || poc_i < 0) return;

   double total = 0.0;
   for(int i = 0; i < n; i++) total += v[i];
   if(total <= 0.0) return;

   double target = total * target_frac;
   double acc = v[poc_i];

   while(acc < target)
   {
      bool up = (out_hi + 1 < n);
      bool dn = (out_lo - 1 >= 0);
      if(!up && !dn) break;

      if(algorithm == 0)   // single-row, TradingView/CQG
      {
         bool takeUp;
         if(!dn)      takeUp = true;
         else if(!up) takeUp = false;
         else
         {
            double a = v[out_hi + 1], b = v[out_lo - 1];
            if(a != b) takeUp = (a > b);
            else
            {
               int dUp = (out_hi + 1) - poc_i;
               int dDn = poc_i - (out_lo - 1);
               takeUp = (dUp <= dDn);        // equidistant -> upper row
            }
         }
         if(takeUp) { out_hi++; acc += v[out_hi]; }
         else       { out_lo--; acc += v[out_lo]; }
      }
      else                 // two-row pairs, Steidlmayer/Sierra/ToS
      {
         double upSum = -1.0, dnSum = -1.0;
         if(up)
         {
            upSum = v[out_hi + 1];
            if(out_hi + 2 < n) upSum += v[out_hi + 2];
         }
         if(dn)
         {
            dnSum = v[out_lo - 1];
            if(out_lo - 2 >= 0) dnSum += v[out_lo - 2];
         }
         if(!dn || (up && upSum >= dnSum)) out_hi = MathMin(out_hi + 2, n - 1);
         else                              out_lo = MathMax(out_lo - 2, 0);

         acc = 0.0;
         for(int i = out_lo; i <= out_hi; i++) acc += v[i];
      }
   }
}

//--- Skew: standardised third central moment. Sign convention:
//      negative -> mass at HIGH prices -> P
//      positive -> mass at LOW  prices -> b
//    A published indicator reads this geometry the opposite way. Follow
//    the spec, not that script.
bool ProfileSkew(const double &v[], const int min_row, double &out_skew)
{
   int n = ArraySize(v);
   if(n < 2) return false;
   double total = 0.0;
   for(int i = 0; i < n; i++) total += v[i];
   if(total <= 0.0) return false;

   double mean = 0.0;
   for(int i = 0; i < n; i++) mean += (v[i] / total) * RowMid(min_row + i);

   double var = 0.0;
   for(int i = 0; i < n; i++)
   {
      double d = RowMid(min_row + i) - mean;
      var += (v[i] / total) * d * d;
   }
   if(var <= 0.0) return false;
   double sd = MathSqrt(var);

   double m3 = 0.0;
   for(int i = 0; i < n; i++)
   {
      double d = RowMid(min_row + i) - mean;
      m3 += (v[i] / total) * d * d * d;
   }
   out_skew = m3 / (sd * sd * sd);
   return true;
}

//--- Grow the row array on demand, keeping min_row aligned to the
//    absolute grid.
void AddToRow(SessionProfile &p, const int row, const double w)
{
   int n = ArraySize(p.volumes);
   if(n == 0)
   {
      ArrayResize(p.volumes, 1);
      p.volumes[0] = w;
      p.min_row = row;
      return;
   }
   if(row < p.min_row)
   {
      int shift = p.min_row - row;
      ArrayResize(p.volumes, n + shift);
      for(int i = n - 1; i >= 0; i--) p.volumes[i + shift] = p.volumes[i];
      for(int i = 0; i < shift; i++)  p.volumes[i] = 0.0;
      p.min_row = row;
      p.volumes[0] += w;
      return;
   }
   int idx = row - p.min_row;
   if(idx >= n)
   {
      ArrayResize(p.volumes, idx + 1);
      for(int i = n; i <= idx; i++) p.volumes[i] = 0.0;
   }
   p.volumes[idx] += w;
}

//--- Incremental tick fetch.
//
//    CopyTicksRange is INCLUSIVE on both from_msc and to_msc, and distinct
//    ticks can share a millisecond. The obvious cursor (from = last seen)
//    therefore re-counts the boundary millisecond on EVERY refresh -- a
//    compounding double-count that drags VPOC toward whatever price was
//    busiest at the last refresh. Skipping one tick does not fix it.
//
//    Invariant: nothing at or after cursor_msc has been processed.
//    Process strictly below the batch max, park the cursor there, and defer
//    the partial millisecond one cycle. Also gap-heals across disconnects:
//    if the terminal drops, the cursor does not advance and the next call
//    backfills automatically.
//
//    Returns: number of ticks accumulated, or -1 if history is not ready.
int AccumulateTicksIncremental(SessionProfile &p, const datetime to_time)
{
   MqlTick ticks[];
   ulong to_msc = (ulong)to_time * 1000 + 999;
   int n = CopyTicksRange(_Symbol, ticks, COPY_TICKS_ALL, p.cursor_msc, to_msc);
   if(n < 0)
   {
      // History still synchronising. Do NOT read this as "no ticks" and fall
      // back to M1 -- that would silently pin the profile at reduced fidelity
      // for the rest of the session.
      p.retries++;
      PrintFormat("%s tick history not ready (err %d), retry %d/%d",
                  PFX, GetLastError(), p.retries, InpTickRetryLimit);
      return -1;
   }
   if(n == 0) return 0;

   // MqlTick.time_msc is signed; the CopyTicksRange cursor is not. Compare in
   // the tick's own type and convert once, so the comparison has no sign mismatch.
   long boundary = ticks[n - 1].time_msc;
   int  safe = 0;
   while(safe < n && ticks[safe].time_msc < boundary) safe++;
   p.cursor_msc = (ulong)boundary;

   int added = 0;
   for(int i = 0; i < safe; i++)
   {
      double bid = ticks[i].bid, ask = ticks[i].ask;
      if(bid <= 0.0 || ask <= 0.0) { p.rejected++; continue; }
      if((ask - bid) > InpMaxSpreadUSD) { p.rejected++; continue; }

      double px = (bid + ask) / 2.0;
      if(InpTickPriceMode == 1) px = bid;
      else if(InpTickPriceMode == 2) px = ask;

      if(p.accepted == 0 && added == 0) p.open_price = px;
      AddToRow(p, RowIndex(px), 1.0);
      p.accepted++;
      added++;
   }
   return added;
}

//--- Resolve a histogram into levels. Mirrors build_profile() in Python:
//      VPOC = mid of the POC row
//      VAH  = UPPER edge of the highest absorbed row
//      VAL  = LOWER edge of the lowest absorbed row
//      low/high = edges of the OCCUPIED range, not of the array
//    so the band genuinely contains its >= target_frac of volume.
//    Leaves p.valid false for an empty or all-zero histogram.
void ResolveProfile(SessionProfile &p)
{
   p.valid = false;
   p.has_skew = false;
   p.shape = SHAPE_UNKNOWN;

   int poc_i = POCIndex(p.volumes);
   if(poc_i < 0) return;

   int lo_i, hi_i;
   ValueArea(p.volumes, poc_i, InpValueAreaPct, InpVAAlgorithm, lo_i, hi_i);

   int n = ArraySize(p.volumes);
   int first = -1, last = -1;
   for(int i = 0; i < n; i++) if(p.volumes[i] > 0.0) { if(first < 0) first = i; last = i; }

   p.poc_row = p.min_row + poc_i;
   p.val_row = p.min_row + lo_i;
   p.vah_row = p.min_row + hi_i;
   p.vpoc    = RowMid(p.poc_row);
   p.val     = RowLow(p.val_row);
   p.vah     = RowHigh(p.vah_row);
   p.low     = RowLow(p.min_row + first);
   p.high    = RowHigh(p.min_row + last);
   p.has_skew = ProfileSkew(p.volumes, p.min_row, p.skew);
   p.valid   = true;
}

//--- Lifecycle is wired in Task 10. Until then this compiles and draws
//    nothing, so the port above can be checked by the compiler on its own.
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[],
                const double &high[], const double &low[],
                const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
{
   return rates_total;
}

void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, PFX);
}
