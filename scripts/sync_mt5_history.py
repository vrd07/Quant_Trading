#!/usr/bin/env python3
"""
Sync MT5 History - Reconstruct trade journal from MT5 history data.

This script fetches historical deals from MT5 via the bridge and
updates/backfills data/logs/trade_journal.csv to ensure accurate P&L
and correct strategy mapping.

Usage:
    python scripts/sync_mt5_history.py --days 30
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import csv

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.connectors.mt5_connector import get_mt5_connector
from src.monitoring.logger import get_logger

logger = get_logger("sync_mt5_history")
JOURNAL_FILE = PROJECT_ROOT / "data" / "logs" / "trade_journal.csv"

def init_connector():
    """Initialize MT5 connector."""
    try:
        connector = get_mt5_connector()
        if not connector.connect():
            logger.error("Failed to connect to MT5.")
            sys.exit(1)
        return connector
    except Exception as e:
        logger.error(f"Error connecting to MT5: {e}")
        sys.exit(1)

def get_history_deals(connector, days: int):
    """Fetch history deals from MT5."""
    minutes = days * 1440
    logger.info(f"Fetching history for the last {days} days ({minutes} minutes)...")
    deals = connector.get_closed_positions(minutes=minutes)
    
    if not deals:
        logger.warning("No historical deals found or failed to fetch.")
        return []
    
    logger.info(f"Retrieved {len(deals)} out deals from MT5.")
    return deals

def parse_mt5_comment(comment: str):
    """Extract strategy from MT5 comment if possible."""
    if not comment or comment == "PythonBridge":
        return "unknown"
    
    # Format typically: strategy|uuid
    if "|" in comment:
        parts = comment.split("|", 1)
        return parts[0]
    return comment

def sync_journal(deals: list):
    """Read existing journal, backfill/update data, and save."""
    if not JOURNAL_FILE.exists():
        logger.warning(f"Journal file {JOURNAL_FILE} does not exist. Cannot sync.")
        return
        
    try:
        df = pd.read_csv(JOURNAL_FILE)
    except Exception as e:
        logger.error(f"Error reading journal CSV: {e}")
        return
        
    if df.empty:
        logger.warning("Journal is empty.")
        return

    # Index deals by ticket/position_ticket
    deal_map = {str(d.get("position_ticket", "")): d for d in deals if "position_ticket" in d}
    
    updated_count = 0
    fixed_strategies = 0
    
    # Iterate through journal and update
    for idx, row in df.iterrows():
        ticket = str(row.get("mt5_ticket", ""))
        
        if ticket in deal_map:
            deal = deal_map[ticket]
            
            # 1. Update Strategy
            current_strat = str(row.get("strategy", "unknown"))
            deal_comment = deal.get("comment", "")
            extracted_strat = parse_mt5_comment(deal_comment)
            
            if current_strat == "unknown" and extracted_strat != "unknown":
                df.at[idx, "strategy"] = extracted_strat
                fixed_strategies += 1
            
            # 2. Update P&L metrics with exact values
            profit = float(deal.get("profit", 0))
            swap = float(deal.get("swap", 0))
            commission = float(deal.get("commission", 0))
            realized_pnl = profit + swap + commission
            
            old_pnl = float(row.get("realized_pnl", 0))
            
            # Only update if the difference is significant (MT5 P&L is exact)
            if abs(old_pnl - realized_pnl) > 0.01:
                df.at[idx, "realized_pnl"] = realized_pnl
                updated_count += 1
                
                # Recalculate pnl_pct
                entry_price = float(row.get("entry_price", 0))
                quantity = float(row.get("quantity", 0))
                # Approximate value_per_lot since we don't have Symbol object here
                # Standard FX is usually 100,000, XAU is 100
                symbol = str(row.get("symbol", ""))
                val_per_lot = 100 if "XAU" in symbol else 100000
                
                if entry_price > 0 and quantity > 0:
                    pnl_pct = (realized_pnl / (entry_price * quantity * val_per_lot)) * 100
                    df.at[idx, "pnl_pct"] = pnl_pct
            
            # 3. Update Exit Price
            exit_price = float(deal.get("price", 0))
            if exit_price > 0 and row.get("exit_reason") == "closed_on_broker":
                df.at[idx, "exit_price"] = exit_price
                
            # 4. Update Exit Time
            deal_time = deal.get("time", 0)
            if deal_time > 0:
                dt = datetime.fromtimestamp(deal_time, tz=timezone.utc)
                df.at[idx, "exit_time"] = dt.isoformat()
                
    # Save back
    if updated_count > 0 or fixed_strategies > 0:
        df.to_csv(JOURNAL_FILE, index=False)
        logger.info(f"Successfully updated {updated_count} P&L records and fixed {fixed_strategies} strategy labels.")
    else:
        logger.info("No updates were necessary. Journal is already in sync.")

def main():
    parser = argparse.ArgumentParser(description="Sync MT5 History to Trade Journal")
    parser.add_argument("--days", type=int, default=30, help="Days of history to fetch (default: 30)")
    args = parser.parse_args()
    
    logger.info("Starting MT5 History Sync...")
    connector = init_connector()
    deals = get_history_deals(connector, args.days)
    sync_journal(deals)
    connector.disconnect()
    logger.info("Sync complete.")

if __name__ == "__main__":
    main()
