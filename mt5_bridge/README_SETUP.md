# 🖥️ Windows Setup Guide — Step-by-Step

This guide is written for **complete beginners**. Follow every step exactly as written.

---

## What You Need

- A **Windows 10 or 11** computer
- An **internet connection**
- A **broker account** that supports MetaTrader 5 (demo account is fine to start)

You will install two things:
1. **Python** — the programming language that runs the trading brain
2. **MetaTrader 5** — the trading platform that connects to your broker

---

## Part 1: Install Python

### Step 1.1 — Download Python

1. Open your web browser
2. Go to: **https://www.python.org/downloads/**
3. Click the big yellow button that says **"Download Python 3.x.x"**
4. A file will download (something like `python-3.x.x-amd64.exe`)

### Step 1.2 — Install Python

1. Double-click the downloaded file to open the installer
2. **⚠️ IMPORTANT: Check the box at the bottom that says "Add Python to PATH"** — this is critical!
3. Click **"Install Now"**
4. Wait for it to finish
5. Click **"Close"**

### Step 1.3 — Verify Python Works

1. Press `Win + R` on your keyboard (the Windows key + the letter R)
2. Type `cmd` and press Enter — a black window (Command Prompt) opens
3. Type this and press Enter:
   ```
   python --version
   ```
4. You should see something like: `Python 3.12.5`
5. If you see an error, restart your computer and try again

---

## Part 2: Install MetaTrader 5

### Step 2.1 — Download MT5

1. Go to: **https://www.metatrader5.com/en/download**
2. Click **"Download MetaTrader 5"**
3. Run the downloaded installer
4. Follow the installation wizard (click Next, Next, Finish)

### Step 2.2 — Log In to Your Broker

1. Open MetaTrader 5
2. Your broker should appear in the server list
3. Enter your **login**, **password**, and select your **server**
4. Click **OK**
5. You should see your account balance in the bottom-left area

> 💡 **Tip:** If you don't have a broker yet, many brokers offer free **demo accounts** with virtual money. This is perfect for testing.

---

## Part 3: Download This Project

### Step 3.1 — Download from GitHub

1. Go to: **https://github.com/vrd07/Quant_Trading**
2. Click the green **"Code"** button
3. Click **"Download ZIP"**
4. Save the ZIP file to your computer

### Step 3.2 — Extract the ZIP

