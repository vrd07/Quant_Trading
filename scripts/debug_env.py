import sys
from pathlib import Path
print("DEBUG: Starting execution")
print(f"DEBUG: CWD is {Path.cwd()}")

root_path = str(Path(__file__).parent.parent)
print(f"DEBUG: Adding project root: {root_path}")
sys.path.insert(0, root_path)

try:
    print("DEBUG: Attempting import src.connectors.mt5_connector")
    from src.connectors.mt5_connector import MT5Connector
    print("DEBUG: Import successful")
    
    print("DEBUG: Attempting MT5Connector instantiation")
    connector = MT5Connector(data_dir=None) # Dry run init
    print("DEBUG: Instantiation successful")

except Exception as e:
    print(f"DEBUG: ERROR: {e}")
    import traceback
    traceback.print_exc()

print("DEBUG: Finished")
