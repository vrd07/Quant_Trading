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

//--- Tick admission. One definition, shared by the incremental cursor and the
//    historical builders -- two copies of a spread filter is two chances to
//    drift. Rejections are counted by the caller, never silently dropped: at
//    rollover and on news gold's spread blows past $1 and the mid lands in a
//    row where nothing traded.
bool TickPrice(const double bid, const double ask, double &px)
{
   if(bid <= 0.0 || ask <= 0.0) return false;
   if((ask - bid) > InpMaxSpreadUSD) return false;
   px = (bid + ask) / 2.0;
   if(InpTickPriceMode == 1) px = bid;
   else if(InpTickPriceMode == 2) px = ask;
   return true;
}

//--- Each accepted tick contributes weight 1.0 at its price, and feeds the
//    initial balance while it is still inside the IB window.
//
//    IB is standard RANGE-based Initial Balance. It is NOT Fabio Valentini's
//    IVB, whose rule incorporates volume and is not recoverable from the
//    course material -- do not relabel it as IVB anywhere.
void AddTickToProfile(SessionProfile &p, const long time_msc, const double px)
{
   if(p.accepted == 0) p.open_price = px;
   AddToRow(p, RowIndex(px), 1.0);
   p.accepted++;

   long ib_end_msc = ((long)p.start + (long)InpIBMinutes * 60) * 1000;
   if(time_msc < ib_end_msc)
   {
      if(!p.has_ib) { p.ib_low = px; p.ib_high = px; p.has_ib = true; }
      else
      {
         if(px < p.ib_low)  p.ib_low  = px;
         if(px > p.ib_high) p.ib_high = px;
      }
   }
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
   p.retries = 0;      // the limit counts CONSECUTIVE failures, not lifetime ones
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
      double px;
      if(!TickPrice(ticks[i].bid, ticks[i].ask, px)) { p.rejected++; continue; }
      AddTickToProfile(p, ticks[i].time_msc, px);
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


//--- Fallback accumulation from M1 bars, used only when tick history never
//    arrives. Each bar's tick_volume is spread UNIFORMLY across the rows its
//    high-low spans -- the standard, and reproducible. No OHLC weighting.
//    Mirrors accumulate_m1_bars() in Python.
void AccumulateM1Bars(SessionProfile &p, const datetime from, const datetime to)
{
   MqlRates r[];
   int n = CopyRates(_Symbol, PERIOD_M1, from, to, r);
   if(n <= 0) return;
   for(int i = 0; i < n; i++)
   {
      int r0 = RowIndex(r[i].low);
      int r1 = RowIndex(r[i].high);
      int rows = r1 - r0 + 1;
      if(rows <= 0) continue;
      double w = (double)r[i].tick_volume / rows;
      for(int k = r0; k <= r1; k++) AddToRow(p, k, w);
      if(p.accepted == 0) p.open_price = r[i].open;
      p.accepted++;

      if(r[i].time < from + InpIBMinutes * 60)
      {
         if(!p.has_ib) { p.ib_low = r[i].low; p.ib_high = r[i].high; p.has_ib = true; }
         else
         {
            if(r[i].low  < p.ib_low)  p.ib_low  = r[i].low;
            if(r[i].high > p.ib_high) p.ib_high = r[i].high;
         }
      }
   }
   p.source = SRC_M1;
}

//--- Context classifiers. Strings are the Python spellings verbatim: the
//    parity harness compares them literally, so "OUT OF BALANCE UP" with
//    spaces would read as a genuine disagreement between the two ports.
int ClassifyShape(SessionProfile &p)
{
   int occupied = 0;
   for(int i = 0; i < ArraySize(p.volumes); i++) if(p.volumes[i] > 0.0) occupied++;
   if(!p.has_skew || occupied < InpMinRowsForShape) return SHAPE_UNKNOWN;
   if(p.skew <= -InpSkewThreshold) return SHAPE_P;
   if(p.skew >=  InpSkewThreshold) return SHAPE_B;
   return SHAPE_D;
}

//--- Value-area boundaries are INCLUSIVE: an open exactly on VAH or VAL is
//    inside value. Range boundaries are exclusive, so an open exactly on the
//    prior high is above value but not above range.
string ClassifyOpenType(const double open_price, const SessionProfile &prior)
{
   if(open_price > prior.high) return "OPEN_ABOVE_RANGE";
   if(open_price < prior.low)  return "OPEN_BELOW_RANGE";
   if(open_price > prior.vah)  return "OPEN_ABOVE_VA";
   if(open_price < prior.val)  return "OPEN_BELOW_VA";
   return "OPEN_INSIDE_VA";
}

string ClassifyValueMigration(const SessionProfile &today, const SessionProfile &prior)
{
   // Containment BEFORE direction, so inside/engulfing days are never
   // mislabelled as drift.
   if(today.val >= prior.val && today.vah <= prior.vah) return "INSIDE";
   if(today.val <= prior.val && today.vah >= prior.vah) return "ENGULFING";
   if(today.val >  prior.vah) return "HIGHER";
   if(today.vah <  prior.val) return "LOWER";
   return (today.vah > prior.vah) ? "OVERLAPPING_HIGHER" : "OVERLAPPING_LOWER";
}

string ClassifyRegime(const int shape, const double elapsed_pct, const bool is_developing)
{
   // Every session looks like a P or a b before it has traded both ways.
   if(is_developing && elapsed_pct < InpRegimeMinElapsed) return "FORMING";
   switch(shape)
   {
      case SHAPE_D: return "BALANCED";
      case SHAPE_P: return "OUT_OF_BALANCE_UP";
      case SHAPE_B: return "OUT_OF_BALANCE_DOWN";
   }
   return "UNCLEAR";
}

//--- HVN / LVN. Mirrors find_nodes() in Python.
//
//    ⚠️ The three thresholds are UNCALIBRATED display heuristics. Unlike the
//    skew threshold, nothing was fitted to produce them. Do not build a
//    trading rule on them without the full backtest.md gate.
void FindNodes(const SessionProfile &p, double &hvn[], double &lvn[])
{
   ArrayResize(hvn, 0);
   ArrayResize(lvn, 0);
   int n = ArraySize(p.volumes);
   if(n < 3) return;

   double vmax = 0.0;
   for(int i = 0; i < n; i++) if(p.volumes[i] > vmax) vmax = p.volumes[i];
   double floor_v = vmax * InpHVNProminencePct;

   // Local maxima above the prominence floor, greedily thinned by separation
   // in descending volume order -- the tallest peak in a cluster wins.
   int cands[];
   for(int i = 1; i < n - 1; i++)
      if(p.volumes[i] >= p.volumes[i - 1] && p.volumes[i] > p.volumes[i + 1]
         && p.volumes[i] >= floor_v)
      {
         int m = ArraySize(cands);
         ArrayResize(cands, m + 1);
         cands[m] = i;
      }
   // Descending volume, and STABLE on ties. Candidates arrive in ascending row
   // order, so a stable sort leaves equal-volume peaks lowest-row-first, which
   // is what the greedy filter below then keeps. Python's sort is stable; a
   // swap sort is not, and would silently pick the other peak of every tie.
   int nc = ArraySize(cands);
   for(int a = 1; a < nc; a++)             // insertion sort: shift only on strictly greater
   {
      int key = cands[a];
      int b = a - 1;
      while(b >= 0 && p.volumes[cands[b]] < p.volumes[key])
      { cands[b + 1] = cands[b]; b--; }
      cands[b + 1] = key;
   }

   int kept[];
   for(int a = 0; a < nc; a++)
   {
      bool ok = true;
      for(int b = 0; b < ArraySize(kept); b++)
         if(MathAbs(cands[a] - kept[b]) < InpNodeMinSepRows) { ok = false; break; }
      if(!ok) continue;
      int m = ArraySize(kept);
      ArrayResize(kept, m + 1);
      kept[m] = cands[a];
   }
   ArraySort(kept);

   int nk = ArraySize(kept);
   ArrayResize(hvn, nk);
   for(int a = 0; a < nk; a++) hvn[a] = RowMid(p.min_row + kept[a]);

   // An LVN is the thin gap BETWEEN two adjacent peaks -- where the auction
   // left a hole. The course material treats these as the absorption zones.
   for(int a = 0; a + 1 < nk; a++)
   {
      int lo = kept[a], hi = kept[a + 1];
      if(hi - lo < 2) continue;
      int j = lo + 1;
      for(int k = lo + 1; k < hi; k++) if(p.volumes[k] < p.volumes[j]) j = k;
      double lim = InpLVNRatio * MathMin(p.volumes[lo], p.volumes[hi]);
      if(p.volumes[j] <= lim)
      {
         int m = ArraySize(lvn);
         ArrayResize(lvn, m + 1);
         lvn[m] = RowMid(p.min_row + j);
      }
   }
}

//--- Composite: sum session histograms onto the shared absolute grid. Nearly
//    free -- no ticks are re-read. It works ONLY because the grid is absolute;
//    session-anchored bins could not be summed like this.
//
//    A session that accepted nothing carries min_row = 0 and must stay out of
//    the grid maths or it drags the composite's floor to row 0. Its counts are
//    still summed: a fully spread-filtered session is exactly what the reader
//    most needs to see.
void BuildComposite(SessionProfile &out, const SessionProfile &src[], const int count)
{
   ArrayResize(out.volumes, 0);
   out.min_row = 0;
   out.accepted = 0;
   out.rejected = 0;
   out.has_ib = false;
   out.source = SRC_TICK;

   int first = MathMax(0, count - InpCompositeDays);
   for(int s = first; s < count; s++)
   {
      out.accepted += src[s].accepted;
      out.rejected += src[s].rejected;
      for(int i = 0; i < ArraySize(src[s].volumes); i++)
         if(src[s].volumes[i] > 0.0) AddToRow(out, src[s].min_row + i, src[s].volumes[i]);
   }
   if(count > first)
   {
      out.start = src[first].start;
      out.end   = src[count - 1].end;
   }
   ResolveProfile(out);
   out.shape = ClassifyShape(out);
}

//--- Session boundaries ---------------------------------------------
//    Default is the broker's own D1 boundary, so the profile matches the
//    daily candles on the chart. The override is a UTC hour, converted
//    through the live server-GMT offset rather than assumed to be zero.
int ServerGMTOffsetSec()
{
   return (int)(TimeCurrent() - TimeGMT());
}

datetime SessionStartFor(const datetime t)
{
   if(InpSessionUTCOverride < 0)
   {
      MqlDateTime d;
      TimeToStruct(t, d);
      d.hour = 0; d.min = 0; d.sec = 0;
      return StructToTime(d);
   }
   int off = ServerGMTOffsetSec();
   datetime gmt = t - off;
   MqlDateTime d;
   TimeToStruct(gmt, d);
   d.hour = 0; d.min = 0; d.sec = 0;
   datetime start_gmt = StructToTime(d) + InpSessionUTCOverride * 3600;
   if(start_gmt > gmt) start_gmt -= 86400;
   return start_gmt + off;
}

//--- Build one completed session. Ticks first; M1 only if the tick history
//    genuinely is not there. A session that never cleared the minimum tick
//    count is left invalid rather than drawn at reduced confidence.
//
//    Returns false when the tick history is still SYNCHRONISING, so the caller
//    can retry instead of accepting M1. CopyTicksRange returning 0 is a real
//    answer ("no ticks in this range" -- a weekend); returning -1 is not an
//    answer at all, and treating the two alike would pin every completed
//    session to M1 fidelity permanently, since history is built only once.
bool BuildSession(SessionProfile &p, const datetime from, const datetime to, const bool allow_m1)
{
   ArrayResize(p.volumes, 0);
   p.start = from; p.end = to;
   p.min_row = 0; p.accepted = 0; p.rejected = 0;
   p.retries = 0; p.valid = false; p.has_ib = false; p.has_skew = false;
   p.shape = SHAPE_UNKNOWN; p.open_price = 0.0;
   p.cursor_msc = (ulong)from * 1000;
   p.source = SRC_PENDING;

   MqlTick ticks[];
   int n = CopyTicksRange(_Symbol, ticks, COPY_TICKS_ALL,
                          (ulong)from * 1000, (ulong)to * 1000 - 1);
   if(n < 0 && !allow_m1) return false;      // still syncing -- ask to be retried

   if(n > 0)
   {
      for(int i = 0; i < n; i++)
      {
         double px;
         if(!TickPrice(ticks[i].bid, ticks[i].ask, px)) { p.rejected++; continue; }
         AddTickToProfile(p, ticks[i].time_msc, px);
      }
      p.source = SRC_TICK;
   }
   else
   {
      AccumulateM1Bars(p, from, to);
   }

   if(p.accepted < InpMinSessionTicks) return true;   // built, just too thin to use
   ResolveProfile(p);
   p.shape = ClassifyShape(p);
   return true;
}

//--- Naked POCs: session POCs no LATER bar has traded through. These are the
//    left-side levels the methodology uses to judge whether a setup's
//    risk-to-reward is viable. A bar whose [low, high] contains the price
//    tags it; touching an extreme exactly counts as a tag.
bool IsNakedPOC(const double price, const datetime after)
{
   double h[], l[];
   int n = CopyHigh(_Symbol, PERIOD_M15, after, TimeCurrent(), h);
   if(n <= 0) return true;
   if(CopyLow(_Symbol, PERIOD_M15, after, TimeCurrent(), l) != n) return true;
   for(int i = 0; i < n; i++) if(l[i] <= price && h[i] >= price) return false;
   return true;
}

//--- Rendering -------------------------------------------------------
int g_objects = 0;

bool BudgetOK()
{
   if(g_objects < InpMaxObjects) return true;
   static bool warned = false;
   if(!warned)
   {
      PrintFormat("%s object budget %d reached -- suppressing further drawing",
                  PFX, InpMaxObjects);
      warned = true;
   }
   return false;
}

void DrawLevel(const string id, const datetime t0, const datetime t1,
               const double price, const color clr, const int width,
               const ENUM_LINE_STYLE style, const string label)
{
   if(!BudgetOK()) return;
   string name = PFX + id;
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_TREND, 0, t0, price, t1, price);
      g_objects++;
   }
   ObjectMove(0, name, 0, t0, price);
   ObjectMove(0, name, 1, t1, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);

   if(label == "") return;
   string lname = PFX + id + "_lbl";
   if(ObjectFind(0, lname) < 0)
   {
      ObjectCreate(0, lname, OBJ_TEXT, 0, t1, price);
      g_objects++;
   }
   ObjectMove(0, lname, 0, t1, price);
   ObjectSetString(0, lname, OBJPROP_TEXT, label);
   ObjectSetInteger(0, lname, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, lname, OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, lname, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, lname, OBJPROP_SELECTABLE, false);
}