1. Find the downloaded ZIP file (usually in your **Downloads** folder)
2. Right-click it → **"Extract All..."**
3. Choose where to extract it (e.g., `C:\Users\YourName\Documents\`)
4. Click **"Extract"**
5. You should now have a folder called `Quant_Trading-main`
6. **Rename** it to `Quant_Trading` (remove the `-main` part) for simplicity

---

## Part 4: Install Python Dependencies

### Step 4.1 — Open Command Prompt in Project Folder

1. Open the `Quant_Trading` folder in File Explorer
2. Click on the **address bar** at the top (where it shows the folder path)
3. Type `cmd` and press **Enter** — a Command Prompt opens in this folder

> 💡 **Alternative:** Press `Win + R`, type `cmd`, press Enter. Then type:
> ```
> cd C:\Users\YourName\Documents\Quant_Trading
> ```
> (Replace `YourName` with your actual Windows username)

### Step 4.2 — Install Required Packages

Type this command and press Enter:
```
pip install -r requirements.txt
```

Wait for it to finish. You'll see a lot of text scrolling — that's normal. It should end with `Successfully installed ...`.

> ⚠️ **If you see "pip is not recognized":** Python wasn't added to PATH. Uninstall Python and reinstall it, making sure to check **"Add Python to PATH"** in Step 1.2.

---

## Part 5: Set Up the Expert Advisor (EA) in MT5

The EA is the "robot" inside MetaTrader that receives commands from Python.

### Step 5.1 — Find Your MT5 Data Folder

1. Open MetaTrader 5
2. Click **File** (top-left menu) → **Open Data Folder**
3. A Windows Explorer window opens — this is your MT5 data folder
4. Navigate into: `MQL5` → `Experts`
5. **Keep this folder open** — you'll need it in the next step

### Step 5.2 — Copy the EA File

1. Open the `Quant_Trading` project folder
2. Go into `mt5_bridge`
3. Find the file called **`EA_FileBridge.mq5`**
4. **Copy** this file (`Ctrl + C`)
5. **Paste** it into the `MQL5/Experts` folder you opened in Step 5.1 (`Ctrl + V`)

### Step 5.3 — Compile the EA

1. Go back to MetaTrader 5
2. Press **F4** on your keyboard — this opens **MetaEditor** (the code editor)
3. In MetaEditor, on the left panel, find: `Experts` → `EA_FileBridge.mq5`
4. Double-click to open it
5. Press **F7** to compile (or click the **Compile** button)
6. Look at the bottom panel — it should say:
   ```
   0 errors, 0 warnings
   ```
7. ✅ If you see 0 errors, the EA is ready!
8. Close MetaEditor (click the X)

> ⚠️ **If you see errors:** Make sure you copied `EA_FileBridge.mq5` (NOT `EA_ZeroMQ_Bridge.mq5`). The File Bridge has zero external dependencies and should compile without any issues.

### Step 5.4 — Enable Automated Trading

1. In MetaTrader 5, click **Tools** (top menu) → **Options**
2. Click the **Expert Advisors** tab
3. Check ✅ **Allow automated trading**
4. Click **OK**
5. In the main toolbar, find the **Algo Trading** button and click it
6. The button should turn **green** ✅ — this means automated trading is enabled

### Step 5.5 — Attach the EA to a Chart

1. Press **Ctrl + N** to open the **Navigator** panel (left side)
2. Expand **Expert Advisors**
3. Find **EA_FileBridge**
4. **Drag and drop** it onto any open chart (e.g., XAUUSD chart)
5. A settings window appears:
   - Go to the **Inputs** tab
   - You can adjust settings here (see table below), or leave defaults
   - Click **OK**
6. In the **top-right corner** of the chart, you should see the EA name — this means it's running!

### Step 5.6 — Verify the EA is Working

1. Press **Ctrl + T** to open the **Toolbox** at the bottom
2. Click the **Experts** tab
3. You should see messages like:
   ```
   ========================================
   === EA_FileBridge v3.0 PRODUCTION ===
   ========================================
   ```
4. ✅ The EA is running and ready to receive commands from Python!

---

## Part 6: Run the Trading System

### Step 6.1 — Open Command Prompt

1. Open the `Quant_Trading` folder
2. Click the address bar, type `cmd`, press Enter

### Step 6.2 — Start the Bot

Type this command and press Enter:
```
python src/main.py --env live --force-live
```

### Step 6.3 — What to Expect

1. **First launch may take 1-3 minutes** — Python is loading libraries (this is normal!)
2. You should then see:
   ```
   ============================================================
   Initializing Trading System
   ============================================================
   1. Connecting to MT5...
   ✓ Connected to MT5
   ✓ ALL SYSTEMS OPERATIONAL
   Starting main trading loop...
   ```
3. The system is now **live** and trading automatically! 🎉

### Step 6.4 — Stopping the Bot

To stop the trading system:
- Press **Ctrl + C** in the Command Prompt window
- The system will gracefully close all positions and save its state

---

## Part 7: Important Settings

### EA Settings (in MetaTrader 5)

You can change these by right-clicking the chart → **Expert Properties** → **Inputs**:

| Setting | Default | Recommended For Beginners |
|---------|---------|--------------------------|
| EnableTrading | true | Leave as `true` |
| MaxOpenPositions | 10 | Start with `3` |
| MaxDailyLossPercent | 3.0 | Start with `2.0` |
| MaxTradesPerDay | 50 | Start with `10` |

### Python Config (in `config/config_live.yaml`)

Edit this file with Notepad to change trading parameters:

| Setting | What It Controls |
|---------|-----------------|
| `initial_balance` | Your account balance (for risk calculations) |
| `risk_per_trade_pct` | How much to risk per trade (0.005 = 0.5%) |
| `max_daily_loss_pct` | Maximum daily loss before stopping (0.05 = 5%) |
| `max_positions` | Maximum simultaneous trades |

> 💡 **To edit:** Right-click `config_live.yaml` → **Open with** → **Notepad**

---

## ⚠️ Safety Features

This system has **multiple layers of protection**:

1. **EA-Level Protection** (in MetaTrader):
   - Maximum positions limit
   - Daily loss limit — stops trading if you lose too much
   - Daily profit limit — locks in profits
   - Kill switch — instantly disable all trading
   - Panic close — emergency close everything

2. **Python-Level Protection** (in the trading engine):
   - Risk per trade limit (default 0.5%)
   - Circuit breaker — stops after 3 consecutive losses
   - Portfolio exposure limits
   - Automatic state saving (recovers from crashes)

3. **Broker-Level Protection**:
   - Your broker's margin requirements still apply
   - Broker stop-out levels provide final safety net

---

## Common Questions

### "Can I just test it without real money?"

**Yes!** Use a **demo account** from your broker. It works exactly the same but with virtual money.

### "How do I know it's working?"

Look at the Command Prompt window — you'll see logs showing what the system is doing. Also check the **Experts** tab in MT5 for EA messages.

### "How do I stop it?"

Press `Ctrl + C` in the Command Prompt window. Or set `EnableTrading = false` in the EA inputs to stop new trades while keeping existing ones open.

### "What if my computer turns off?"

The system saves its state every 10 seconds. When you restart, it will detect the previous state and reconcile with MT5. Any positions that were opened will still be managed by your broker's stop-loss/take-profit levels.

### "Can I change what it trades?"

Yes — edit `config/config_live.yaml`. Under `symbols:`, you can enable/disable XAUUSD and BTCUSD, and adjust their settings.

### "Where are the trade logs?"

Run this command to see your trading history:
```
python scripts/view_journal.py
```

---

## Troubleshooting

### "python is not recognized"

→ Python wasn't added to PATH. Uninstall Python and reinstall it. In the installer, check **"Add Python to PATH"** at the bottom.

### "pip is not recognized"

→ Same fix as above — reinstall Python with PATH enabled.

### "No module named 'pandas'" or similar

→ Run `pip install -r requirements.txt` again in the project folder.

### "Status file not found - is EA running?"

→ Make sure:
1. MetaTrader 5 is open
2. The EA is attached to a chart (check top-right corner of chart)
3. Algo Trading is enabled (button should be green)

### "Connection timed out"

→ The EA is not responding. Check:
1. Is MT5 open and logged in?
2. Is the EA running? (Check Experts tab in Toolbox)
3. Try removing and re-attaching the EA to the chart

### "AllSYSTEMS OPERATIONAL" but "Waiting for data"

→ This is normal! The system needs about 10 minutes of data (10 bars on 1-minute timeframe) before it starts generating trade signals. Just wait.

### Numbers look wrong in the logs

→ Check that `config/config_live.yaml` has the correct `initial_balance` matching your actual account balance.

---

## File Locations Reference (Windows)

| What | Where |
|------|-------|
| The project | `C:\Users\YourName\Documents\Quant_Trading\` |
| Python config | `C:\Users\YourName\Documents\Quant_Trading\config\config_live.yaml` |
| EA file in MT5 | `...\MQL5\Experts\EA_FileBridge.mq5` (use File → Open Data Folder in MT5) |
| MT5 shared files | `C:\Users\YourName\AppData\Roaming\MetaQuotes\Terminal\Common\Files\` |
| Trade logs | Run `python scripts/view_journal.py` |
