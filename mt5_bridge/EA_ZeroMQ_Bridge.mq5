//+------------------------------------------------------------------+
//|                                          EA_ZeroMQ_Bridge.mq5    |
//|                                              Trading System      |
//|                            ZeroMQ Bridge for Python Integration  |
//+------------------------------------------------------------------+
#property copyright "Trading System"
#property link      ""
#property version   "1.00"
#property strict
#property description "ZeroMQ Bridge EA for Python-MT5 Communication"

//+------------------------------------------------------------------+
//| Include Libraries                                                 |
//+------------------------------------------------------------------+
// ZeroMQ library - must be installed in Include/Zmq/
#include <Zmq/Zmq.mqh>

// JSON library - using JAson.mqh for JSON parsing
// If not available, download from: https://www.mql5.com/en/code/13663
#include <JAson.mqh>

//+------------------------------------------------------------------+
//| Input Parameters                                                  |
//+------------------------------------------------------------------+
input int    REP_PORT        = 5555;       // REP Socket Port (Request/Reply)
input int    PUSH_PORT       = 5556;       // PUSH Socket Port (Fill Confirmations)
input int    PUB_PORT        = 5557;       // PUB Socket Port (Tick Data)
input bool   VERBOSE_LOGGING = true;       // Enable verbose logging
input int    RECV_TIMEOUT    = 1;          // Receive timeout in milliseconds
input string BIND_ADDRESS    = "127.0.0.1"; // Bind address (localhost)

//+------------------------------------------------------------------+
//| Global Variables                                                  |
//+------------------------------------------------------------------+
Context context("ZeroMQ_Bridge");
Socket  repSocket(context, ZMQ_REP);
Socket  pushSocket(context, ZMQ_PUSH);
Socket  pubSocket(context, ZMQ_PUB);

bool    g_socketsInitialized = false;
string  g_currentSymbol;

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   g_currentSymbol = Symbol();
   
   PrintFormat("EA_ZeroMQ_Bridge initializing for symbol: %s", g_currentSymbol);
   
   // Initialize ZeroMQ sockets
   if(!InitializeSockets())
   {
      Print("ERROR: Failed to initialize ZeroMQ sockets");
      return(INIT_FAILED);
   }
   
   g_socketsInitialized = true;
   Print("EA_ZeroMQ_Bridge started successfully");
   PrintFormat("  REP  Socket bound to tcp://%s:%d", BIND_ADDRESS, REP_PORT);
   PrintFormat("  PUSH Socket bound to tcp://%s:%d", BIND_ADDRESS, PUSH_PORT);
   PrintFormat("  PUB  Socket bound to tcp://%s:%d", BIND_ADDRESS, PUB_PORT);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Initialize all ZeroMQ sockets                                     |
