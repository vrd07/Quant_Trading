import sys
from pathlib import Path
import traceback

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    with open("startup_progress.txt", "w") as f:
        f.write("Starting import...\n")
        
    from src.main import main
    
    with open("startup_progress.txt", "a") as f:
        f.write("Import successful. Calling main()...\n")
        
    main()
except Exception:
    with open("startup_error.txt", "w") as f:
        f.write(traceback.format_exc())
    print("Error written to startup_error.txt")
