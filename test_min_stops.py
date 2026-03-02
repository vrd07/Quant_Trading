import json
import os

files_dir = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files")
status_file = os.path.join(files_dir, "mt5_status.json")

try:
    with open(status_file, "r") as f:
        data = json.load(f)
        quotes = data.get("quotes", {})
        xauusd_quote = quotes.get("XAUUSD", {})
        if xauusd_quote:
            print(f"XAUUSD BID: {xauusd_quote.get('bid')} ASK: {xauusd_quote.get('ask')}")
        else:
            print(f"XAUUSD quote not found in {data.keys()}")
except Exception as e:
    print(f"Error parsing json: {e}")