//+------------------------------------------------------------------+
bool InitializeSockets()
{
   string repAddress  = StringFormat("tcp://%s:%d", BIND_ADDRESS, REP_PORT);
   string pushAddress = StringFormat("tcp://%s:%d", BIND_ADDRESS, PUSH_PORT);
   string pubAddress  = StringFormat("tcp://%s:%d", BIND_ADDRESS, PUB_PORT);
   
   // Set socket options for non-blocking
   repSocket.setLinger(0);
   pushSocket.setLinger(0);
   pubSocket.setLinger(0);
   
   // Set receive timeout for REP socket
   repSocket.setReceiveTimeout(RECV_TIMEOUT);
   
   // Bind REP socket (Request/Reply)
   if(!repSocket.bind(repAddress))
   {
      PrintFormat("ERROR: Failed to bind REP socket to %s", repAddress);
      return false;
   }
   
   // Bind PUSH socket (Fill confirmations)
   if(!pushSocket.bind(pushAddress))
   {
      PrintFormat("ERROR: Failed to bind PUSH socket to %s", pushAddress);
      repSocket.unbind(repAddress);
      return false;
   }
   
   // Bind PUB socket (Tick data)
   if(!pubSocket.bind(pubAddress))
   {
      PrintFormat("ERROR: Failed to bind PUB socket to %s", pubAddress);
      repSocket.unbind(repAddress);
      pushSocket.unbind(pushAddress);
      return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_socketsInitialized)
   {
      // Unbind and close sockets
      string repAddress  = StringFormat("tcp://%s:%d", BIND_ADDRESS, REP_PORT);
      string pushAddress = StringFormat("tcp://%s:%d", BIND_ADDRESS, PUSH_PORT);
      string pubAddress  = StringFormat("tcp://%s:%d", BIND_ADDRESS, PUB_PORT);
      
      repSocket.unbind(repAddress);
      pushSocket.unbind(pushAddress);
      pubSocket.unbind(pubAddress);
      
      // Shutdown context
      context.shutdown();
   }
   
   PrintFormat("EA_ZeroMQ_Bridge stopped. Reason: %d", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_socketsInitialized)
      return;
   
   // 1. Check for commands on REP socket (non-blocking)
   string command = ReceiveCommand();
   if(command != "")
   {
      if(VERBOSE_LOGGING)
         PrintFormat("Received command: %s", StringSubstr(command, 0, 200));
      
      string response = ProcessCommand(command);
      SendResponse(response);
      
      if(VERBOSE_LOGGING)
         PrintFormat("Sent response: %s", StringSubstr(response, 0, 200));
   }
   
   // 2. Publish current tick to PUB socket
   PublishTick();
}

//+------------------------------------------------------------------+
//| Trade transaction event                                           |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(!g_socketsInitialized)
      return;
   
   // Check if this is an order fill (deal added)
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      // Get deal info
      ulong dealTicket = trans.deal;
      
      if(dealTicket > 0)
      {
         // Select the deal to get its properties
         if(HistoryDealSelect(dealTicket))
         {
            string fillJson = BuildFillConfirmation(dealTicket);
            PushFillConfirmation(fillJson);
            
            if(VERBOSE_LOGGING)
               PrintFormat("Pushed fill confirmation: %s", fillJson);
         }
      }
   }
   
   // Also handle order fill through TRADE_TRANSACTION_HISTORY_ADD
   if(trans.type == TRADE_TRANSACTION_ORDER_UPDATE && 
      trans.order_state == ORDER_STATE_FILLED)
   {
      // Order was filled - this is handled by DEAL_ADD above
   }
}

//+------------------------------------------------------------------+
//| Build fill confirmation JSON                                      |
//+------------------------------------------------------------------+
string BuildFillConfirmation(ulong dealTicket)
{
   CJAVal json;
   
   json["type"] = "FILL";
   json["order_id"] = IntegerToString(HistoryDealGetInteger(dealTicket, DEAL_ORDER));
   json["position_id"] = IntegerToString(HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID));
   json["symbol"] = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
   
   ENUM_DEAL_TYPE dealType = (ENUM_DEAL_TYPE)HistoryDealGetInteger(dealTicket, DEAL_TYPE);
   json["side"] = (dealType == DEAL_TYPE_BUY) ? "BUY" : "SELL";
   
   json["filled_quantity"] = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
   json["filled_price"] = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
   json["commission"] = HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
   json["timestamp"] = GetISOTimestamp(TimeCurrent());
   
   return json.Serialize();
}

//+------------------------------------------------------------------+
//| Push fill confirmation to PUSH socket                             |
//+------------------------------------------------------------------+
void PushFillConfirmation(string message)
{
   ZmqMsg msg(message);
   pushSocket.send(msg, true);  // Non-blocking
}

//+------------------------------------------------------------------+
//| Receive command from REP socket (non-blocking)                    |
//+------------------------------------------------------------------+
string ReceiveCommand()
{
   ZmqMsg msg;
   
   // Use ZMQ_DONTWAIT for non-blocking receive
   if(repSocket.recv(msg, true))
   {
      return msg.getData();
   }
   
   return "";
}

//+------------------------------------------------------------------+
//| Send response to REP socket                                       |
//+------------------------------------------------------------------+
void SendResponse(string response)
{
   ZmqMsg msg(response);
   repSocket.send(msg);
}

