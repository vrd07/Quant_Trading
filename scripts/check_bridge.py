#!/usr/bin/env python3
"""Check EA bridge status file to debug data collection."""
import json, os

path = os.path.expanduser(
    "~/Library/Application Support/net.metaquotes.wine.metatrader5"
    "/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/mt5_status.json"
)

results = []

if not os.path.exists(path):
    results.append(f"FILE NOT FOUND: {path}")
else:
    size = os.path.getsize(path)
    results.append(f"File size: {size} bytes")
    
    with open(path, 'rb') as f:
        raw = f.read()
    results.append(f"First 20 bytes hex: {raw[:20].hex()}")
    
    # Try decode
    decoded = False
    for enc in ['utf-16-le', 'utf-16', 'utf-8']:
        try:
            text = raw.decode(enc).strip().lstrip('\ufeff')
            data = json.loads(text)
            results.append(f"Decoded with: {enc}")
            results.append(f"Top-level keys: {list(data.keys())}")
            
            if 'quotes' in data:
                results.append(f"Quotes symbols: {list(data['quotes'].keys())}")
                for sym, q in data['quotes'].items():
                    results.append(f"  {sym}: bid={q.get('bid')}, ask={q.get('ask')}")
            else:
                results.append("NO 'quotes' key!")
            
            if 'symbol' in data:
                results.append(f"Single symbol field: {data['symbol']}")
                results.append(f"  bid={data.get('bid')}, ask={data.get('ask')}")
            else:
                results.append("NO 'symbol' key!")
            
            decoded = True
            break
        except Exception as e:
            results.append(f"{enc} failed: {e}")
    
    if not decoded:
        results.append("COULD NOT DECODE FILE")
        results.append(f"Raw content (first 200 chars): {raw[:200]}")

# Write results
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'bridge_check.txt')
with open(output_path, 'w') as f:
    f.write('\n'.join(results))

print('\n'.join(results))
