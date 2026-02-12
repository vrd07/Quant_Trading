//+------------------------------------------------------------------+
//|                                           EA_FileBridge.mq5      |
//|                           File-Based MT5-Python Bridge v3.0      |
//|                        PRODUCTION-READY with Risk Management     |
//+------------------------------------------------------------------+
#property copyright "MT5 File Bridge v3.0 - Production Edition"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "3.00"
#property strict

//--- Input parameters - File Communication
input string CommandFile = "mt5_commands.json";
input string StatusFile = "mt5_status.json";
input string ResponseFile = "mt5_responses.json";
input int CommandCheckIntervalMs = 100;  // Poll commands every 100ms
input int StatusUpdateIntervalMs = 1000; // Write status every 1000ms (1s)

//--- Input parameters - RISK MANAGEMENT (CRITICAL FOR LIVE TRADING)
input group "=== EMERGENCY CONTROLS ==="
input bool EnableTrading = true;                    // MASTER KILL SWITCH - Set to FALSE to stop all trading
input bool PanicCloseAll = false;                   // Set to TRUE to close all positions immediately

input group "=== POSITION LIMITS ==="
input int MaxOpenPositions = 10;                    // Maximum simultaneous positions
input double MaxPositionSizePercent = 1.0;          // Max risk per trade (% of balance)
input double MaxTotalExposureLots = 5.0;            // Max total lots across all positions

input group "=== DAILY LIMITS ==="
input double MaxDailyLossPercent = 3.0;             // Stop trading if daily loss exceeds this % (matches Python config)
input double MaxDailyProfitPercent = 10.0;          // Stop trading if daily profit exceeds this % (take profits)
input int MaxTradesPerDay = 50;                     // Maximum trades allowed per day (matches Python config)

input group "=== TRADING HOURS ==="
input bool UseTradingHours = false;                 // Enable/disable trading hours restriction
input int TradingStartHour = 9;                     // Trading start hour (broker time)
input int TradingEndHour = 17;                      // Trading end hour (broker time)
input bool AvoidFridayClose = true;                 // Stop trading 2 hours before Friday close

input group "=== SLIPPAGE & EXECUTION ==="
input int MaxSlippagePips = 5;                      // Alert if slippage exceeds this
input int MaxRetries = 3;                           // Maximum order retry attempts

input group "=== NOTIFICATIONS ==="
input bool SendAlerts = true;                       // Enable alerts for important events
input bool LogAllTrades = true;                     // Log every trade to Experts tab

//--- Global variables
string lastCommandContent = "";
double dailyStartingBalance = 0;
int dailyTradeCount = 0;
datetime lastResetDate = 0;

//--- Forward declarations
double NormalizeVolume(string symbol, double volume);
ENUM_ORDER_TYPE_FILLING GetFillingMode(string symbol);
bool SendOrderWithRetry(MqlTradeRequest& request, MqlTradeResult& result);
void WriteResponse(string json);
string ExtractJsonValue(string json, string key);
bool ValidateOrder(string symbol, double volume);
bool IsTradingAllowed();
void CheckPanicClose();
void ResetDailyCounters();
double GetDailyPnL();
int CountOpenPositions();
double GetTotalExposure();
void LogTrade(string action, string symbol, double volume, double price, string result);
void SendAlert(string message);

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("========================================");
   Print("=== EA_FileBridge v3.0 PRODUCTION ===");
   Print("========================================");
   Print("Status Update Interval: ", StatusUpdateIntervalMs, "ms");
   Print("Command Check Interval: ", CommandCheckIntervalMs, "ms");
   Print("");
   Print("=== RISK MANAGEMENT SETTINGS ===");
   Print("Trading Enabled: ", EnableTrading ? "YES" : "NO");
   Print("Max Open Positions: ", MaxOpenPositions);
   Print("Max Position Size: ", MaxPositionSizePercent, "%");
   Print("Max Daily Loss: ", MaxDailyLossPercent, "%");
   Print("Max Daily Profit: ", MaxDailyProfitPercent, "%");
   Print("Max Trades/Day: ", MaxTradesPerDay);
   Print("========================================");
   
   // Clean up old files
   FileDelete(ResponseFile);
   
   // Initialize daily counters
   dailyStartingBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   dailyTradeCount = 0;
   lastResetDate = TimeCurrent();
   
   // Write initial status immediately
   WriteStatus();
   
   // Set timer for command polling (high frequency)
   EventSetMillisecondTimer(CommandCheckIntervalMs);
   
   if(!EnableTrading)
   {
      Print("WARNING: Trading is DISABLED. Set EnableTrading=true to allow trading.");
      SendAlert("EA Started but Trading DISABLED");
   }
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("=== EA_FileBridge Stopped ===");
   Print("Final Daily P&L: $", DoubleToString(GetDailyPnL(), 2));
   Print("Total Trades Today: ", dailyTradeCount);
}