//+------------------------------------------------------------------+
//| Process command and return response                               |
//+------------------------------------------------------------------+
string ProcessCommand(string command)
{
   CJAVal json;
   
   // Parse JSON command
   if(!json.Deserialize(command))
   {
      return BuildErrorResponse("Invalid JSON format");
   }
   
   // Get command type
   string cmdType = json["command"].ToStr();
   
   if(cmdType == "")
   {
      return BuildErrorResponse("Missing 'command' field");
   }
   
   // Route to appropriate handler
   if(cmdType == "HEARTBEAT")
   {
      return HandleHeartbeat();
   }
   else if(cmdType == "PLACE_ORDER")
   {
      return HandlePlaceOrder(json);
   }
   else if(cmdType == "CLOSE_POSITION")
   {
      return HandleClosePosition(json);
   }
   else if(cmdType == "GET_POSITIONS")
   {
      return HandleGetPositions();
   }
   else if(cmdType == "GET_ACCOUNT_INFO")
   {
      return HandleGetAccountInfo();
   }
   else if(cmdType == "MODIFY_ORDER")
   {
      return HandleModifyOrder(json);
   }
   else
   {
      return BuildErrorResponse(StringFormat("Unknown command: %s", cmdType));
   }
}

//+------------------------------------------------------------------+
//| Build error response JSON                                         |
//+------------------------------------------------------------------+
string BuildErrorResponse(string errorMessage)
{
   CJAVal json;
   json["error"] = errorMessage;
   return json.Serialize();
}

//+------------------------------------------------------------------+
//| Get ISO 8601 timestamp string                                     |
//+------------------------------------------------------------------+
string GetISOTimestamp(datetime time)
{
   MqlDateTime dt;
   TimeToStruct(time, dt);
   
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d.000Z",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
}

//+------------------------------------------------------------------+
//| Get ISO 8601 timestamp with milliseconds                          |
//+------------------------------------------------------------------+
string GetISOTimestampMs(datetime time, long timeMs)
{
   MqlDateTime dt;
   TimeToStruct(time, dt);
   
   long ms = timeMs % 1000;
   
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec, (int)ms);
}

//+------------------------------------------------------------------+
//| Handler: HEARTBEAT                                                |
//+------------------------------------------------------------------+
string HandleHeartbeat()
{
   CJAVal json;
   json["status"] = "ALIVE";
   json["timestamp"] = GetISOTimestamp(TimeCurrent());
   json["symbol"] = g_currentSymbol;
   json["server_time"] = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
   
   return json.Serialize();
}

