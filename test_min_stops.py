import json
import sys
from pathlib import Path

# Use the cross-platform path detection from MT5FileClient
sys.path.insert(0, str(Path(__file__).parent))
from mt5_bridge.mt5_file_client import MT5FileClient

files_dir = MT5FileClient._get_default_mt5_path()
status_file = files_dir / "mt5_status.json"

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