//--- Histogram rows grow rightward from the session's left edge and never
//    overflow into the next session. Only the most recent
//    InpHistogramProfiles sessions get one: ~400 rows/session means ten
//    sessions would be 4,000 objects and the chart would crawl.
void DrawHistogram(const SessionProfile &p, const int seq)
{
   double vmax = 0.0;
   for(int i = 0; i < ArraySize(p.volumes); i++) if(p.volumes[i] > vmax) vmax = p.volumes[i];
   if(vmax <= 0.0) return;
   long span = (long)(p.end - p.start);

   for(int i = 0; i < ArraySize(p.volumes); i++)
   {
      if(p.volumes[i] <= 0.0) continue;
      if(!BudgetOK()) return;
      int row = p.min_row + i;
      bool inVA = (row >= p.val_row && row <= p.vah_row);
      double frac = p.volumes[i] / vmax;
      datetime t1 = p.start + (datetime)(long)(span * frac * InpHistogramWidthPct);

      string name = StringFormat("%sHIST_%d_%d", PFX, seq, row);
      if(ObjectFind(0, name) < 0)
      {
         ObjectCreate(0, name, OBJ_RECTANGLE, 0, p.start, RowLow(row), t1, RowHigh(row));
         g_objects++;
      }
      ObjectMove(0, name, 0, p.start, RowLow(row));
      ObjectMove(0, name, 1, t1, RowHigh(row));
      ObjectSetInteger(0, name, OBJPROP_COLOR, inVA ? clrSteelBlue : clrDimGray);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   }
}

