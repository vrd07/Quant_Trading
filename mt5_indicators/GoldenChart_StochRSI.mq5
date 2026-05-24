//+------------------------------------------------------------------+
//|                                       GoldenChart_StochRSI.mq5    |
//|     Stochastic RSI (14, 14, 3, 3) — RSI length / Stoch length /   |
//|     %K smoothing / %D smoothing. Not built into MT5.              |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum 0
#property indicator_maximum 100
#property indicator_buffers 6
#property indicator_plots   3

//--- Plot 0: shaded band 20..80
#property indicator_label1  "StochRSI Band"
#property indicator_type1   DRAW_FILLING
#property indicator_color1  C'235,222,240',C'235,222,240'

//--- Plot 1: %K
#property indicator_label2  "%K"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrRoyalBlue
#property indicator_style2  STYLE_SOLID
#property indicator_width2  2

//--- Plot 2: %D
#property indicator_label3  "%D"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrOrangeRed
#property indicator_style3  STYLE_SOLID
#property indicator_width3  1

//--- Inputs --------------------------------------------------------
input int InpRSILen   = 14;   // RSI length
input int InpStochLen  = 14;  // Stochastic length
input int InpKSmooth   = 3;   // %K smoothing
input int InpDSmooth   = 3;   // %D smoothing
input double InpUpper  = 80.0;
input double InpLower   = 20.0;

double BufFillUp[];   // 0
double BufFillLo[];   // 1
double BufK[];        // 2
double BufD[];        // 3
double BufRSI[];      // 4 (calc)
double BufRaw[];      // 5 (calc)

int hRSI = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BufFillUp, INDICATOR_DATA);
   SetIndexBuffer(1, BufFillLo, INDICATOR_DATA);
   SetIndexBuffer(2, BufK,      INDICATOR_DATA);
   SetIndexBuffer(3, BufD,      INDICATOR_DATA);
   SetIndexBuffer(4, BufRSI,    INDICATOR_CALCULATIONS);
   SetIndexBuffer(5, BufRaw,    INDICATOR_CALCULATIONS);

   IndicatorSetInteger(INDICATOR_DIGITS, 2);
   IndicatorSetString(INDICATOR_SHORTNAME,
      StringFormat("Stoch RSI(%d,%d,%d,%d)", InpRSILen, InpStochLen, InpKSmooth, InpDSmooth));

   IndicatorSetInteger(INDICATOR_LEVELS, 2);
   IndicatorSetDouble (INDICATOR_LEVELVALUE, 0, InpUpper);
   IndicatorSetDouble (INDICATOR_LEVELVALUE, 1, InpLower);
   for(int i = 0; i < 2; i++)
   {
      IndicatorSetInteger(INDICATOR_LEVELSTYLE, i, STYLE_DOT);
      IndicatorSetInteger(INDICATOR_LEVELCOLOR, i, clrSilver);
   }

   hRSI = iRSI(_Symbol, _Period, InpRSILen, PRICE_CLOSE);
   if(hRSI == INVALID_HANDLE)
   {
      Print("GoldenChart_StochRSI: failed to create RSI handle");
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

   //--- pull the full RSI series into the calc buffer (non-series, [0]=oldest)
   if(CopyBuffer(hRSI, 0, 0, rates_total, BufRSI) <= 0)
      return(prev_calculated);

   int warmup = InpRSILen + InpStochLen + InpKSmooth + InpDSmooth + 2;
   int start  = (prev_calculated <= 0) ? 0 : prev_calculated - 1;
   if(start < warmup) start = warmup;

   for(int i = start; i < rates_total; i++)
   {
      //--- raw stochastic of RSI over InpStochLen
      double hh = -DBL_MAX, ll = DBL_MAX;
      for(int j = 0; j < InpStochLen; j++)
      {
         double r = BufRSI[i - j];
         if(r > hh) hh = r;
         if(r < ll) ll = r;
      }
      double rng = hh - ll;
      double raw = (rng > 0.0) ? 100.0 * (BufRSI[i] - ll) / rng : 0.0;
      BufRaw[i] = raw;

      //--- %K = SMA(raw, InpKSmooth)
      double sumK = 0.0;
      for(int j = 0; j < InpKSmooth; j++) sumK += BufRaw[i - j];
      BufK[i] = sumK / InpKSmooth;

      //--- %D = SMA(%K, InpDSmooth)
      double sumD = 0.0;
      for(int j = 0; j < InpDSmooth; j++) sumD += BufK[i - j];
      BufD[i] = sumD / InpDSmooth;

      BufFillUp[i] = InpUpper;
      BufFillLo[i] = InpLower;
   }

   //--- keep the warmup region flat so the band fill renders cleanly
   for(int i = (prev_calculated <= 0 ? 0 : start) ; i < warmup && i < rates_total; i++)
   {
      BufFillUp[i] = InpUpper;
      BufFillLo[i] = InpLower;
   }
   return(rates_total);
}
//+------------------------------------------------------------------+
