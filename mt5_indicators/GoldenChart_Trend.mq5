//+------------------------------------------------------------------+
//|                                          GoldenChart_Trend.mq5    |
//|     Bollinger Bands(20,2) + Williams Alligator(21,13,8)           |
//|     Replicates the Investing.com / TradingView overlay look:      |
//|     shaded BB band + 3 alligator SMMAs (blue/red/green).          |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_chart_window
#property indicator_buffers 8
#property indicator_plots   7

//--- Plot 0: Bollinger band fill (lavender, like the TradingView shading)
#property indicator_label1  "BB Fill"
#property indicator_type1   DRAW_FILLING
#property indicator_color1  clrLavender,clrLavender

//--- Plot 1: BB upper band
#property indicator_label2  "BB Upper"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrSlateBlue
#property indicator_style2  STYLE_SOLID
#property indicator_width2  1

//--- Plot 2: BB lower band
#property indicator_label3  "BB Lower"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrSlateBlue
#property indicator_style3  STYLE_SOLID
#property indicator_width3  1

//--- Plot 3: BB middle (SMA 20)
#property indicator_label4  "BB Basis"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrSilver
#property indicator_style4  STYLE_DOT
#property indicator_width4  1

//--- Plot 4: Alligator Jaw (slow, blue)
#property indicator_label5  "Jaw"
#property indicator_type5   DRAW_LINE
#property indicator_color5  clrRoyalBlue
#property indicator_style5  STYLE_SOLID
#property indicator_width5  2

//--- Plot 5: Alligator Teeth (medium, red)
#property indicator_label6  "Teeth"
#property indicator_type6   DRAW_LINE
#property indicator_color6  clrRed
#property indicator_style6  STYLE_SOLID
#property indicator_width6  1

//--- Plot 6: Alligator Lips (fast, green)
#property indicator_label7  "Lips"
#property indicator_type7   DRAW_LINE
#property indicator_color7  clrLimeGreen
#property indicator_style7  STYLE_SOLID
#property indicator_width7  1

//--- Inputs (defaults match the charts in /volume) -----------------
input int    InpBBPeriod   = 20;          // Bollinger period
input double InpBBDev      = 2.0;         // Bollinger deviations
input int    InpJawPeriod  = 21;          // Alligator Jaw period
input int    InpJawShift   = 8;           // Jaw forward shift
input int    InpTeethPeriod= 13;          // Alligator Teeth period
input int    InpTeethShift = 5;           // Teeth forward shift
input int    InpLipsPeriod = 8;           // Alligator Lips period
input int    InpLipsShift  = 3;           // Lips forward shift

//--- Buffers
double BufFillUp[];   // 0  (DRAW_FILLING upper)
double BufFillLo[];   // 1  (DRAW_FILLING lower)
double BufUpper[];    // 2
double BufLower[];    // 3
double BufBasis[];    // 4
double BufJaw[];      // 5
double BufTeeth[];    // 6
double BufLips[];     // 7

//--- Handles
int hBands = INVALID_HANDLE;
int hGator = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BufFillUp, INDICATOR_DATA);
   SetIndexBuffer(1, BufFillLo, INDICATOR_DATA);
   SetIndexBuffer(2, BufUpper,  INDICATOR_DATA);
   SetIndexBuffer(3, BufLower,  INDICATOR_DATA);
   SetIndexBuffer(4, BufBasis,  INDICATOR_DATA);
   SetIndexBuffer(5, BufJaw,    INDICATOR_DATA);
   SetIndexBuffer(6, BufTeeth,  INDICATOR_DATA);
   SetIndexBuffer(7, BufLips,   INDICATOR_DATA);

   //--- alligator lines are drawn shifted forward, like the classic indicator
   PlotIndexSetInteger(4, PLOT_SHIFT, InpJawShift);
   PlotIndexSetInteger(5, PLOT_SHIFT, InpTeethShift);
   PlotIndexSetInteger(6, PLOT_SHIFT, InpLipsShift);

   IndicatorSetString(INDICATOR_SHORTNAME,
      StringFormat("GoldenChart BB(%d,%.0f) + Alligator(%d,%d,%d)",
                   InpBBPeriod, InpBBDev, InpJawPeriod, InpTeethPeriod, InpLipsPeriod));
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   hBands = iBands(_Symbol, _Period, InpBBPeriod, 0, InpBBDev, PRICE_CLOSE);
   //--- shift handled by PLOT_SHIFT, so request handles with shift 0
   hGator = iAlligator(_Symbol, _Period,
                       InpJawPeriod, 0, InpTeethPeriod, 0, InpLipsPeriod, 0,
                       MODE_SMMA, PRICE_MEDIAN);

   if(hBands == INVALID_HANDLE || hGator == INVALID_HANDLE)
   {
      Print("GoldenChart_Trend: failed to create indicator handles");
      return(INIT_FAILED);
   }
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(hBands != INVALID_HANDLE) IndicatorRelease(hBands);
   if(hGator != INVALID_HANDLE) IndicatorRelease(hGator);
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
   if(BarsCalculated(hBands) < rates_total || BarsCalculated(hGator) < rates_total)
      return(prev_calculated);

   int to_copy;
   if(prev_calculated > rates_total || prev_calculated <= 0)
      to_copy = rates_total;
   else
   {
      to_copy = rates_total - prev_calculated;
      to_copy++;   // recompute the last (forming) bar
   }

   if(CopyBuffer(hBands, BASE_LINE,   0, to_copy, BufBasis) <= 0) return(prev_calculated);
   if(CopyBuffer(hBands, UPPER_BAND,  0, to_copy, BufUpper) <= 0) return(prev_calculated);
   if(CopyBuffer(hBands, LOWER_BAND,  0, to_copy, BufLower) <= 0) return(prev_calculated);
   if(CopyBuffer(hBands, UPPER_BAND,  0, to_copy, BufFillUp) <= 0) return(prev_calculated);
   if(CopyBuffer(hBands, LOWER_BAND,  0, to_copy, BufFillLo) <= 0) return(prev_calculated);

   if(CopyBuffer(hGator, GATORJAW_LINE,   0, to_copy, BufJaw)   <= 0) return(prev_calculated);
   if(CopyBuffer(hGator, GATORTEETH_LINE, 0, to_copy, BufTeeth) <= 0) return(prev_calculated);
   if(CopyBuffer(hGator, GATORLIPS_LINE,  0, to_copy, BufLips)  <= 0) return(prev_calculated);

   return(rates_total);
}
//+------------------------------------------------------------------+