void DrawSessionLevels(const SessionProfile &p, const int seq, const bool developing)
{
   if(!p.valid) return;
   string tag = StringFormat("S%d", seq);
   datetime t1 = developing ? TimeCurrent() : p.end;
   DrawLevel(tag + "_POC", p.start, t1, p.vpoc, InpVPOCColor, 2, STYLE_SOLID,
             developing ? "VPOC" : "");
   DrawLevel(tag + "_VAH", p.start, t1, p.vah, InpVAHColor, 1, STYLE_DOT,
             developing ? "VAH" : "");
   DrawLevel(tag + "_VAL", p.start, t1, p.val, InpVALColor, 1, STYLE_DOT,
             developing ? "VAL" : "");
}

void DrawNodes(const SessionProfile &p, const datetime t1)
{
   if(!p.valid || (!InpShowLVN && !InpShowHVN)) return;
   double hvn[], lvn[];
   FindNodes(p, hvn, lvn);
   if(InpShowHVN)
      for(int i = 0; i < ArraySize(hvn); i++)
         DrawLevel(StringFormat("HVN_%d", i), p.start, t1, hvn[i],
                   InpHVNColor, 1, STYLE_DASHDOT, "HVN");
   if(InpShowLVN)
      for(int i = 0; i < ArraySize(lvn); i++)
         DrawLevel(StringFormat("LVN_%d", i), p.start, t1, lvn[i],
                   InpLVNColor, 1, STYLE_DASH, "LVN");
}