//+------------------------------------------------------------------+
//| Timer function - Handles both commands and status updates        |
//+------------------------------------------------------------------+
void OnTimer()
{
   // 0. Reset daily counters if new day
   ResetDailyCounters();
   
   // 1. Check for panic close
   CheckPanicClose();
   
   // 2. Process Commands (Every timer tick - 100ms)
   ProcessCommands();
   
   // 3. Write Status (Throttled to 1000ms)
   static uint lastStatusTime = 0;
   uint currentTime = GetTickCount();
   
   if(currentTime - lastStatusTime >= (uint)StatusUpdateIntervalMs)
   {
      WriteStatus();
      lastStatusTime = currentTime;
   }
}

//+------------------------------------------------------------------+
//| Expert tick function - NO FILE OPS HERE TO PREVENT CONTENTION    |
//+------------------------------------------------------------------+
void OnTick()
{
   // Logic moved to OnTimer to decouple from market volatility
}

//+------------------------------------------------------------------+
//| Check for panic close and execute if needed                      |
//+------------------------------------------------------------------+
void CheckPanicClose()
{
   if(!PanicCloseAll) return;
   
   Print("!!! PANIC CLOSE ACTIVATED !!!");
   SendAlert("PANIC CLOSE: Closing all positions!");
   
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == 55555)
      {
         MqlTradeRequest request;
         MqlTradeResult result;
         ZeroMemory(request);
         ZeroMemory(result);
         
         request.action = TRADE_ACTION_DEAL;
         request.position = ticket;
         request.symbol = PositionGetString(POSITION_SYMBOL);
         request.volume = PositionGetDouble(POSITION_VOLUME);
         request.type = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
         request.price = (request.type == ORDER_TYPE_BUY) ? SymbolInfoDouble(request.symbol, SYMBOL_ASK) : SymbolInfoDouble(request.symbol, SYMBOL_BID);
         request.deviation = 50; // Higher deviation for panic close
         request.type_filling = GetFillingMode(request.symbol);
         
         if(OrderSend(request, result))
            Print("Panic closed position #", ticket);
         else
            Print("Failed to panic close position #", ticket, " Error: ", result.retcode);
      }
   }
   
   Print("!!! PANIC CLOSE COMPLETED !!!");
}

//+------------------------------------------------------------------+
//| Reset daily counters at midnight                                 |
//+------------------------------------------------------------------+
void ResetDailyCounters()
{
   datetime currentTime = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(currentTime, dt);
   
   // Check if it's a new day
   MqlDateTime lastDt;
   TimeToStruct(lastResetDate, lastDt);
   
   if(dt.day != lastDt.day || dt.mon != lastDt.mon || dt.year != lastDt.year)
   {
      Print("=== NEW TRADING DAY ===");
      Print("Previous Day - Trades: ", dailyTradeCount, " | P&L: $", DoubleToString(GetDailyPnL(), 2));
      
      dailyStartingBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      dailyTradeCount = 0;
      lastResetDate = currentTime;
      
      Print("Starting Balance: $", DoubleToString(dailyStartingBalance, 2));
   }
}

