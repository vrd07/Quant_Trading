import re
import glob
from collections import defaultdict

def parse_logs():
    log_files = glob.glob("/Users/varadbandekar/Documents/Quant_trading/data/logs/trading_system_live.log*")
    log_files.sort(key=lambda x: -int(x.split('.')[-1]) if x[-1].isdigit() else 0)

    # Track strategies by signal ID or order ID
    strategy_map = {}
    
    # Regex for signals and orders to map IDs to strategies
    order_re = re.compile(r'order_id=(?P<order_id>[\w-]+) \| strategy=(?P<strategy>\w+)')
    
    # Regex for position closes
    pos_close_re = re.compile(r'(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| INFO     \| src\..+ \| Position closed \|.*position_id=(?P<position_id>[\w-]+).*symbol=(?P<symbol>[A-Z]+).*realized_pnl=(?P<pnl>-?[\d\.]+)')
    
    trades = []

    for file in log_files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                for line in f:
                    match_order = order_re.search(line)
                    if match_order:
                        strategy_map[match_order.group('order_id')] = match_order.group('strategy')
                        
                    match_pos = pos_close_re.search(line)
                    if match_pos:
                        d = match_pos.groupdict()
                        # MT5 position_id is usually the order_id that opened it
                        d['strategy'] = strategy_map.get(d['position_id'], 'unknown')
                        trades.append(d)
        except Exception as e:
            print(f"Error reading {file}: {e}")

    strat_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0})

    for t in trades:
        pnl = float(t['pnl'])
        strat = t['strategy']
        strat_stats[strat]['pnl'] += pnl
        if pnl > 0:
            strat_stats[strat]['wins'] += 1
        elif pnl < 0:
            strat_stats[strat]['losses'] += 1

    print("\n=== STRATEGY PERFORMANCE ===")
    for strat, stats in strat_stats.items():
        total = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total * 100) if total > 0 else 0
        print(f"Strategy: {strat.ljust(20)} | Trades: {total:3d} | WR: {win_rate:5.1f}% | Net PNL: ${stats['pnl']:6.2f}")

if __name__ == "__main__":
    parse_logs()