//--- The panel's honesty markers -- source, skew values and the
//    UNCALIBRATED warning -- are NOT suppressible. A letter is never shown
//    without its skew value beside it.
//    That claim is only true if the markers are VISIBLE: MT5's one-click
//    trading widget is on by default and covers the top ~60px of the upper-left
//    corner, which is exactly where the header and the UNCALIBRATED warning sit.
//    So the origin drops below it while that widget is showing, and YDISTANCE is
//    re-applied on every call so toggling one-click trading moves the panel.
void PanelLine(const int idx, const string text, const color clr)
{
   int y0 = ChartGetInteger(0, CHART_SHOW_ONE_CLICK) ? 78 : 18;
   string name = StringFormat("%sPANEL_%d", PFX, idx);
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      g_objects++;
   }
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y0 + idx * 13);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
}

void DrawPanel(SessionProfile &dev, SessionProfile &prior, SessionProfile &comp,
               const double elapsed_pct)
{
   if(!InpShowPanel) return;
   int r = 0;

   string calib = (InpSkewThreshold <= 0.0)
                  ? "!! UNCALIBRATED"
                  : StringFormat("skewT %.2f", InpSkewThreshold);
   PanelLine(r++, StringFormat("%s VOLUME PROFILE (tick density)  src %s  %s",
                               _Symbol, SourceName(dev.source), calib),
             (InpSkewThreshold <= 0.0) ? clrOrangeRed : clrSilver);

   PanelLine(r++, "- DEVELOPING ----------------------", clrDimGray);
   if(dev.valid)
   {
      string sk = dev.has_skew ? StringFormat("%+.2f", dev.skew) : "n/a";
      PanelLine(r++, StringFormat(" shape %-13s skew %s", ShapeName(dev.shape), sk), clrWhite);
      PanelLine(r++, StringFormat(" regime %s",
                                  ClassifyRegime(dev.shape, elapsed_pct, true)), clrWhite);
      if(prior.valid)
         PanelLine(r++, StringFormat(" open  %s", ClassifyOpenType(dev.open_price, prior)), clrWhite);
      PanelLine(r++, StringFormat(" VPOC %.2f  VAH %.2f  VAL %.2f",
                                  dev.vpoc, dev.vah, dev.val), clrWhite);
      if(dev.has_ib)
         PanelLine(r++, StringFormat(" IB   %.2f - %.2f (%dm)",
                                     dev.ib_low, dev.ib_high, InpIBMinutes), clrWhite);
   }

   if(prior.valid)
   {
      PanelLine(r++, "- PRIOR SESSION -------------------", clrDimGray);
      string sk = prior.has_skew ? StringFormat("%+.2f", prior.skew) : "n/a";
      string mig = dev.valid ? ClassifyValueMigration(dev, prior) : "n/a";
      PanelLine(r++, StringFormat(" shape %-4s skew %s  value %s",
                                  ShapeName(prior.shape), sk, mig), clrWhite);
      PanelLine(r++, StringFormat(" VPOC %.2f  VAH %.2f  VAL %.2f",
                                  prior.vpoc, prior.vah, prior.val), clrWhite);
   }

   if(comp.valid)
   {
      PanelLine(r++, StringFormat("- COMPOSITE %dd -------------------", InpCompositeDays), clrDimGray);
      string sk = comp.has_skew ? StringFormat("%+.2f", comp.skew) : "n/a";
      PanelLine(r++, StringFormat(" shape %-4s skew %s", ShapeName(comp.shape), sk), clrWhite);
      PanelLine(r++, StringFormat(" VPOC %.2f  VAH %.2f  VAL %.2f",
                                  comp.vpoc, comp.vah, comp.val), clrWhite);
   }

   PanelLine(r++, StringFormat(" ticks %d  rejected %d  elapsed %.0f%%",
                               dev.accepted, dev.rejected, elapsed_pct * 100.0), clrSilver);
}