//+------------------------------------------------------------------+
//| Handler: PLACE_ORDER                                              |
//+------------------------------------------------------------------+
string HandlePlaceOrder(CJAVal &json)
{
   // Extract parameters
   string symbol = json["symbol"].ToStr();
   string side = json["side"].ToStr();
   double quantity = json["quantity"].ToDbl();
   string orderType = json["order_type"].ToStr();
   double stopLoss = json["stop_loss"].ToDbl();
   double takeProfit = json["take_profit"].ToDbl();
   
   // Validate symbol
   if(symbol == "")
   {
      return BuildErrorResponse("Missing 'symbol' parameter");
   }
   
   if(!SymbolSelect(symbol, true))
   {
      return BuildErrorResponse(StringFormat("Invalid symbol: %s", symbol));
   }
   
   // Validate side
   ENUM_ORDER_TYPE orderDirection;
   if(side == "BUY")
   {
      orderDirection = ORDER_TYPE_BUY;
   }
   else if(side == "SELL")
   {
      orderDirection = ORDER_TYPE_SELL;
   }
   else
   {
      return BuildErrorResponse(StringFormat("Invalid side: %s. Must be BUY or SELL", side));
   }
   
   // Validate quantity
   if(quantity <= 0)
   {
      return BuildErrorResponse("Quantity must be greater than 0");
   }
   
   // Check minimum lot size
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   if(quantity < minLot)
   {
      return BuildErrorResponse(StringFormat("Quantity %.2f below minimum lot size %.2f", quantity, minLot));
   }
   
   if(quantity > maxLot)
   {
      return BuildErrorResponse(StringFormat("Quantity %.2f exceeds maximum lot size %.2f", quantity, maxLot));
   }
   
   // Normalize lot size
   quantity = MathFloor(quantity / lotStep) * lotStep;
   
   // Check margin
   double marginRequired;
   if(!OrderCalcMargin(orderDirection, symbol, quantity, SymbolInfoDouble(symbol, SYMBOL_ASK), marginRequired))
   {
      return BuildErrorResponse("Failed to calculate required margin");
   }
   
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(marginRequired > freeMargin)
   {
      return BuildErrorResponse(StringFormat("Insufficient margin. Required: %.2f, Available: %.2f", 
                                            marginRequired, freeMargin));
   }
   
   // Get current prices
   double price = (side == "BUY") ? 
                  SymbolInfoDouble(symbol, SYMBOL_ASK) : 
                  SymbolInfoDouble(symbol, SYMBOL_BID);
   
   // Build trade request
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = quantity;
   request.type = orderDirection;
   request.price = price;
   request.sl = stopLoss;
   request.tp = takeProfit;
   request.deviation = 10;  // Slippage in points
   request.magic = 123456;  // Magic number for identification
   request.comment = "ZMQ_Bridge";
   request.type_filling = ORDER_FILLING_IOC;  // Immediate or Cancel
   
   // Try different filling modes if IOC not supported
   if(!OrderSend(request, result))
   {
      // Try FOK filling
      request.type_filling = ORDER_FILLING_FOK;
      if(!OrderSend(request, result))
      {
         // Try RETURN filling
         request.type_filling = ORDER_FILLING_RETURN;
         if(!OrderSend(request, result))
         {
            int errorCode = (int)result.retcode;
            string errorDesc = GetRetcodeDescription(result.retcode);
            return BuildErrorResponse(StringFormat("Order rejected. Code: %d, Description: %s", 
                                                   errorCode, errorDesc));
         }
      }
   }
   
   // Build success response
   CJAVal response;
   response["order_id"] = IntegerToString(result.order);
   response["deal_id"] = IntegerToString(result.deal);
   response["status"] = "ACCEPTED";
   response["filled_price"] = result.price;
   response["filled_volume"] = result.volume;
   response["retcode"] = (int)result.retcode;
   
   if(VERBOSE_LOGGING)
   {
      PrintFormat("Order placed: %s %s %.2f @ %.5f, Order ID: %d", 
                 side, symbol, quantity, result.price, result.order);
   }
   
   return response.Serialize();
}

