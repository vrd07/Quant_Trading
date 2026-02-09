//+------------------------------------------------------------------+
//|                                           EA_FileBridge.mq5      |
//|                                  File-Based MT5-Python Bridge v2 |
//|                                  Robust & Stable Architecture    |
//+------------------------------------------------------------------+
#property copyright "MT5 File Bridge v2 - Enhanced"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "2.10"
#property strict

//--- Input parameters
input string CommandFile = "mt5_commands.json";
input string StatusFile = "mt5_status.json";
input string ResponseFile = "mt5_responses.json";
input int CommandCheckIntervalMs = 100;  // Poll commands every 100ms
input int StatusUpdateIntervalMs = 1000; // Write status every 1000ms (1s)

//--- Global variables
string lastCommandContent = "";

//--- Forward declarations
double NormalizeVolume(string symbol, double volume);
ENUM_ORDER_TYPE_FILLING GetFillingMode(string symbol);
bool SendOrderWithRetry(MqlTradeRequest& request, MqlTradeResult& result);
void WriteResponse(string json);
string ExtractJsonValue(string json, string key);

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("=== EA_FileBridge v2.10 Initialized ===");
   Print("Status Update Interval: ", StatusUpdateIntervalMs, "ms");
   Print("Command Check Interval: ", CommandCheckIntervalMs, "ms");
   
   // Clean up old files
   FileDelete(ResponseFile);
   
   // Write initial status immediately
   WriteStatus();
   
   // Set timer for command polling (high frequency)
   EventSetMillisecondTimer(CommandCheckIntervalMs);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("=== EA_FileBridge Stopped ===");
}