//--- Alerts ----------------------------------------------------------
//    A touch is defined at TICK level: the current bid crosses the level
//    since the previous tick. Bar-based detection would miss intrabar tags,
//    which is exactly the case that matters live.
double g_last_bid = 0.0;

struct AlertState { double price; bool fired; bool armed; };
AlertState g_alerts[16];
int        g_alert_count = 0;

void ArmAlert(const double price)
{
   if(g_alert_count >= 16) return;
   // Re-arming an existing level would wipe its fired flag and let it
   // machine-gun across refreshes, so a duplicate price is left alone.
   for(int i = 0; i < g_alert_count; i++)
      if(MathAbs(g_alerts[i].price - price) < 1e-8) return;
   g_alerts[g_alert_count].price = price;
   g_alerts[g_alert_count].fired = false;
   g_alerts[g_alert_count].armed = true;
   g_alert_count++;
}

//--- Rebuild the watch list, carrying the fired/armed state of any level that
//    survives. Levels move as the session develops; without the carry-over
//    every refresh would re-arm everything.
void RearmLevels(SessionProfile &dev, SessionProfile &prior, const double &naked[])
{
   AlertState old[16];
   int old_n = g_alert_count;
   for(int i = 0; i < old_n; i++) old[i] = g_alerts[i];
   g_alert_count = 0;

   if(prior.valid) { ArmAlert(prior.vpoc); ArmAlert(prior.vah); ArmAlert(prior.val); }
   if(InpAlertDeveloping && dev.valid) { ArmAlert(dev.vah); ArmAlert(dev.val); }
   for(int i = 0; i < ArraySize(naked); i++) ArmAlert(naked[i]);
   if(InpAlertLVN && dev.valid)
   {
      double hvn[], lvn[];
      FindNodes(dev, hvn, lvn);
      for(int i = 0; i < ArraySize(lvn); i++) ArmAlert(lvn[i]);
   }

   for(int i = 0; i < g_alert_count; i++)
      for(int j = 0; j < old_n; j++)
         if(MathAbs(old[j].price - g_alerts[i].price) < 1e-8)
         { g_alerts[i].fired = old[j].fired; g_alerts[i].armed = old[j].armed; break; }
}

