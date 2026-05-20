//+------------------------------------------------------------------+
//|                                           GoldenChart_MACD.mq5    |
//|     MACD(12, 26, 9) — histogram (green/red) + MACD & signal lines |
//|     in the TradingView layout (histogram = MACD - signal).        |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_buffers 4
#property indicator_plots   3

//--- Plot 0: histogram (MACD - signal), colored by sign
#property indicator_label1  "Hist"
#property indicator_type1   DRAW_COLOR_HISTOGRAM
#property indicator_color1  clrLimeGreen,clrRed
#property indicator_width1  2

//--- Plot 1: MACD line
#property indicator_label2  "MACD"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrRoyalBlue
#property indicator_style2  STYLE_SOLID
#property indicator_width2  2

//--- Plot 2: Signal line
#property indicator_label3  "Signal"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrOrangeRed
#property indicator_style3  STYLE_SOLID
#property indicator_width3  1

//--- Inputs --------------------------------------------------------
input int InpFast   = 12;   // Fast EMA
input int InpSlow   = 26;   // Slow EMA
input int InpSignal = 9;    // Signal SMA

double BufHist[];     // 0
double BufColor[];    // 1
double BufMACD[];     // 2
double BufSignal[];   // 3

int hMACD = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BufHist,   INDICATOR_DATA);
   SetIndexBuffer(1, BufColor,  INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2, BufMACD,   INDICATOR_DATA);
   SetIndexBuffer(3, BufSignal, INDICATOR_DATA);

   IndicatorSetInteger(INDICATOR_DIGITS, _Digits + 1);
   IndicatorSetString(INDICATOR_SHORTNAME,
      StringFormat("MACD(%d,%d,%d)", InpFast, InpSlow, InpSignal));
   IndicatorSetInteger(INDICATOR_LEVELS, 1);
   IndicatorSetDouble (INDICATOR_LEVELVALUE, 0, 0.0);
   IndicatorSetInteger(INDICATOR_LEVELSTYLE, 0, STYLE_DOT);
   IndicatorSetInteger(INDICATOR_LEVELCOLOR, 0, clrSilver);

   hMACD = iMACD(_Symbol, _Period, InpFast, InpSlow, InpSignal, PRICE_CLOSE);
   if(hMACD == INVALID_HANDLE)
   {
      Print("GoldenChart_MACD: failed to create MACD handle");
      return(INIT_FAILED);
   }
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(hMACD != INVALID_HANDLE) IndicatorRelease(hMACD);
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
   if(BarsCalculated(hMACD) < rates_total)
      return(prev_calculated);

   int to_copy;
   if(prev_calculated > rates_total || prev_calculated <= 0)
      to_copy = rates_total;
   else
      to_copy = rates_total - prev_calculated + 1;

   if(CopyBuffer(hMACD, MAIN_LINE,   0, to_copy, BufMACD)   <= 0) return(prev_calculated);
   if(CopyBuffer(hMACD, SIGNAL_LINE, 0, to_copy, BufSignal) <= 0) return(prev_calculated);

   int start = (prev_calculated <= 0) ? 0 : prev_calculated - 1;
   for(int i = start; i < rates_total; i++)
   {
      BufHist[i]  = BufMACD[i] - BufSignal[i];
      BufColor[i] = (BufHist[i] >= 0.0) ? 0 : 1;   // 0=green, 1=red
   }
   return(rates_total);
}
//+------------------------------------------------------------------+
