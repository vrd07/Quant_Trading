//+------------------------------------------------------------------+
//|                                         GoldenChart_Levels.mq5    |
//|     Marks live ENTRY / TP / SL of open positions and pending      |
//|     orders on the chart symbol as dashed HLINEs with price tags.  |
//|     Entry = black, TP = magenta, SL = red  (matches /volume pics) |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "2.00"
#property strict
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//--- Inputs --------------------------------------------------------
input color  InpEntryColor   = clrBlack;     // Entry / open price line
input color  InpTPColor      = clrMagenta;   // Take-profit line
input color  InpSLColor      = clrRed;       // Stop-loss line
input int    InpLineWidth    = 2;            // Line width
input ENUM_LINE_STYLE InpStyle = STYLE_DASH; // Line style
input bool   InpShowPending  = true;         // Also mark pending orders
input bool   InpShowLabels   = true;         // Show "TP/ENTRY/SL @price" text
input int    InpRefreshSec   = 1;            // Refresh interval (seconds)

const string PFX = "GC_TRD_";

//+------------------------------------------------------------------+
int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "GoldenChart Trade Levels");
   EventSetTimer(MathMax(1, InpRefreshSec));
   RefreshLevels();
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
   RefreshLevels();
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
   RefreshLevels();
   return(rates_total);
}

//+------------------------------------------------------------------+
//| Draw / update one horizontal level line                          |
//+------------------------------------------------------------------+
void DrawLevel(const string id, double price, color clr, const string label)
{
   if(price <= 0.0) return;                 // 0 == no SL/TP set
   string name = PFX + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble (0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, InpStyle);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, InpLineWidth);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   string txt = label + " " + DoubleToString(price, _Digits);
   ObjectSetString (0, name, OBJPROP_TEXT, InpShowLabels ? txt : "");
   ObjectSetString (0, name, OBJPROP_TOOLTIP, txt);
}

//+------------------------------------------------------------------+
//| Rebuild all trade lines from live positions + pending orders     |
//+------------------------------------------------------------------+
void RefreshLevels()
{
   ObjectsDeleteAll(0, PFX);

   //--- open positions on this symbol --------------------------------
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double tp    = PositionGetDouble(POSITION_TP);
      double sl    = PositionGetDouble(POSITION_SL);
      string tk    = (string)ticket;

      DrawLevel("P" + tk + "_E", entry, InpEntryColor, "ENTRY");
      DrawLevel("P" + tk + "_T", tp,    InpTPColor,    "TP");
      DrawLevel("P" + tk + "_S", sl,    InpSLColor,    "SL");
   }

   //--- pending orders on this symbol --------------------------------
   if(InpShowPending)
   {
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         ulong ticket = OrderGetTicket(i);
         if(ticket == 0) continue;
         if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;

         double entry = OrderGetDouble(ORDER_PRICE_OPEN);
         double tp    = OrderGetDouble(ORDER_TP);
         double sl    = OrderGetDouble(ORDER_SL);
         string tk    = (string)ticket;

         DrawLevel("O" + tk + "_E", entry, InpEntryColor, "PENDING");
         DrawLevel("O" + tk + "_T", tp,    InpTPColor,    "TP");
         DrawLevel("O" + tk + "_S", sl,    InpSLColor,    "SL");
      }
   }

   ChartRedraw();
}
//+------------------------------------------------------------------+