//+------------------------------------------------------------------+
//| Timer function - Handles both commands and status updates        |
//+------------------------------------------------------------------+
void OnTimer()
{
   // 1. Process Commands (Every timer tick - 100ms)
   ProcessCommands();
   
   // 2. Write Status (Throttled to 1000ms)
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
//| Write current status to file                                     |
//+------------------------------------------------------------------+
void WriteStatus()
{
   int handle = INVALID_HANDLE;
   int maxRetries = 50; // Increased retry loop for Wine latency & Python contention
   
   // Retry loop to handle file contention
   for(int i = 0; i < maxRetries; i++)
   {
      handle = FileOpen(StatusFile, FILE_WRITE|FILE_TXT|FILE_COMMON); // Exclusive write
      if(handle != INVALID_HANDLE)
         break;
      Sleep(20); // Wait 20ms before retry (total wait up to 1000ms)
   }
   
   if(handle == INVALID_HANDLE)
   {
      // Fails silently to log after retries to avoid spamming journal
      // Only print if very persistent (e.g. permission issues)
      // Print("Error opening status file: ", GetLastError());
      return;
   }
   
   // Get market info
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   
   // Get account info
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   
   // Build JSON status manually for speed and simplicity
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
   json += "\"server_time\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\"";
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
   
   // Basic deduplication
   if(commandJson == lastCommandContent || StringLen(commandJson) < 5) return;
   lastCommandContent = commandJson;
   
   // Parse "command":"NAME"
   string command = ExtractJsonValue(commandJson, "command");
   
   // Route commands
   if(command == "HEARTBEAT")           HandleHeartbeat();
   else if(command == "GET_ACCOUNT_INFO") HandleGetAccountInfo();
   else if(command == "GET_POSITIONS")    HandleGetPositions();
   else if(command == "PLACE_ORDER")      HandlePlaceOrder(commandJson);
   else if(command == "CLOSE_POSITION")   HandleClosePosition(commandJson);
   else if(command == "GET_HISTORY")      HandleGetHistory(commandJson);
   // Unknown command ignored or logged
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
   json += "\"leverage\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE));
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
         // Filter by magic number - only return positions from this EA
         if(PositionGetInteger(POSITION_MAGIC) != 55555) continue;
         
         if(count > 0) json += ",";
         json += "{";
         json += "\"ticket\":" + IntegerToString(ticket) + ",";
         json += "\"symbol\":\"" + PositionGetString(POSITION_SYMBOL) + "\",";
         json += "\"type\":" + IntegerToString(PositionGetInteger(POSITION_TYPE)) + ","; // 0=Buy, 1=Sell
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
   
   // Validate symbol is available
   if(!SymbolSelect(symbol, true))
   {
      WriteResponse("{\"status\":\"ERROR\",\"message\":\"Symbol not available\"}");
      return;
   }
   
   // Normalize volume to broker requirements
   volume = NormalizeVolume(symbol, volume);
   
   // Check margin availability
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double requiredMargin = volume * SymbolInfoDouble(symbol, SYMBOL_MARGIN_INITIAL);
   
   if(requiredMargin > freeMargin * 0.9) // 90% safety margin
   {
      string errorMsg = "{\"status\":\"ERROR\",\"message\":\"Insufficient margin\"";
      errorMsg += ",\"required\":" + DoubleToString(requiredMargin, 2);
      errorMsg += ",\"available\":" + DoubleToString(freeMargin, 2) + "}";
      WriteResponse(errorMsg);
      return;
   }
   
   // Get fresh prices (critical for BTCUSD/XAUUSD volatility)
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
   
   // Intelligent Filling Mode Selection based on Symbol Capabilities
   request.type_filling = GetFillingMode(symbol);
   
   // Try to send order with robust retry
   if(SendOrderWithRetry(request, result))
   {
      string successMsg = "{\"status\":\"SUCCESS\"";
      successMsg += ",\"ticket\":" + IntegerToString(result.order);
      successMsg += ",\"price\":" + DoubleToString(request.price, _Digits);
      successMsg += ",\"volume\":" + DoubleToString(volume, 2) + "}";
      WriteResponse(successMsg);
   }
   else
   {
      // Improve error message with debug info about filling mode
      long fillingFlags = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
      
      string errorMsg = "{\"status\":\"ERROR\"";
      errorMsg += ",\"code\":" + IntegerToString(result.retcode);
      errorMsg += ",\"message\":\"" + result.comment + "\"";
      errorMsg += ",\"volume_requested\":" + DoubleToString(volume, 2);
      errorMsg += ",\"debug_fill_mode\":" + IntegerToString(request.type_filling);
      errorMsg += ",\"debug_sym_flags\":" + IntegerToString(fillingFlags) + "}";
      
      WriteResponse(errorMsg);
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
   
   // Get profit BEFORE closing the position
   double currentProfit = PositionGetDouble(POSITION_PROFIT);
   
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
   request.deviation = 20;
   
   // Apply correct filling mode (Fix for unsupported filling mode error)
   request.type_filling = GetFillingMode(request.symbol);
   
   if(SendOrderWithRetry(request, result))
      WriteResponse("{\"status\":\"SUCCESS\",\"pnl\":" + DoubleToString(currentProfit, 2) + "}");
   else
      WriteResponse("{\"status\":\"ERROR\",\"code\":" + IntegerToString(result.retcode) + ",\"message\":\"" + result.comment + "\"}");
}

void HandleGetHistory(string json)
{
   // Default to last 24 hours (1440 minutes)
   int minutes = (int)StringToInteger(ExtractJsonValue(json, "minutes"));
   if(minutes <= 0) minutes = 1440;
   
   datetime start = TimeCurrent() - (minutes * 60);
   datetime end = TimeCurrent();
   
   if(!HistorySelect(start, end))
   {
      WriteResponse("{\"status\":\"ERROR\",\"message\":\"Failed to select history\"}");
      return;
   }
   
   int total = HistoryDealsTotal();
   int count = 0;
   
   string resultJson = "{\"status\":\"SUCCESS\",\"deals\":[";
   
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      
      // Filter by magic number
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != 55555) continue;
      
      // Only interested in EXIT deals (closing trades)
      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) continue;
      
      if(count > 0) resultJson += ",";
      
      resultJson += "{";
      resultJson += "\"ticket\":" + IntegerToString(ticket) + ",";
      resultJson += "\"order_ticket\":" + IntegerToString(HistoryDealGetInteger(ticket, DEAL_ORDER)) + ",";
      resultJson += "\"position_ticket\":" + IntegerToString(HistoryDealGetInteger(ticket, DEAL_POSITION_ID)) + ",";
      resultJson += "\"symbol\":\"" + HistoryDealGetString(ticket, DEAL_SYMBOL) + "\",";
      resultJson += "\"type\":" + IntegerToString(HistoryDealGetInteger(ticket, DEAL_TYPE)) + ",";
      resultJson += "\"volume\":" + DoubleToString(HistoryDealGetDouble(ticket, DEAL_VOLUME), 2) + ",";
      resultJson += "\"price\":" + DoubleToString(HistoryDealGetDouble(ticket, DEAL_PRICE), _Digits) + ",";
      resultJson += "\"profit\":" + DoubleToString(HistoryDealGetDouble(ticket, DEAL_PROFIT), 2) + ",";
      resultJson += "\"commission\":" + DoubleToString(HistoryDealGetDouble(ticket, DEAL_COMMISSION), 2) + ",";
      resultJson += "\"swap\":" + DoubleToString(HistoryDealGetDouble(ticket, DEAL_SWAP), 2) + ",";
      resultJson += "\"time\":" + IntegerToString(HistoryDealGetInteger(ticket, DEAL_TIME));
      resultJson += "}";
      
      count++;
   }
   
   resultJson += "]}";
   WriteResponse(resultJson);
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Normalize volume to broker requirements                          |
//+------------------------------------------------------------------+
double NormalizeVolume(string symbol, double volume)
{
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double stepLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   // Normalize to step size
   volume = MathRound(volume / stepLot) * stepLot;
   
   // Clamp to valid range
   if(volume < minLot) volume = minLot;
   if(volume > maxLot) volume = maxLot;
   
   return NormalizeDouble(volume, 2);
}

