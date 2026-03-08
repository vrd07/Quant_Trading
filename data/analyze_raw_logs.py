import re
import glob
from collections import defaultdict

def parse_logs():
    log_files = glob.glob("/Users/varadbandekar/Documents/Quant_trading/data/logs/trading_system_live.log*")
    # Sort files so .log is last, .log.1 before it, etc (reverse numerical order)
    log_files.sort(key=lambda x: -int(x.split('.')[-1]) if x[-1].isdigit() else 0)

    trades = []
    
    # Regex to capture "Position closed | position_id=xxx | symbol=XAUUSD | ... | realized_pnl=0.0961"
    pos_close_re = re.compile(r'(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| INFO     \| src\..+ \| Position closed \|.*position_id=(?P<position_id>[\w-]+).*symbol=(?P<symbol>[A-Z]+).*realized_pnl=(?P<pnl>-?[\d\.]+)')
    
    for file in log_files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                for line in f:
                    match = pos_close_re.search(line)
                    if match:
                        trades.append(match.groupdict())
        except Exception as e:
            print(f"Error reading {file}: {e}")

    print(f"Found {len(trades)} position close events in live logs.")
    
    wins = 0
    losses = 0
    total_pnl = 0.0
    symbols = defaultdict(float)

    for t in trades:
        pnl = float(t['pnl'])
        total_pnl += pnl
        symbols[t['symbol']] += pnl
        
        if pnl > 0:
            wins += 1
        elif pnl <= 0:
            losses += 1

    print("\n=== LIVE LOG METRICS ===")
    print(f"Total Trades: {len(trades)}")
    print(f"Wins: {wins}, Losses: {losses}")
    print(f"Win Rate: {(wins/len(trades)*100):.2f}%" if len(trades) > 0 else "0%")
    print(f"Total PNL: ${total_pnl:.2f}")
    
    print("\n=== SYMBOL PNL ===")
    for sym, pnl in symbols.items():
        print(f"{sym}: ${pnl:.2f}")

    print("\n=== LATEST 20 LOSING TRADES ===")
    losers = [t for t in trades if float(t['pnl']) < 0][-20:]
    for l in losers:
        print(f"Time: {l['time']} | Symbol: {l['symbol']} | PNL: ${float(l['pnl']):.2f}")

if __name__ == "__main__":
    parse_logs()
