//+------------------------------------------------------------------+
//| EA_DOMProbe.mq5 — READ-ONLY depth-of-market probe.               |
//| Writes book snapshots to its own file in Common Files. Sends NO  |
//| commands — cannot interact with EA_FileBridge's command channel. |
//| Attach to the XAUUSDs chart alongside (not instead of) the bot EA.|
//+------------------------------------------------------------------+
#property strict
input string OutFile = "mt5_dom_probe.json";

int OnInit()
{
   if(!MarketBookAdd(_Symbol))
      Print("DOMProbe: MarketBookAdd failed for ", _Symbol,
            " — broker likely publishes no book");
   EventSetTimer(5); // heartbeat write even when no book events arrive
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   MarketBookRelease(_Symbol);
   EventKillTimer();
}

void OnTimer()   { WriteBook(); }
void OnBookEvent(const string &symbol)
{
   if(symbol == _Symbol) WriteBook();
}

void WriteBook()
{
   MqlBookInfo book[];
   bool got = MarketBookGet(_Symbol, book);
   string json = "{\"ts\":\"" + TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS) +
                 "\",\"symbol\":\"" + _Symbol + "\",\"levels\":[";
   if(got)
   {
      for(int i = 0; i < ArraySize(book); i++)
      {
         if(i > 0) json += ",";
         json += "{\"type\":" + IntegerToString(book[i].type) +
                 ",\"price\":" + DoubleToString(book[i].price, _Digits) +
                 ",\"volume\":" + DoubleToString((double)book[i].volume, 2) + "}";
      }
   }
   json += "]}";
   int h = FileOpen(OutFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}