void CheckAlerts(const string context)
{
   if(!InpAlertsOn) return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(g_last_bid <= 0.0) { g_last_bid = bid; return; }

   for(int i = 0; i < g_alert_count; i++)
   {
      double lvl = g_alerts[i].price;
      bool crossed = (g_last_bid < lvl && bid >= lvl) || (g_last_bid > lvl && bid <= lvl);

      // Re-arm only once price has left by the band, so a level cannot
      // machine-gun while price grinds along it.
      if(g_alerts[i].fired && MathAbs(bid - lvl) >= InpAlertRearmUSD)
         { g_alerts[i].fired = false; g_alerts[i].armed = true; }

      if(crossed && g_alerts[i].armed && !g_alerts[i].fired)
      {
         string msg = StringFormat("%s %s touched %.2f", _Symbol, context, lvl);
         Alert(msg);
         if(InpSendNotifications) SendNotification(msg);
         g_alerts[i].fired = true;
         g_alerts[i].armed = false;
      }
   }
   g_last_bid = bid;
}

//--- Lifecycle -------------------------------------------------------
SessionProfile g_dev;
SessionProfile g_done[];      // completed sessions, oldest .. newest
SessionProfile g_comp;
double         g_naked[];
bool           g_history_built = false;

int g_history_retries = 0;

void BuildHistory()
{
   datetime today = SessionStartFor(TimeCurrent());
   int want = MathMax(InpProfileDays, InpCompositeDays);
   bool allow_m1 = (g_history_retries >= InpTickRetryLimit);
   ArrayResize(g_done, 0);

   for(int back = want; back >= 1; back--)
   {
      datetime from = today - (datetime)back * 86400;
      datetime to   = from + 86400;
      SessionProfile p;
      if(!BuildSession(p, from, to, allow_m1))
      {
         g_history_retries++;
         ArrayResize(g_done, 0);
         PrintFormat("%s tick history still syncing, deferring history build "
                     "(retry %d/%d)", PFX, g_history_retries, InpTickRetryLimit);
         return;                       // leaves g_history_built false -- retried next timer
      }
      if(!p.valid) continue;
      int m = ArraySize(g_done);
      ArrayResize(g_done, m + 1);
      g_done[m] = p;
   }
   if(allow_m1)
      PrintFormat("%s tick history unavailable after %d retries -- completed "
                  "sessions built from M1 (reduced fidelity)", PFX, g_history_retries);

   ArrayResize(g_naked, 0);
   for(int i = 0; i < ArraySize(g_done); i++)
      if(IsNakedPOC(g_done[i].vpoc, g_done[i].end))
      {
         int m = ArraySize(g_naked);
         ArrayResize(g_naked, m + 1);
         g_naked[m] = g_done[i].vpoc;
      }

   BuildComposite(g_comp, g_done, ArraySize(g_done));
   g_history_built = (ArraySize(g_done) > 0);
   PrintFormat("%s history: %d sessions, %d naked POCs, composite %s",
               PFX, ArraySize(g_done), ArraySize(g_naked),
               g_comp.valid ? "ok" : "empty");
}

void StartDeveloping(const datetime from)
{
   ArrayResize(g_dev.volumes, 0);
   g_dev.start = from;
   g_dev.end = from + 86400;
   g_dev.min_row = 0; g_dev.accepted = 0; g_dev.rejected = 0;
   g_dev.retries = 0; g_dev.valid = false; g_dev.has_ib = false;
   g_dev.has_skew = false; g_dev.shape = SHAPE_UNKNOWN; g_dev.open_price = 0.0;
   g_dev.cursor_msc = (ulong)from * 1000;
   g_dev.source = SRC_PENDING;
}

