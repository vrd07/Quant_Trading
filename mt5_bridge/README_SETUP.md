# MT5 ZeroMQ Bridge - Setup Guide

This guide details exactly how to set up the environment to run the **EA_ZeroMQ_Bridge** in MetaTrader 5.

## 1. Install ZeroMQ for MQL5

The Expert Advisor relies on the **ZeroMQ** networking library to communicate with Python.

### Step 1: Download the Library
We recommend using the [mql-zmq](https://github.com/dingmaotu/mql-zmq) binding.

1.  Download the latest release or clone the repository.
2.  Locate the `Include/Zmq` folder in the downloaded files.
3.  Locate the DLL files (`libzmq.dll`, `libsodium.dll`) usually found in `Library/MT5`.

### Step 2: Install Files to MT5 Data Folder

To find your Data Folder in MT5: **File -> Open Data Folder**.

1.  **Copy Include Files**:
    *   Copy the entire `Zmq` folder from the download.
    *   Paste it into: `MQL5/Include/`
    *   *Result*: You should have `MQL5/Include/Zmq/Zmq.mqh` and other files.

2.  **Copy DLL Files**:
    *   Copy `libzmq.dll` (and `libsodium.dll` if present).
    *   Paste them into: `MQL5/Libraries/`
    *   *Note*: Ensure you use the **x64** versions of the DLLs for MT5.

### Windows vs. Mac
*   **Windows**: The standard `MQL5` folder structure applies.
*   **Mac**: If running MT5 via Wine/Crossover:
    *   Open "C: Drive" in your Wine bottle manager.
    *   Navigate to `Program Files/MetaTrader 5/MQL5/`.
    *   Follow the same placement instructions.

---

## 2. Install JSON Parsing Library

The EA communicates using JSON format. We use the **JAson** library.

### Downloads
*   **Recommended**: [JAson.mqh](https://www.mql5.com/en/code/13663) by Sergey Pavlov.

### Installation
1.  Download `JAson.mqh`.
2.  Place it directly in: `MQL5/Include/`
3.  *Result*: You should have `MQL5/Include/JAson.mqh`.

*(Note: If the code uses `#include <JAson.mqh>`, the file must be directly in the Include folder, not a subfolder)*

---

## 3. Compile the Expert Advisor

1.  **Open MetaEditor**:
    *   In MT5, press `F4` or click the **IDE** icon in the toolbar.
2.  **Open the Source File**:
    *   Navigate to the folder where you placed `EA_ZeroMQ_Bridge.mq5`.
    *   Double-click to open it.
3.  **Compile**:
    *   Press `F7` or click **Compile**.
4.  **Check for Errors**:
    *   Look at the "Errors" tab at the bottom.
    *   **Success**: "0 errors, 0 warnings" (or minor warnings).
    *   **Failure**: Check if `Zmq/Zmq.mqh` or `JAson.mqh` can be found.
5.  **Locate Result**:
    *   A successfully compiled file `EA_ZeroMQ_Bridge.ex5` will appear in the same folder.

---

## 4. Run the EA

1.  **Prepare MT5**:
    *   Open MetaTrader 5.
    *   Enable AutoTrading: Click the **Algo Trading** button in the toolbar (it should turn Green).
    *   Enable DLL Imports: Go to **Tools -> Options -> Expert Advisors** and check **Allow DLL imports**.
2.  **Attach to Chart**:
    *   Open the **Navigator** panel (`Ctrl+N`).
    *   Wait for the list to refresh; find **EA_ZeroMQ_Bridge** under "Experts".
    *   Open any chart (e.g., EURUSD, H1). The symbol/timeframe doesn't matter for the bridge itself, but tick data will come from this symbol.
    *   Drag and drop the EA onto the chart.
3.  **Configure**:
    *   A window will pop up. Go to the **Inputs** tab.
    *   Verify ports (Defaults: REP=5555, PUSH=5556, PUB=5557).
    *   Click **OK**.
4.  **Verify**:
    *   Look at the **Toolbox** (`Ctrl+T`) -> **Experts** tab.
    *   You should see: `EA_ZeroMQ_Bridge initialized successfully` and socket binding messages.
    *   In the top-right corner of the chart, you should see the EA name with a hat icon (blue/active).

---

## 5. Troubleshooting

| Common Error | Likely Cause | Solution |
| :--- | :--- | :--- |
| **"DLL calls are not allowed"** | Security setting blocks DLLs. | Go to **Tools > Options > Expert Advisors** and check **Allow DLL imports**. |
| **"ZeroMQ bind failed"** | Port collision. | Another program (or instance of this EA) is using port 5555/5556/5557. Change the port numbers in EA Inputs. |
| **"Cannot open file 'Zmq/Zmq.mqh'"** | Missing library files. | Re-check that `MQL5/Include/Zmq` folder exists and contains `.mqh` files. |
| **"Expert Remove" immediately** | Critical init failure. | Check the **Experts** log tab for the specific error message. |
| **Python: Connection Refused** | Firewall or wrong IP. | Ensure firewall allows connections on ports 5555-5557. Use `127.0.0.1` if local. |

---

## 6. Testing the Connection

Once the EA is running (showing "initialized" in logs), you can test it with a simple Python script.

1.  Ensure you have `pyzmq` installed:
    ```bash
    pip install pyzmq
    ```

2.  Run this Python script:

```python
import zmq
import json

def test_bridge():
    context = zmq.Context()
    
    # Connect to REP socket (Command channel)
    req_socket = context.socket(zmq.REQ)
    req_socket.connect("tcp://127.0.0.1:5555")
    
    print("Sending HEARTBEAT...")
    req_socket.send_json({"command": "HEARTBEAT"})
    
    # Wait for response
    response = req_socket.recv_json()
    print(f"Response: {json.dumps(response, indent=2)}")
    
    if response.get("status") == "ALIVE":
        print("✅ SUCCESS: Bridge is alive!")
    else:
        print("❌ FAILURE: Unexpected response.")

if __name__ == "__main__":
    test_bridge()
```

### Expected Output:
```json
Sending HEARTBEAT...
Response: {
  "status": "ALIVE",
  "timestamp": "2026-01-21T14:48:00.000Z",
  "symbol": "EURUSD",
  "server_time": "2026.01.21 14:48:00"
}
✅ SUCCESS: Bridge is alive!
```