//+------------------------------------------------------------------+
//| Calculate daily P&L                                              |
//+------------------------------------------------------------------+
double GetDailyPnL()
{
   return AccountInfoDouble(ACCOUNT_BALANCE) - dailyStartingBalance;
}

//+------------------------------------------------------------------+
//| Count open positions from this EA                                |
//+------------------------------------------------------------------+
int CountOpenPositions()
{
   int count = 0;
   int total = PositionsTotal();
   
   for(int i = 0; i < total; i++)
   {
      if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == 55555)
         count++;
   }
   
   return count;
}

//+------------------------------------------------------------------+
//| Calculate total exposure across all positions                    |
//+------------------------------------------------------------------+
double GetTotalExposure()
{
   double totalLots = 0;
   int total = PositionsTotal();
   
   for(int i = 0; i < total; i++)
   {
      if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == 55555)
         totalLots += PositionGetDouble(POSITION_VOLUME);
   }
   
   return totalLots;
}

//+------------------------------------------------------------------+
//| Check if trading is allowed based on all rules                   |
//+------------------------------------------------------------------+
bool IsTradingAllowed()
{
   // 1. Master kill switch
   if(!EnableTrading)
   {
      if(SendAlerts) SendAlert("Trading attempt blocked: EnableTrading is FALSE");
      return false;
   }
   
   // 2. Panic mode
   if(PanicCloseAll)
   {
      if(SendAlerts) SendAlert("Trading blocked: Panic mode active");
      return false;
   }
   
   // 3. Daily loss limit
   double dailyPnL = GetDailyPnL();
   double maxDailyLoss = dailyStartingBalance * MaxDailyLossPercent / 100.0;
   
   if(dailyPnL < -maxDailyLoss)
   {
      if(SendAlerts) SendAlert("Daily loss limit reached: $" + DoubleToString(dailyPnL, 2));
      return false;
   }
   
   // 4. Daily profit limit (take profits and stop)
   double maxDailyProfit = dailyStartingBalance * MaxDailyProfitPercent / 100.0;
   
   if(dailyPnL > maxDailyProfit)
   {
      if(SendAlerts) SendAlert("Daily profit target reached: $" + DoubleToString(dailyPnL, 2) + " - Trading stopped");
      return false;
   }
   
   // 5. Max trades per day
   if(dailyTradeCount >= MaxTradesPerDay)
   {
      if(SendAlerts) SendAlert("Max trades per day reached: " + IntegerToString(dailyTradeCount));
      return false;
   }
   
   // 6. Trading hours
   if(UseTradingHours)
   {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      
      if(dt.hour < TradingStartHour || dt.hour >= TradingEndHour)
         return false;
         
      // Avoid Friday close (2 hours before end of week)
      if(AvoidFridayClose && dt.day_of_week == 5 && dt.hour >= 20)
         return false;
   }
   
   // 7. Max open positions
   if(CountOpenPositions() >= MaxOpenPositions)
   {
      if(SendAlerts) SendAlert("Max positions reached: " + IntegerToString(MaxOpenPositions));
      return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Validate individual order before placing                         |
//+------------------------------------------------------------------+
bool ValidateOrder(string symbol, double volume)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   
   // 1. Check max position size based on % of balance
   double marginRequired = volume * SymbolInfoDouble(symbol, SYMBOL_MARGIN_INITIAL);
   double maxMargin = balance * MaxPositionSizePercent / 100.0;
   
   if(marginRequired > maxMargin)
   {
      if(SendAlerts) 
         SendAlert("Position too large: " + DoubleToString(volume, 2) + 
                   " lots requires $" + DoubleToString(marginRequired, 2) + 
                   " but max allowed is $" + DoubleToString(maxMargin, 2));
      return false;
   }
   
   // 2. Check total exposure limit
   double currentExposure = GetTotalExposure();
   if(currentExposure + volume > MaxTotalExposureLots)
   {
      if(SendAlerts)
         SendAlert("Total exposure limit: Current=" + DoubleToString(currentExposure, 2) + 
                   " + New=" + DoubleToString(volume, 2) + 
                   " exceeds max=" + DoubleToString(MaxTotalExposureLots, 2));
      return false;
   }
   
   // 3. Check margin availability
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(marginRequired > freeMargin * 0.8) // Use only 80% of free margin for safety
   {
      if(SendAlerts)
         SendAlert("Insufficient margin: Required=" + DoubleToString(marginRequired, 2) + 
                   " Available=" + DoubleToString(freeMargin, 2));
      return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Write current status to file                                     |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Write current status to file                                     |
//+------------------------------------------------------------------+
void WriteStatus()
{
   int handle = INVALID_HANDLE;
   int maxRetries = 50;
   
   for(int i = 0; i < maxRetries; i++)
   {
      handle = FileOpen(StatusFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
      if(handle != INVALID_HANDLE)
         break;
      Sleep(20);
   }
   
   if(handle == INVALID_HANDLE)
      return;
   
   // Get market info
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   
   // Get account info
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   
   // Build enhanced JSON status
   string json = "{";
   json += "\"status\":\"ALIVE\",";
   json += "\"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\",";
   json += "\"symbol\":\"" + _Symbol + "\",";
   json += "\"bid\":" + DoubleToString(bid, _Digits) + ",";
   json += "\"ask\":" + DoubleToString(ask, _Digits) + ",";
   json += "\"balance\":" + DoubleToString(balance, 2) + ",";
   json += "\"equity\":" + DoubleToString(equity, 2) + ",";
   json += "\"margin\":" + DoubleToString(margin, 2) + ",";
   json += "\"free_margin\":" + DoubleToString(free_margin, 2) + ",";
   json += "\"trading_enabled\":" + (EnableTrading ? "true" : "false") + ",";
   json += "\"daily_pnl\":" + DoubleToString(GetDailyPnL(), 2) + ",";
   json += "\"daily_trades\":" + IntegerToString(dailyTradeCount) + ",";
   json += "\"open_positions\":" + IntegerToString(CountOpenPositions()) + ",";
   json += "\"total_exposure\":" + DoubleToString(GetTotalExposure(), 2) + ",";
   json += "\"server_time\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\",";
   
   // === NEW: Multi-Symbol Support ===
   // Broadcast prices for ALL symbols in Market Watch
   json += "\"quotes\":{";
   int total = SymbolsTotal(true); // true = selected in Market Watch
   int added = 0;
   
   for(int i=0; i<total; i++)
   {
      string symbol = SymbolName(i, true);
      MqlTick tick;
      
      if(SymbolInfoTick(symbol, tick))
      {
         int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
         
         if(added > 0) json += ",";
         json += "\"" + symbol + "\":{";
         json += "\"bid\":" + DoubleToString(tick.bid, digits) + ",";
         json += "\"ask\":" + DoubleToString(tick.ask, digits) + ",";
         json += "\"time\":" + IntegerToString(tick.time);
         json += "}";
         added++;
      }
   }
   json += "}";
   // ================================
   
   json += "}";
   
   FileWriteString(handle, json);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Read and process commands from file                              |
//+------------------------------------------------------------------+
void ProcessCommands()
{
   if(!FileIsExist(CommandFile, FILE_COMMON)) return;

   int handle = FileOpen(CommandFile, FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ);
   if(handle == INVALID_HANDLE) return;
   
   string commandJson = "";
   while(!FileIsEnding(handle))
      commandJson += FileReadString(handle);
      
   FileClose(handle);
   
   if(commandJson == lastCommandContent || StringLen(commandJson) < 5) return;
   lastCommandContent = commandJson;
   
   string command = ExtractJsonValue(commandJson, "command");
   
   if(command == "HEARTBEAT")           HandleHeartbeat();
   else if(command == "GET_ACCOUNT_INFO") HandleGetAccountInfo();
   else if(command == "GET_POSITIONS")    HandleGetPositions();
   else if(command == "PLACE_ORDER")      HandlePlaceOrder(commandJson);
   else if(command == "CLOSE_POSITION")   HandleClosePosition(commandJson);
   else if(command == "GET_LIMITS")       HandleGetLimits();
}

//+------------------------------------------------------------------+
//| Implementation of Commands                                       |
//+------------------------------------------------------------------+
void HandleHeartbeat()
{
   string json = "{\"status\":\"ALIVE\",\"server_time\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\"}";
   WriteResponse(json);
}

void HandleGetAccountInfo()
{
   string json = "{";
   json += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   json += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   json += "\"margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ",";
   json += "\"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + ",";
   json += "\"currency\":\"" + AccountInfoString(ACCOUNT_CURRENCY) + "\",";
   json += "\"leverage\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)) + ",";
   json += "\"daily_pnl\":" + DoubleToString(GetDailyPnL(), 2) + ",";
   json += "\"daily_trades\":" + IntegerToString(dailyTradeCount);
   json += "}";
   WriteResponse(json);
}

void HandleGetLimits()
{
   string json = "{";
   json += "\"trading_enabled\":" + (EnableTrading ? "true" : "false") + ",";
   json += "\"max_positions\":" + IntegerToString(MaxOpenPositions) + ",";
   json += "\"current_positions\":" + IntegerToString(CountOpenPositions()) + ",";
   json += "\"max_daily_loss_pct\":" + DoubleToString(MaxDailyLossPercent, 2) + ",";
   json += "\"max_daily_profit_pct\":" + DoubleToString(MaxDailyProfitPercent, 2) + ",";
   json += "\"daily_pnl\":" + DoubleToString(GetDailyPnL(), 2) + ",";
   json += "\"max_trades_per_day\":" + IntegerToString(MaxTradesPerDay) + ",";
   json += "\"daily_trades\":" + IntegerToString(dailyTradeCount) + ",";
   json += "\"total_exposure\":" + DoubleToString(GetTotalExposure(), 2) + ",";
   json += "\"max_exposure\":" + DoubleToString(MaxTotalExposureLots, 2);
   json += "}";
   WriteResponse(json);
}

void HandleGetPositions()
{
   string json = "{\"positions\":[";
   int total = PositionsTotal();
   int count = 0;
   
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetInteger(POSITION_MAGIC) != 55555) continue;
         
         if(count > 0) json += ",";
         json += "{";
         json += "\"ticket\":" + IntegerToString(ticket) + ",";
         json += "\"symbol\":\"" + PositionGetString(POSITION_SYMBOL) + "\",";
         json += "\"type\":" + IntegerToString(PositionGetInteger(POSITION_TYPE)) + ",";
         json += "\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ",";
         json += "\"price_open\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), _Digits) + ",";
         json += "\"price_current\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), _Digits) + ",";
         json += "\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + ",";
         json += "\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), _Digits) + ",";
         json += "\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), _Digits) + ",";
         json += "\"comment\":\"" + PositionGetString(POSITION_COMMENT) + "\"";
         json += "}";
         count++;
      }
   }
   json += "]}";
   WriteResponse(json);
}

void HandlePlaceOrder(string json)
{
   string symbol = ExtractJsonValue(json, "symbol");
   string typeStr = ExtractJsonValue(json, "order_type");
   double volume = StringToDouble(ExtractJsonValue(json, "volume"));
   double sl = StringToDouble(ExtractJsonValue(json, "sl"));
   double tp = StringToDouble(ExtractJsonValue(json, "tp"));
   
   if(symbol == "") symbol = _Symbol;
   
   // === SAFETY CHECK 1: Is trading allowed? ===
   if(!IsTradingAllowed())
   {
      WriteResponse("{\"status\":\"ERROR\",\"message\":\"Trading not allowed - check limits\"}");
      LogTrade("PLACE_ORDER", symbol, volume, 0, "BLOCKED - Trading not allowed");
      return;
   }
   
   // === SAFETY CHECK 2: Validate symbol ===
   if(!SymbolSelect(symbol, true))
   {
      WriteResponse("{\"status\":\"ERROR\",\"message\":\"Symbol not available\"}");
      return;
   }
   
   // === SAFETY CHECK 3: Normalize volume ===
   volume = NormalizeVolume(symbol, volume);
   
   // === SAFETY CHECK 4: Validate order size and exposure ===
   if(!ValidateOrder(symbol, volume))
   {
      WriteResponse("{\"status\":\"ERROR\",\"message\":\"Order validation failed - check size limits\"}");
      LogTrade("PLACE_ORDER", symbol, volume, 0, "BLOCKED - Validation failed");
      return;
   }
   
   // === SAFETY CHECK 5: Get fresh prices ===
   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
   {
      WriteResponse("{\"status\":\"ERROR\",\"message\":\"Failed to get current price\"}");
      return;
   }
   
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = volume;
   request.type = (typeStr == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   request.price = (request.type == ORDER_TYPE_BUY) ? tick.ask : tick.bid;
   request.sl = sl;
   request.tp = tp;
   request.deviation = 20;
   request.magic = 55555;
   request.comment = "PythonBridge";
   request.type_filling = GetFillingMode(symbol);
   
   double requestedPrice = request.price;
   
   if(SendOrderWithRetry(request, result))
   {
      // Increment daily trade counter
      dailyTradeCount++;
      
      // Check slippage
      double slippage = MathAbs(result.price - requestedPrice);
      double slippagePips = slippage / SymbolInfoDouble(symbol, SYMBOL_POINT) / 10.0;
      
      if(slippagePips > MaxSlippagePips && SendAlerts)
         SendAlert("High slippage: " + DoubleToString(slippagePips, 1) + " pips on " + symbol);
      
      string successMsg = "{\"status\":\"SUCCESS\"";
      successMsg += ",\"ticket\":" + IntegerToString(result.order);
      successMsg += ",\"price\":" + DoubleToString(result.price, _Digits);
      successMsg += ",\"volume\":" + DoubleToString(volume, 2);
      successMsg += ",\"slippage_pips\":" + DoubleToString(slippagePips, 2) + "}";
      WriteResponse(successMsg);
      
      LogTrade("PLACE_ORDER", symbol, volume, result.price, "SUCCESS - Ticket #" + IntegerToString(result.order));
   }
   else
   {
      long fillingFlags = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
      
      string errorMsg = "{\"status\":\"ERROR\"";
      errorMsg += ",\"code\":" + IntegerToString(result.retcode);
      errorMsg += ",\"message\":\"" + result.comment + "\"";
      errorMsg += ",\"volume_requested\":" + DoubleToString(volume, 2);
      errorMsg += ",\"debug_fill_mode\":" + IntegerToString(request.type_filling);
      errorMsg += ",\"debug_sym_flags\":" + IntegerToString(fillingFlags) + "}";
      
      WriteResponse(errorMsg);
      LogTrade("PLACE_ORDER", symbol, volume, 0, "FAILED - " + result.comment);
   }
}

void HandleClosePosition(string json)
{
   ulong ticket = StringToInteger(ExtractJsonValue(json, "ticket"));
   
   if(!PositionSelectByTicket(ticket))
   {
      WriteResponse("{\"status\":\"ERROR\",\"message\":\"Position not found\"}");
      return;
   }
   
   // Get profit BEFORE closing
   double currentProfit = PositionGetDouble(POSITION_PROFIT);
   string symbol = PositionGetString(POSITION_SYMBOL);
   double volume = PositionGetDouble(POSITION_VOLUME);
   
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   
   request.action = TRADE_ACTION_DEAL;
   request.position = ticket;
   request.symbol = symbol;
   request.volume = volume;
   request.type = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.price = (request.type == ORDER_TYPE_BUY) ? SymbolInfoDouble(request.symbol, SYMBOL_ASK) : SymbolInfoDouble(request.symbol, SYMBOL_BID);
   request.deviation = 20;
   request.type_filling = GetFillingMode(request.symbol);
   
   if(SendOrderWithRetry(request, result))
   {
      WriteResponse("{\"status\":\"SUCCESS\",\"pnl\":" + DoubleToString(currentProfit, 2) + "}");
      LogTrade("CLOSE_POSITION", symbol, volume, result.price, "SUCCESS - P&L: $" + DoubleToString(currentProfit, 2));
   }
   else
   {
      WriteResponse("{\"status\":\"ERROR\",\"code\":" + IntegerToString(result.retcode) + ",\"message\":\"" + result.comment + "\"}");
      LogTrade("CLOSE_POSITION", symbol, volume, 0, "FAILED - " + result.comment);
   }
}

//+------------------------------------------------------------------+
//| Helper Functions                                                 |
//+------------------------------------------------------------------+

double NormalizeVolume(string symbol, double volume)
{
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double stepLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   volume = MathRound(volume / stepLot) * stepLot;
   
   if(volume < minLot) volume = minLot;
   if(volume > maxLot) volume = maxLot;
   
   return NormalizeDouble(volume, 2);
}

ENUM_ORDER_TYPE_FILLING GetFillingMode(string symbol)
{
   long modes = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   
   if((modes & SYMBOL_FILLING_IOC) != 0) 
      return ORDER_FILLING_IOC;
      
   if((modes & SYMBOL_FILLING_FOK) != 0) 
      return ORDER_FILLING_FOK;
      
   return ORDER_FILLING_RETURN;
}

bool SendOrderWithRetry(MqlTradeRequest& request, MqlTradeResult& result)
{
   // Attempt with initial filling mode
   if(OrderSend(request, result)) return true;
   
   if(result.retcode != 10030) return false;
   
   // Retry with different filling modes
   ENUM_ORDER_TYPE_FILLING modes[3];
   modes[0] = ORDER_FILLING_IOC;
   modes[1] = ORDER_FILLING_FOK;
   modes[2] = ORDER_FILLING_RETURN;
   
   for(int i = 0; i < 3; i++)
   {
      if(request.type_filling == modes[i]) continue;
      
      request.type_filling = modes[i];
      
      if(OrderSend(request, result)) return true;
      if(result.retcode != 10030) return false;
   }
   
   return false;
}

void WriteResponse(string json)
{
   int handle = FileOpen(ResponseFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle != INVALID_HANDLE)
   {
      FileWriteString(handle, json);
      FileClose(handle);
   }
}

string ExtractJsonValue(string json, string key)
{
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start == -1) return "";
   
   start += StringLen(search);
   
   while(start < StringLen(json))
   {
      ushort charCode = StringGetCharacter(json, start);
      if(charCode != ' ' && charCode != '"' && charCode != ':') break;
      start++;
   }
   
   int end = -1;
   int endComma = StringFind(json, ",", start);
   int endBrace = StringFind(json, "}", start);
   int endQuote = StringFind(json, "\"", start);
   
   if(endComma != -1) end = endComma;
   if(endBrace != -1 && (end == -1 || endBrace < end)) end = endBrace;
   if(endQuote != -1 && (endQuote > start) && (end == -1 || endQuote < end)) end = endQuote;
   
   if(end == -1) return "";
   
   return StringSubstr(json, start, end-start);
}

void LogTrade(string action, string symbol, double volume, double price, string result)
{
   if(!LogAllTrades) return;
   
   string logMsg = StringFormat("[%s] %s | %s | %.2f lots | Price: %.5f | %s", 
                                TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS),
                                action, symbol, volume, price, result);
   Print(logMsg);
}

void SendAlert(string message)
{
   if(!SendAlerts) return;
   
   string alertMsg = "[EA_FileBridge] " + message;
   Alert(alertMsg);
   Print("ALERT: ", alertMsg);
}