int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "GoldenChart VolumeProfile");
   if(InpRowSize <= 0.0)
   {
      Print(PFX + "InpRowSize must be positive");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpSkewThreshold <= 0.0)
      Print(PFX + "InpSkewThreshold is 0 -- shape labels are UNCALIBRATED. "
                  "See reports/volume_profile_shape_calibration.md");
   StartDeveloping(SessionStartFor(TimeCurrent()));
   EventSetTimer(MathMax(1, InpRefreshSec));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, PFX);
   g_objects = 0;
}

void OnTimer()
{
   if(!g_history_built) BuildHistory();

   // Rollover: freeze the finished session into the cache and start clean.
   datetime cur_start = SessionStartFor(TimeCurrent());
   if(cur_start != g_dev.start)
   {
      if(g_dev.valid && g_dev.accepted >= InpMinSessionTicks)
      {
         int m = ArraySize(g_done);
         ArrayResize(g_done, m + 1);
         g_done[m] = g_dev;
         BuildComposite(g_comp, g_done, ArraySize(g_done));
      }
      ObjectsDeleteAll(0, PFX);
      g_objects = 0;
      StartDeveloping(cur_start);
   }

   // Accumulate. A negative return means history is still syncing -- it must
   // NOT be read as "no ticks", which would pin the session to M1 for good.
   if(g_dev.source != SRC_M1)
   {
      int added = AccumulateTicksIncremental(g_dev, TimeCurrent());
      if(added < 0)
      {
         if(g_dev.retries >= InpTickRetryLimit)
         {
            PrintFormat("%s tick history unavailable after %d retries -- "
                        "falling back to M1 (reduced fidelity)", PFX, g_dev.retries);
            AccumulateM1Bars(g_dev, g_dev.start, TimeCurrent());
         }
      }
      else if(g_dev.source == SRC_PENDING && g_dev.accepted > 0)
      {
         g_dev.source = SRC_TICK;
      }
   }

   ResolveProfile(g_dev);
   g_dev.shape = ClassifyShape(g_dev);

   // Draw: completed sessions newest-first so the budget, if it bites, eats
   // the oldest rather than the ones being traded off.
   int n_done = ArraySize(g_done);
   int shown = MathMin(InpProfileDays, n_done);
   for(int i = 0; i < shown; i++)
   {
      int idx = n_done - 1 - i;
      DrawSessionLevels(g_done[idx], idx, false);
      if(i < InpHistogramProfiles) DrawHistogram(g_done[idx], idx);
   }
   DrawSessionLevels(g_dev, 999, true);
   if(InpHistogramProfiles > 0) DrawHistogram(g_dev, 999);
   DrawNodes(g_dev, TimeCurrent());

   for(int i = 0; i < ArraySize(g_naked); i++)
      DrawLevel(StringFormat("NAKED_%d", i), g_dev.start - 86400 * 3, TimeCurrent(),
                g_naked[i], InpNakedPOCColor, 1, STYLE_SOLID, "nPOC");

   if(g_comp.valid)
   {
      DrawLevel("COMP_POC", g_comp.start, TimeCurrent(), g_comp.vpoc,
                InpCompositeColor, 2, STYLE_SOLID, "cPOC");
      DrawLevel("COMP_VAH", g_comp.start, TimeCurrent(), g_comp.vah,
                InpCompositeColor, 1, STYLE_DOT, "cVAH");
      DrawLevel("COMP_VAL", g_comp.start, TimeCurrent(), g_comp.val,
                InpCompositeColor, 1, STYLE_DOT, "cVAL");
   }
   if(g_dev.has_ib)
   {
      DrawLevel("IB_HI", g_dev.start, TimeCurrent(), g_dev.ib_high,
                InpIBColor, 1, STYLE_DASH, "IBH");
      DrawLevel("IB_LO", g_dev.start, TimeCurrent(), g_dev.ib_low,
                InpIBColor, 1, STYLE_DASH, "IBL");
   }

   double elapsed = (double)(TimeCurrent() - g_dev.start) / 86400.0;
   if(elapsed > 1.0) elapsed = 1.0;
   SessionProfile prior;
   bool has_prior = (n_done > 0);
   if(has_prior) prior = g_done[n_done - 1];
   else          prior.valid = false;
   DrawPanel(g_dev, prior, g_comp, elapsed);

   RearmLevels(g_dev, prior, g_naked);

   if(InpExportCSV)
   {
      SessionProfile all[];
      ArrayResize(all, n_done + 1);
      for(int i = 0; i < n_done; i++) all[i] = g_done[i];
      all[n_done] = g_dev;
      ExportParityCSV(all, n_done + 1, elapsed);
   }

   ChartRedraw(0);
}