//+------------------------------------------------------------------+
//| Handler: CLOSE_POSITION                                           |
//+------------------------------------------------------------------+
string HandleClosePosition(CJAVal &json)
{
   string positionIdStr = json["position_id"].ToStr();
   
   if(positionIdStr == "")
   {
      return BuildErrorResponse("Missing 'position_id' parameter");
   }
   
   ulong positionId = StringToInteger(positionIdStr);
   
   // Find and select the position
   bool found = false;
   int totalPositions = PositionsTotal();
   
   for(int i = 0; i < totalPositions; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_IDENTIFIER) == positionId)
         {
            found = true;
            break;
         }
      }
   }
   
   if(!found)
   {
      return BuildErrorResponse(StringFormat("Position not found: %s", positionIdStr));
   }
   
   // Get position details
   string symbol = PositionGetString(POSITION_SYMBOL);
   double volume = PositionGetDouble(POSITION_VOLUME);
   ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double profit = PositionGetDouble(POSITION_PROFIT);
   double swap = PositionGetDouble(POSITION_SWAP);
   
   // Determine close direction
   ENUM_ORDER_TYPE closeDirection = (posType == POSITION_TYPE_BUY) ? 
                                    ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   
   double closePrice = (closeDirection == ORDER_TYPE_BUY) ? 
                       SymbolInfoDouble(symbol, SYMBOL_ASK) : 
                       SymbolInfoDouble(symbol, SYMBOL_BID);
   
   // Build close request
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = volume;
   request.type = closeDirection;
   request.price = closePrice;
   request.position = positionId;
   request.deviation = 10;
   request.magic = 123456;
   request.comment = "ZMQ_Bridge_Close";
   request.type_filling = ORDER_FILLING_IOC;
   
   // Send close order
   if(!OrderSend(request, result))
   {
      // Try different filling modes
      request.type_filling = ORDER_FILLING_FOK;
      if(!OrderSend(request, result))
      {
         request.type_filling = ORDER_FILLING_RETURN;
         if(!OrderSend(request, result))
         {
            int errorCode = (int)result.retcode;
            string errorDesc = GetRetcodeDescription(result.retcode);
            return BuildErrorResponse(StringFormat("Failed to close position. Code: %d, Description: %s", 
                                                   errorCode, errorDesc));
         }
      }
   }
   
   // Calculate realized PnL
   double realizedPnl = profit + swap;
   
   // Build success response
   CJAVal response;
   response["status"] = "CLOSED";
   response["position_id"] = positionIdStr;
   response["realized_pnl"] = realizedPnl;
   response["close_price"] = result.price;
   response["order_id"] = IntegerToString(result.order);
   
   if(VERBOSE_LOGGING)
   {
      PrintFormat("Position closed: %s, PnL: %.2f", positionIdStr, realizedPnl);
   }
   
   return response.Serialize();
}

//+------------------------------------------------------------------+
//| Handler: GET_POSITIONS                                            |
//+------------------------------------------------------------------+
string HandleGetPositions()
{
   CJAVal response;
   CJAVal positions;
   
   int totalPositions = PositionsTotal();
   int posIndex = 0;
   
   for(int i = 0; i < totalPositions; i++)
   {
      ulong ticket = PositionGetTicket(i);
      
      if(ticket > 0 && PositionSelectByTicket(ticket))
      {
         CJAVal pos;
         
         string symbol = PositionGetString(POSITION_SYMBOL);
         ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         double volume = PositionGetDouble(POSITION_VOLUME);
         double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
         double currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
         double profit = PositionGetDouble(POSITION_PROFIT);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         ulong positionId = PositionGetInteger(POSITION_IDENTIFIER);
         
         pos["position_id"] = IntegerToString(positionId);
         pos["ticket"] = IntegerToString(ticket);
         pos["symbol"] = symbol;
         pos["side"] = (posType == POSITION_TYPE_BUY) ? "LONG" : "SHORT";
         pos["quantity"] = volume;
         pos["entry_price"] = openPrice;
         pos["current_price"] = currentPrice;
         pos["unrealized_pnl"] = profit;
         pos["stop_loss"] = sl;
         pos["take_profit"] = tp;
         pos["swap"] = PositionGetDouble(POSITION_SWAP);
         pos["magic"] = IntegerToString(PositionGetInteger(POSITION_MAGIC));
         pos["comment"] = PositionGetString(POSITION_COMMENT);
         pos["open_time"] = GetISOTimestamp((datetime)PositionGetInteger(POSITION_TIME));
         
         positions[posIndex] = pos;
         posIndex++;
      }
   }
   
   response["positions"] = positions;
   response["count"] = posIndex;
   
   return response.Serialize();
}

