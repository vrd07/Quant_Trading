# 📈 Quant Trading Bot — Windows 11 Setup Guide

> **This guide is written for non-technical users who want to run the trading bot on a fresh Windows 11 PC.**
> No coding knowledge needed — just follow each step in order.

---

## ⚠️ Important Disclaimer

> This bot trades **real money** on a live account. You can lose your entire balance.
> Only run this if you fully understand the risks and have tested everything first.

---

## 🗂️ What This Bot Does

This is an **automated gold (XAUUSD) trading bot** built for the **The5ers $5,000 prop firm challenge**. It:

- Connects to MetaTrader 5 (MT5) on your PC
- Automatically analyzes gold prices 24/7
- Places and manages trades using multiple strategies
- Enforces strict risk rules (daily loss limits, trailing stops, etc.)
- Targets a **$400 profit** while staying within a **5% daily loss** and **10% drawdown** limit

---

## 🖥️ Step 1 — What You Need

Before you start, make sure you have:

| Requirement | Where to Get It |
|---|---|
| Windows 11 PC | Your current PC |
| Internet connection | Your router/WiFi |
| MetaTrader 5 | [Download here](https://www.metatrader5.com/en/download) |
| A The5ers MT5 account | [The5ers website](https://the5ers.com/) |
| Python 3.11 | [Download here](https://www.python.org/downloads/) |
| Git | [Download here](https://git-scm.com/download/win) |

---

## 🐍 Step 2 — Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click **"Download Python 3.11.x"**
3. Run the installer
4. ✅ **VERY IMPORTANT:** On the first screen, tick **"Add Python to PATH"** before clicking Install

   ![Python PATH checkbox — must be ticked](https://www.python.org/static/img/python-logo.png)

5. Click **"Install Now"**
6. When done, click **"Close"**

**Verify it worked:** Press `Win + R`, type `cmd`, press Enter. Then type:
```
python --version
```
You should see something like `Python 3.11.9`. If you get an error, Python was not added to PATH — reinstall and tick the box.

---

## 📥 Step 3 — Download the Bot

1. Press `Win + R`, type `cmd`, press Enter (this opens the Command Prompt)
2. Type these commands one at a time, pressing **Enter** after each:

```
cd %USERPROFILE%\Documents
git clone https://github.com/vrd07/Quant_Trading.git
cd Quant_Trading
```

You should now be inside the bot's folder.

---

## 📦 Step 4 — Install Bot Dependencies

Still in the Command Prompt (inside the `Quant_Trading` folder), type:

```
pip install -r requirements.txt
```

Wait for everything to install. This may take 2–3 minutes. You'll see a lot of text scrolling — that's normal.

---

## 📉 Step 5 — Install & Set Up MetaTrader 5

1. Download MT5 from [metatrader5.com](https://www.metatrader5.com/en/download)
2. Install it like a normal Windows app
3. Open MT5 and **log in with your The5ers account credentials**
4. Open the **EA (Expert Advisor) bridge file:**
   - In MT5, click **File → Open Data Folder**
   - Navigate to `MQL5 → Experts`
   - Copy the file `mt5_bridge\EA_FileBridge.mq5` from the bot folder into this `Experts` folder
5. Back in MT5, go to **Tools → Options → Expert Advisors** and tick:
   - ✅ Allow automated trading
   - ✅ Allow DLL imports
6. In the **Navigator** panel (left side), expand **Expert Advisors**, find `EA_FileBridge`, and drag it onto the **XAUUSD** chart
7. A dialog appears — click **OK**
8. You should see a smiley face 🙂 in the top-right of the chart, meaning the EA is running

---

## ⚙️ Step 6 — Configure the Bot

1. Open the folder `Quant_Trading\config\` in File Explorer
2. Right-click `config_live_5000.yaml` → **Open with → Notepad**
3. Scroll to the very bottom and find the `file_bridge` section

**✅ Easiest option (works for everyone, including names with spaces like "Shubham Ghodke"):**

Just put a `#` in front of the macOS `data_dir` line to disable it. Leave everything else as-is. The bot will **automatically find the correct MT5 folder** on Windows — no path editing needed.

It should look like this after your edit:

```yaml
file_bridge:
  enabled: true
  # macOS (disabled — add # to the front of this line):
  # data_dir: "~/Library/Application Support/..."
```

4. Save the file (`Ctrl + S`) and close Notepad

> **If auto-detect ever fails** (the health check shows ❌ for the bridge directory), you can manually set the path:
> ```yaml
> data_dir: "C:/Users/Shubham Ghodke/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
> ```
> Spaces in your Windows username are perfectly fine here — just type it exactly as it appears.
>
> Not sure of your exact username? Open Command Prompt and type: `echo %USERNAME%`

---

## ▶️ Step 7 — Run the Bot

**Option A — Double-click (easiest):**

1. Open the `scripts` folder inside `Quant_Trading`
2. Double-click **`start_live.bat`**
3. A black window will appear asking: *"Are you ABSOLUTELY SURE you want to trade live? (type YES)"*
4. Type `YES` and press Enter
5. The bot is now running! 🎉

**Option B — PowerShell (nicer looking):**

1. Right-click **`scripts\start_live.ps1`**
2. Click **"Run with PowerShell"**
3. If Windows asks about execution policy, type `R` and press Enter
4. Type `YES` when prompted

> **Do not close the black window** while the bot is running. Closing it stops the bot.

---

## 🏥 Step 8 — Health Check (Run Before Every Session)

Before starting the bot each day, run a quick health check to make sure everything is working:

1. Open Command Prompt in the `Quant_Trading` folder
2. Type:
```
python scripts\health_check.py --config config\config_live_5000.yaml
```
3. You should see all `✅ PASS` lines. If you see a `❌ FAIL`, do not run the bot until it's fixed.

---

## 📋 Step 9 — View Your Trades

To see your trade history and performance:

```
python scripts\view_journal.py
```

Or for detailed log analysis:

```
python scripts\analyze_logs.py
```

---

## 🔴 How to Stop the Bot

- **Cleanly:** Press `Ctrl + C` in the black window. The bot will save its state and close all positions if configured to do so.
- **Emergency:** Close the black window directly (less clean — use only in emergency).

---

## 🔄 Auto-Start at Windows Login (Optional)

If you want the bot to start automatically every time you turn on your PC:

1. Open **Task Scheduler** (search it in the Start menu)
2. Click **"Import Task..."** on the right panel
3. Select the file `deployment\windows_task.xml`
4. **Edit the paths** inside the task to match your actual Python and folder locations
5. Click **OK**

---

## 🆘 Common Problems & Fixes

| Problem | Fix |
|---|---|
| `python is not recognized` | Reinstall Python and tick **"Add to PATH"** |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` again |
| Health check shows MT5 status file missing | Make sure MT5 is open and the EA_FileBridge is running on the chart |
| Bot closes immediately | Read the error message in the black window carefully, it will tell you what's wrong |
| `❌ FAIL Bridge directory exists` | Double-check the `data_dir` path in the config — your username might be wrong |
| EA shows 🙁 (sad face) in MT5 | Go to Tools → Options → Expert Advisors and enable automated trading |

---

## 📁 Folder Structure (What Everything Is)

```
Quant_Trading/
├── config/
│   └── config_live_5000.yaml    ← Main settings file (edit this)
├── data/
│   └── logs/                    ← Trading logs and journal
├── mt5_bridge/
│   └── EA_FileBridge.mq5        ← Copy this into MT5's Experts folder
├── scripts/
│   ├── start_live.bat           ← Double-click to start (Windows)
│   ├── start_live.ps1           ← PowerShell launcher
│   ├── health_check.py          ← Run this before every session
│   ├── view_journal.py          ← See your trade history
│   └── analyze_logs.py          ← See strategy performance
└── src/
    └── main.py                  ← The bot's brain (don't edit this)
```

---

## 📞 Need Help?

If something doesn't work, take a screenshot of the error message in the black window and send it. The most useful info is:
1. The **exact error message** (the red text)
2. Which **step** you were on when it happened

---

*Last updated: March 2026 — Windows 11 compatible*