//--- Parity export ---------------------------------------------------
//    Export the indicator's OWN histogram plus every value it derived from
//    it. Python then recomputes those values from this exact histogram and
//    requires an exact match. That is what pins the algorithm layer; the raw
//    tick feeds differ between broker and Dukascopy and can never be
//    compared, so a cross-vendor test would pass or fail for reasons that
//    have nothing to do with correctness.
//
//    Everything Task 10 ported must appear here. A classifier that crosses
//    the language boundary untested is exactly the silent drift CLAUDE.md
//    calls non-optional to guard against.
void ExportParityCSV(const SessionProfile &profiles[], const int count,
                     const double dev_elapsed)
{
   int fh = FileOpen("vp_histogram.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) { PrintFormat("%s export failed: %d", PFX, GetLastError()); return; }
   FileWrite(fh, "session", "row", "volume");
   for(int s = 0; s < count; s++)
   {
      if(!profiles[s].valid) continue;
      string tag = TimeToString(profiles[s].start, TIME_DATE);
      for(int i = 0; i < ArraySize(profiles[s].volumes); i++)
         if(profiles[s].volumes[i] > 0.0)
            FileWrite(fh, tag, profiles[s].min_row + i,
                      DoubleToString(profiles[s].volumes[i], 8));
   }
   FileClose(fh);

   fh = FileOpen("vp_levels.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) return;
   FileWrite(fh, "session", "row_size", "value_area_pct", "va_algorithm",
             "skew_threshold", "min_rows_for_shape", "regime_min_elapsed_pct",
             "hvn_prominence_pct", "lvn_ratio", "node_min_sep_rows",
             "vpoc", "vah", "val", "low", "high", "skew", "shape",
             "open", "elapsed_pct", "is_developing",
             "open_type", "value_migration", "regime");
   int prev = -1;
   for(int s = 0; s < count; s++)
   {
      if(!profiles[s].valid) continue;
      bool developing = (s == count - 1);
      double elapsed = developing ? dev_elapsed : 1.0;

      // No prior session means open type and value migration are UNDEFINED,
      // not neutral. Export them empty so the checker skips rather than
      // compares against a default nobody computed.
      string otype = "", mig = "";
      if(prev >= 0)
      {
         otype = ClassifyOpenType(profiles[s].open_price, profiles[prev]);
         mig   = ClassifyValueMigration(profiles[s], profiles[prev]);
      }

      FileWrite(fh, TimeToString(profiles[s].start, TIME_DATE),
                DoubleToString(InpRowSize, 8), DoubleToString(InpValueAreaPct, 8),
                IntegerToString(InpVAAlgorithm), DoubleToString(InpSkewThreshold, 8),
                IntegerToString(InpMinRowsForShape),
                DoubleToString(InpRegimeMinElapsed, 8),
                DoubleToString(InpHVNProminencePct, 8), DoubleToString(InpLVNRatio, 8),
                IntegerToString(InpNodeMinSepRows),
                DoubleToString(profiles[s].vpoc, 8), DoubleToString(profiles[s].vah, 8),
                DoubleToString(profiles[s].val, 8), DoubleToString(profiles[s].low, 8),
                DoubleToString(profiles[s].high, 8),
                profiles[s].has_skew ? DoubleToString(profiles[s].skew, 8) : "",
                ShapeName(profiles[s].shape),
                DoubleToString(profiles[s].open_price, 8),
                DoubleToString(elapsed, 8),
                developing ? "1" : "0",
                otype, mig,
                ClassifyRegime(profiles[s].shape, elapsed, developing));
      prev = s;
   }
   FileClose(fh);

   fh = FileOpen("vp_nodes.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) return;
   FileWrite(fh, "session", "kind", "price");
   for(int s = 0; s < count; s++)
   {
      if(!profiles[s].valid) continue;
      string tag = TimeToString(profiles[s].start, TIME_DATE);
      double hvn[], lvn[];
      FindNodes(profiles[s], hvn, lvn);
      for(int i = 0; i < ArraySize(hvn); i++)
         FileWrite(fh, tag, "HVN", DoubleToString(hvn[i], 8));
      for(int i = 0; i < ArraySize(lvn); i++)
         FileWrite(fh, tag, "LVN", DoubleToString(lvn[i], 8));
   }
   FileClose(fh);
   PrintFormat("%s parity CSVs written to the Files directory (%d sessions)", PFX, count);
}

//--- Alerts run on the tick stream, not the timer: a level tagged and left
//    inside one refresh interval still fires.
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[],
                const double &high[], const double &low[],
                const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
{
   CheckAlerts("profile level");
   return rates_total;
}