//+------------------------------------------------------------------+
//| Handler: GET_ACCOUNT_INFO                                         |
//+------------------------------------------------------------------+
string HandleGetAccountInfo()
{
   CJAVal response;
   
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin = AccountInfoDouble(ACCOUNT_MARGIN);
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double marginLevel = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   
   response["balance"] = balance;
   response["equity"] = equity;
   response["margin"] = margin;
   response["free_margin"] = freeMargin;
   response["margin_level"] = marginLevel;
   response["profit"] = AccountInfoDouble(ACCOUNT_PROFIT);
   response["credit"] = AccountInfoDouble(ACCOUNT_CREDIT);
   response["currency"] = AccountInfoString(ACCOUNT_CURRENCY);
   response["leverage"] = (int)AccountInfoInteger(ACCOUNT_LEVERAGE);
   response["trade_allowed"] = AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) != 0;
   response["trade_expert"] = AccountInfoInteger(ACCOUNT_TRADE_EXPERT) != 0;
   response["account_id"] = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   response["server"] = AccountInfoString(ACCOUNT_SERVER);
   response["company"] = AccountInfoString(ACCOUNT_COMPANY);
   
   // Account type
   ENUM_ACCOUNT_TRADE_MODE tradeMode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   string accountType;
   switch(tradeMode)
   {
      case ACCOUNT_TRADE_MODE_DEMO:    accountType = "DEMO"; break;
      case ACCOUNT_TRADE_MODE_CONTEST: accountType = "CONTEST"; break;
      case ACCOUNT_TRADE_MODE_REAL:    accountType = "REAL"; break;
      default: accountType = "UNKNOWN";
   }
   response["account_type"] = accountType;
   
   return response.Serialize();
}

//+------------------------------------------------------------------+
//| Handler: MODIFY_ORDER (Modify Position SL/TP)                     |
//+------------------------------------------------------------------+
string HandleModifyOrder(CJAVal &json)
{
   string positionIdStr = json["position_id"].ToStr();
   
   if(positionIdStr == "")
   {
      return BuildErrorResponse("Missing 'position_id' parameter");
   }
   
   ulong positionId = StringToInteger(positionIdStr);
   
   // Find and select the position
   bool found = false;
   ulong posTicket = 0;
   string symbol = "";
   
   int totalPositions = PositionsTotal();
   
   for(int i = 0; i < totalPositions; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_IDENTIFIER) == positionId)
         {
            found = true;
            posTicket = ticket;
            symbol = PositionGetString(POSITION_SYMBOL);
            break;
         }
      }
   }
   
   if(!found)
   {
      return BuildErrorResponse(StringFormat("Position not found: %s", positionIdStr));
   }
   
   // Get new SL/TP values
   double newSL = json["stop_loss"].ToDbl();
   double newTP = json["take_profit"].ToDbl();
   
   // If values are 0, keep existing
   if(newSL == 0)
   {
      newSL = PositionGetDouble(POSITION_SL);
   }
   if(newTP == 0)
   {
      newTP = PositionGetDouble(POSITION_TP);
   }
   
   // Build modify request
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   
   request.action = TRADE_ACTION_SLTP;
   request.symbol = symbol;
   request.position = positionId;
   request.sl = newSL;
   request.tp = newTP;
   
   // Send modify request
   if(!OrderSend(request, result))
   {
      int errorCode = (int)result.retcode;
      string errorDesc = GetRetcodeDescription(result.retcode);
      return BuildErrorResponse(StringFormat("Failed to modify position. Code: %d, Description: %s", 
                                             errorCode, errorDesc));
   }
   
   // Build success response
   CJAVal response;
   response["status"] = "MODIFIED";
   response["position_id"] = positionIdStr;
   response["new_stop_loss"] = newSL;
   response["new_take_profit"] = newTP;
   
   if(VERBOSE_LOGGING)
   {
      PrintFormat("Position modified: %s, SL: %.5f, TP: %.5f", positionIdStr, newSL, newTP);
   }
   
   return response.Serialize();
}

//+------------------------------------------------------------------+
//| Publish tick data to PUB socket                                   |
//+------------------------------------------------------------------+
void PublishTick()
{
   MqlTick tick;
   
   if(!SymbolInfoTick(g_currentSymbol, tick))
   {
      return;
   }
   
   CJAVal json;
   
   json["type"] = "TICK";
   json["symbol"] = g_currentSymbol;
   json["timestamp"] = GetISOTimestampMs((datetime)(tick.time), tick.time_msc);
   json["bid"] = tick.bid;
   json["ask"] = tick.ask;
   json["last"] = tick.last;
   json["volume"] = (double)tick.volume;
   json["volume_real"] = tick.volume_real;
   json["flags"] = (int)tick.flags;
   
   string tickData = json.Serialize();
   
   ZmqMsg msg(tickData);
   pubSocket.send(msg, true);  // Non-blocking send
}

