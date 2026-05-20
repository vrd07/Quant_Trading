//+------------------------------------------------------------------+
//|                                            GoldenChart_RSI.mq5    |
//|     RSI(14) with the pink-shaded band + dashed levels, matching   |
//|     the Investing.com / TradingView sub-window look.              |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum 0
#property indicator_maximum 100
#property indicator_buffers 3
#property indicator_plots   2

//--- Plot 0: shaded band between the two level lines
#property indicator_label1  "RSI Band"
#property indicator_type1   DRAW_FILLING
#property indicator_color1  C'250,225,232',C'250,225,232'   // light pink

//--- Plot 1: RSI line
#property indicator_label2  "RSI"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrMediumVioletRed
#property indicator_style2  STYLE_SOLID
#property indicator_width2  2

//--- Inputs --------------------------------------------------------
input int    InpRSIPeriod = 14;     // RSI period
input double InpUpper     = 60.0;   // Upper band line
input double InpLower     = 40.0;   // Lower band line

double BufFillUp[];   // 0
double BufFillLo[];   // 1
double BufRSI[];      // 2

int hRSI = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BufFillUp, INDICATOR_DATA);
   SetIndexBuffer(1, BufFillLo, INDICATOR_DATA);
   SetIndexBuffer(2, BufRSI,    INDICATOR_DATA);

   IndicatorSetInteger(INDICATOR_DIGITS, 2);
   IndicatorSetString(INDICATOR_SHORTNAME, StringFormat("RSI(%d)", InpRSIPeriod));

   //--- dashed reference levels like the screenshots
   IndicatorSetInteger(INDICATOR_LEVELS, 4);
   IndicatorSetDouble (INDICATOR_LEVELVALUE, 0, 70.0);
   IndicatorSetDouble (INDICATOR_LEVELVALUE, 1, InpUpper);
   IndicatorSetDouble (INDICATOR_LEVELVALUE, 2, InpLower);
   IndicatorSetDouble (INDICATOR_LEVELVALUE, 3, 30.0);
   for(int i = 0; i < 4; i++)
   {
      IndicatorSetInteger(INDICATOR_LEVELSTYLE, i, STYLE_DOT);
      IndicatorSetInteger(INDICATOR_LEVELCOLOR, i, clrSilver);
   }

   hRSI = iRSI(_Symbol, _Period, InpRSIPeriod, PRICE_CLOSE);
   if(hRSI == INVALID_HANDLE)
   {
      Print("GoldenChart_RSI: failed to create RSI handle");
      return(INIT_FAILED);
   }
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(hRSI != INVALID_HANDLE) IndicatorRelease(hRSI);
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
   if(BarsCalculated(hRSI) < rates_total)
      return(prev_calculated);

   int to_copy;
   if(prev_calculated > rates_total || prev_calculated <= 0)
      to_copy = rates_total;
   else
      to_copy = rates_total - prev_calculated + 1;

   if(CopyBuffer(hRSI, 0, 0, to_copy, BufRSI) <= 0)
      return(prev_calculated);

   int start = (prev_calculated <= 0) ? 0 : prev_calculated - 1;
   for(int i = start; i < rates_total; i++)
   {
      BufFillUp[i] = InpUpper;
      BufFillLo[i] = InpLower;
   }
   return(rates_total);
}
//+------------------------------------------------------------------+
