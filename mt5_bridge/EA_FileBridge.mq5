//+------------------------------------------------------------------+
//|                                           EA_FileBridge.mq5      |
//|                                  File-Based MT5-Python Bridge v2 |
//|                                  Robust & Stable Architecture    |
//+------------------------------------------------------------------+
#property copyright "MT5 File Bridge v2"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "2.00"
#property strict

//--- Input parameters
input string CommandFile = "mt5_commands.json";
input string StatusFile = "mt5_status.json";
input string ResponseFile = "mt5_responses.json";
input int CommandCheckIntervalMs = 100;  // Poll commands every 100ms
input int StatusUpdateIntervalMs = 1000; // Write status every 1000ms (1s)

//--- Global variables
string lastCommandContent = "";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("=== EA_FileBridge v2 Initialized ===");
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
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(i > 0) json += ",";
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
      }
   }
   json += "]}";
   WriteResponse(json);
}

void HandlePlaceOrder(string json)
{
   string symbol = ExtractJsonValue(json, "symbol");
   string typeStr = ExtractJsonValue(json, "order_type"); // BUY/SELL
   double volume = StringToDouble(ExtractJsonValue(json, "volume"));
   
   if(symbol == "") symbol = _Symbol;
   
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = volume;
   request.type = (typeStr == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   request.price = (request.type == ORDER_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID);
   request.deviation = 20;
   request.magic = 55555;
   request.comment = "PythonBridge";
   
   // Intelligent Filling Mode Selection based on Symbol Capabilities
   request.type_filling = GetFillingMode(symbol);
   
   // Try to send order
   if(OrderSend(request, result))
   {
      WriteResponse("{\"status\":\"SUCCESS\",\"ticket\":" + IntegerToString(result.order) + "}");
   }
   else
   {
      // Improve error message with debug info about filling mode
      long fillingFlags = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
      string params = "Vol=" + DoubleToString(volume, 2) + 
                      ", FillMode=" + IntegerToString(request.type_filling) + 
                      ", SymFlags=" + IntegerToString(fillingFlags);
                      
      string errorMsg = result.comment + " (" + params + ")";
      
      // Add explicit debug fields to JSON
      string debugJson = "{\"status\":\"ERROR\",\"code\":" + IntegerToString(result.retcode) + 
                         ",\"message\":\"" + errorMsg + "\"" +
                         ",\"debug_fill_mode\":" + IntegerToString(request.type_filling) +
                         ",\"debug_sym_flags\":" + IntegerToString(fillingFlags) + 
                         "}";
      WriteResponse(debugJson);
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
   
   if(OrderSend(request, result))
      WriteResponse("{\"status\":\"SUCCESS\",\"pnl\":" + DoubleToString(result.profit, 2) + "}");
   else
      WriteResponse("{\"status\":\"ERROR\",\"code\":" + IntegerToString(result.retcode) + "}");
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
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
