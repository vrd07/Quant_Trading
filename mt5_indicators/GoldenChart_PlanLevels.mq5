//+------------------------------------------------------------------+
//|                                     GoldenChart_PlanLevels.mq5    |
//|     Draws PLANNED entry/SL/TP from the Python trading system,      |
//|     read from mt5_chart_signals.csv in the MT5 Common Files dir.   |
//|     Dotted lines (distinct from the solid-dashed LIVE markers).    |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//--- Inputs --------------------------------------------------------
input string InpFile        = "mt5_chart_signals.csv"; // File in MT5 Common\\Files
input color  InpEntryColor  = clrDodgerBlue;  // Planned entry
input color  InpTPColor     = clrMagenta;     // Planned TP
input color  InpSLColor     = clrRed;         // Planned SL
input int    InpLineWidth   = 1;              // Line width
input bool   InpShowLabels  = true;           // Show "PLAN ..." text
input bool   InpAllSymbols  = false;          // Draw plans for ALL symbols (else only this chart's)
input int    InpRefreshSec  = 2;              // Poll interval (seconds)

const string PFX = "GC_PLAN_";

//+------------------------------------------------------------------+
int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "GoldenChart Plan Levels");
   EventSetTimer(MathMax(1, InpRefreshSec));
   RefreshPlans();
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, PFX);
   ChartRedraw();
}

//+------------------------------------------------------------------+
void OnTimer()
{
   RefreshPlans();
}

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   RefreshPlans();
   return(rates_total);
}

//+------------------------------------------------------------------+
void DrawLevel(const string id, double price, color clr, const string label)
{
   if(price <= 0.0) return;
   string name = PFX + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble (0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, InpLineWidth);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   string txt = label + " " + DoubleToString(price, _Digits);
   ObjectSetString (0, name, OBJPROP_TEXT, InpShowLabels ? txt : "");
   ObjectSetString (0, name, OBJPROP_TOOLTIP, txt);
}

//+------------------------------------------------------------------+
//| Read the CSV and (re)draw planned levels                         |
//+------------------------------------------------------------------+
void RefreshPlans()
{
   ObjectsDeleteAll(0, PFX);

   int h = FileOpen(InpFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)   // no file yet → nothing planned
   {
      ChartRedraw();
      return;
   }

   ushort sep = StringGetCharacter(",", 0);
   long   now = (long)TimeGMT();
   int    idx = 0;

   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      if(StringLen(line) < 5) continue;

      string p[];
      int n = StringSplit(line, sep, p);
      if(n < 7) continue;
      if(p[0] == "symbol") continue;                       // header
      if(!InpAllSymbols && p[0] != _Symbol) continue;      // other symbol

      long exp = (long)StringToInteger(p[6]);
      if(exp > 0 && exp <= now) continue;                  // expired plan

      string side  = p[1];
      double entry = StringToDouble(p[2]);
      double sl    = StringToDouble(p[3]);
      double tp    = StringToDouble(p[4]);
      string lbl   = p[5];

      string tag = StringFormat("%d", idx);
      DrawLevel(tag + "_E", entry, InpEntryColor, "PLAN " + side + " " + lbl);
      DrawLevel(tag + "_S", sl,    InpSLColor,    "PLAN SL");
      DrawLevel(tag + "_T", tp,    InpTPColor,    "PLAN TP");
      idx++;
   }

   FileClose(h);
   ChartRedraw();
}
//+------------------------------------------------------------------+