//+------------------------------------------------------------------+
//| Get appropriate filling mode for symbol                          |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode(string symbol)
{
   long modes = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   
   if((modes & SYMBOL_FILLING_IOC) != 0) 
      return ORDER_FILLING_IOC;
      
   if((modes & SYMBOL_FILLING_FOK) != 0) 
      return ORDER_FILLING_FOK;
      
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Robust OrderSend with Auto-Retry for Filling Modes               |
//+------------------------------------------------------------------+
bool SendOrderWithRetry(MqlTradeRequest& request, MqlTradeResult& result)
{
   // Attempt 1: Try with the initial (smart-guessed) filling mode
   if(OrderSend(request, result)) return true;
   
   // If the error is NOT "Unsupported Filling Mode" (10030), fail immediately
   if(result.retcode != 10030) return false;
   
   // If we involve retries, we iterate through other possible modes
   ENUM_ORDER_TYPE_FILLING modes[3];
   modes[0] = ORDER_FILLING_IOC;
   modes[1] = ORDER_FILLING_FOK;
   modes[2] = ORDER_FILLING_RETURN;
   
   for(int i = 0; i < 3; i++)
   {
      // Skip the mode we just tried
      if(request.type_filling == modes[i]) continue;
      
      request.type_filling = modes[i];
      
      // Retry
      if(OrderSend(request, result)) return true;
      
      // If error turned into something else, stop retrying
      if(result.retcode != 10030) return false;
   }
   
   return false;
}

//+------------------------------------------------------------------+
//| Write response to file                                           |
//+------------------------------------------------------------------+
void WriteResponse(string json)
{
   int handle = FileOpen(ResponseFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle != INVALID_HANDLE)
   {
      FileWriteString(handle, json);
      FileClose(handle);
   }
}

//+------------------------------------------------------------------+
//| Extract value from JSON string (simple parser)                   |
//+------------------------------------------------------------------+
string ExtractJsonValue(string json, string key)
{
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start == -1) return "";
   
   start += StringLen(search);
   
   // Skip validation of value types (string vs number) for simplicity in this helper
   // Just grab until comma or closing brace
   
   // Skip spaces/quotes
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
   
   // Find nearest delimiter
   if(endComma != -1) end = endComma;
   if(endBrace != -1 && (end == -1 || endBrace < end)) end = endBrace;
   if(endQuote != -1 && (endQuote > start) && (end == -1 || endQuote < end)) end = endQuote;
   
   if(end == -1) return "";
   
   return StringSubstr(json, start, end-start);
}