//+------------------------------------------------------------------+
//| Get retcode description                                           |
//+------------------------------------------------------------------+
string GetRetcodeDescription(uint retcode)
{
   switch(retcode)
   {
      case TRADE_RETCODE_REQUOTE:            return "Requote";
      case TRADE_RETCODE_REJECT:             return "Request rejected";
      case TRADE_RETCODE_CANCEL:             return "Request canceled by trader";
      case TRADE_RETCODE_PLACED:             return "Order placed";
      case TRADE_RETCODE_DONE:               return "Request completed";
      case TRADE_RETCODE_DONE_PARTIAL:       return "Request completed partially";
      case TRADE_RETCODE_ERROR:              return "Request processing error";
      case TRADE_RETCODE_TIMEOUT:            return "Request canceled by timeout";
      case TRADE_RETCODE_INVALID:            return "Invalid request";
      case TRADE_RETCODE_INVALID_VOLUME:     return "Invalid volume";
      case TRADE_RETCODE_INVALID_PRICE:      return "Invalid price";
      case TRADE_RETCODE_INVALID_STOPS:      return "Invalid stops";
      case TRADE_RETCODE_TRADE_DISABLED:     return "Trade disabled";
      case TRADE_RETCODE_MARKET_CLOSED:      return "Market closed";
      case TRADE_RETCODE_NO_MONEY:           return "Insufficient funds";
      case TRADE_RETCODE_PRICE_CHANGED:      return "Price changed";
      case TRADE_RETCODE_PRICE_OFF:          return "No quotes";
      case TRADE_RETCODE_INVALID_EXPIRATION: return "Invalid expiration";
      case TRADE_RETCODE_ORDER_CHANGED:      return "Order state changed";
      case TRADE_RETCODE_TOO_MANY_REQUESTS:  return "Too frequent requests";
      case TRADE_RETCODE_NO_CHANGES:         return "No changes in request";
      case TRADE_RETCODE_SERVER_DISABLES_AT: return "Autotrading disabled by server";
      case TRADE_RETCODE_CLIENT_DISABLES_AT: return "Autotrading disabled by client";
      case TRADE_RETCODE_LOCKED:             return "Request locked for processing";
      case TRADE_RETCODE_FROZEN:             return "Order or position frozen";
      case TRADE_RETCODE_INVALID_FILL:       return "Invalid order filling type";
      case TRADE_RETCODE_CONNECTION:         return "No connection with server";
      case TRADE_RETCODE_ONLY_REAL:          return "Operation allowed only for real accounts";
      case TRADE_RETCODE_LIMIT_ORDERS:       return "Pending orders limit reached";
      case TRADE_RETCODE_LIMIT_VOLUME:       return "Volume limit reached";
      case TRADE_RETCODE_INVALID_ORDER:      return "Invalid or prohibited order type";
      case TRADE_RETCODE_POSITION_CLOSED:    return "Position already closed";
      default:                               return StringFormat("Unknown error code: %d", retcode);
   }
}

//+------------------------------------------------------------------+
//| Push error message to PUSH socket                                 |
//+------------------------------------------------------------------+
void PushError(string errorMessage)
{
   CJAVal json;
   json["type"] = "ERROR";
   json["message"] = errorMessage;
   json["timestamp"] = GetISOTimestamp(TimeCurrent());
   
   string errorJson = json.Serialize();
   
   ZmqMsg msg(errorJson);
   pushSocket.send(msg, true);  // Non-blocking
   
   PrintFormat("ERROR pushed: %s", errorMessage);
}

//+------------------------------------------------------------------+
//| Timer function (optional - for periodic tasks)                    |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Can be used for periodic maintenance tasks
   // Currently not used - tick-based processing is sufficient
}

//+------------------------------------------------------------------+
//| Chart event handler (optional)                                    |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, 
                  const double &dparam, const string &sparam)
{
   // Handle chart events if needed
}
//+------------------------------------------------------------------+
