//+------------------------------------------------------------------+
//|                                        VPParityAttach.mq5        |
//|                                                                  |
//|  Attaches GoldenChart_VolumeProfile to the current chart with    |
//|  InpExportCSV=true, so a parity run can be driven without        |
//|  clicking through the GUI. Drag it onto an XAUUSD chart, or run  |
//|  the terminal with a start config:                               |
//|                                                                  |
//|      [StartUp]                                                   |
//|      Symbol=XAUUSD                                               |
//|      Period=M15                                                  |
//|      Script=VPParityAttach                                       |
//|                                                                  |
//|  (A Template= line alone will NOT attach anything -- MT5 only    |
//|   creates the startup chart when there is an Expert or Script    |
//|   to run on it.)                                                 |
//|                                                                  |
//|  ⚠️ The iCustom arguments are POSITIONAL and must stay in the     |
//|  indicator's input declaration order. Adding an input to         |
//|  GoldenChart_VolumeProfile.mq5 means editing this list too, or   |
//|  every value after the new one is silently read into the wrong   |
//|  parameter.                                                      |
//+------------------------------------------------------------------+
#property script_show_inputs false

void OnStart()
{
   int h = iCustom(_Symbol, PERIOD_M15, "GoldenChart_VolumeProfile",
      0.10,      // InpRowSize
      0.70,      // InpValueAreaPct
      0,         // InpVAAlgorithm
      5,         // InpProfileDays
      3,         // InpCompositeDays
      -1,        // InpSessionUTCOverride
      1.00,      // InpMaxSpreadUSD
      0,         // InpTickPriceMode
      200,       // InpMinSessionTicks
      12,        // InpTickRetryLimit
      0.35,      // InpSkewThreshold  <- calibrated
      5,         // InpMinRowsForShape
      0.50,      // InpRegimeMinElapsed
      60,        // InpIBMinutes
      true,      // InpShowLVN
      true,      // InpShowHVN
      0.15,      // InpHVNProminencePct
      0.50,      // InpLVNRatio
      10,        // InpNodeMinSepRows
      5,         // InpRefreshSec
      true,      // InpShowPanel
      2,         // InpHistogramProfiles
      0.35,      // InpHistogramWidthPct
      3000,      // InpMaxObjects
      clrGold, clrDeepSkyBlue, clrTomato, clrMagenta,
      clrOrchid, clrSlateGray, clrDarkOrange, clrDarkSlateGray,
      false,     // InpAlertsOn
      false,     // InpAlertDeveloping
      false,     // InpAlertLVN
      0.50,      // InpAlertRearmUSD
      false,     // InpSendNotifications
      true);     // InpExportCSV
   if(h == INVALID_HANDLE)
   {
      PrintFormat("VPParityAttach: iCustom failed, err %d", GetLastError());
      return;
   }
   if(!ChartIndicatorAdd(0, 0, h))
      PrintFormat("VPParityAttach: ChartIndicatorAdd failed, err %d", GetLastError());
   else
      Print("VPParityAttach: indicator attached with InpExportCSV=true");
   IndicatorRelease(h);   // the chart keeps its own reference
}
