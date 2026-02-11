
import json
import uuid

def inspect_state():
    try:
        with open("data/state/system_state.json", "r") as f:
            state = json.load(f)
            
        positions = state.get("positions", {})
        print(f"Total positions: {len(positions)}")
        
        symbol_counts = {}
        for pos_id, pos in positions.items():
            sym_obj = pos.get("symbol")
            if isinstance(sym_obj, dict):
                sym = sym_obj.get("ticker", "UNKNOWN")
            else:
                sym = str(sym_obj)
                
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
            print(f"ID: {pos_id} | Symbol: {sym} | Vol: {pos.get('quantity')} | Entry: {pos.get('entry_price')}")
            
        print("\nSummary:")
        for sym, count in symbol_counts.items():
            print(f"  {sym}: {count}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_state()
