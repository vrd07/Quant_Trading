import os
import signal
import subprocess

def cleanup():
    print("Searching for rogue trading processes...")
    try:
        # Try to find PIDs using pgrep or ps
        output = subprocess.check_output(["ps", "-ef"]).decode()
        for line in output.split('\n'):
            if "python3 src/main.py" in line and "grep" not in line:
                pid = int(line.split()[1])
                print(f"Killing process {pid}: {line}")
                os.kill(pid, signal.SIGTERM)
        
        print("Cleaning up old state files to prevent further collisions...")
        # Since I'm about to use partitioned state, I'll clear the root state file
        state_file = "data/state/system_state.json"
        if os.path.exists(state_file):
            os.remove(state_file)
            print(f"Removed {state_file}")
            
    except Exception as e:
        print(f"Cleanup error: {e}")

if __name__ == "__main__":
    cleanup()
