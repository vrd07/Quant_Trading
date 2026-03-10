//+------------------------------------------------------------------+
//|                                              TestHistory.mq5     |
//+------------------------------------------------------------------+
#property copyright "Test"
#property link      ""
#property version   "1.00"

void OnStart()
{
   Print("Testing HistorySelect");
   
   datetime endTime = TimeCurrent();
   datetime startTime = endTime - (30 * 24 * 60 * 60); // 30 days
   
   if(!HistorySelect(startTime, endTime))
   {
      Print("HistorySelect failed! Error: ", GetLastError());
      return;
   }
   
   int total = HistoryDealsTotal();
   Print("Total History Deals found: ", total);
   
   int handle = FileOpen("test_history_deals.txt", FILE_WRITE|FILE_TXT|FILE_COMMON);if(handle == INVALID_HANDLE){ Print("Failed to open file"); return; }
   
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket > 0)
      {
         long entryType = HistoryDealGetInteger(ticket, DEAL_ENTRY);
         long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
         double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
         string comment = HistoryDealGetString(ticket, DEAL_COMMENT);
         
         FileWriteString(handle, StringFormat("Ticket: %d | Entry: %d | Magic: %d | Profit: %.2f | Comment: %s\n", ticket, entryType, magic, profit, comment));
      }
   }
   FileClose(handle);
   Print("Done writing to test_history_deals.txt");
}
