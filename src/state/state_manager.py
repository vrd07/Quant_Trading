"""
State Manager - Persist and restore system state for crash recovery.

Critical Design Principles:
1. State is saved atomically (temp file → rename)
2. State is human-readable (JSON)
3. State includes everything needed to resume
4. Backups are versioned (keep last 10)
5. Load validates integrity
6. Reconciliation happens on restore

State includes:
- Open positions
- Pending orders
- Account balance/equity
- Daily P&L
- Kill switch status
- High water marks

Save triggers:
- Every 60 seconds
- After every order fill
- Before shutdown
- When kill switch triggers
"""

from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import UUID

from ..core.types import SystemState, Position, Order, Symbol
from ..core.constants import OrderStatus, OrderType, OrderSide, PositionSide, MAX_STATE_BACKUPS
from ..core.exceptions import StateError, StateCorruptedError

from .state_store import FileSystemStateStore


class StateManager:
    """
    Manage system state persistence and recovery.
    
    Ensures system can recover gracefully from crashes by:
    - Saving state atomically to prevent corruption
    - Maintaining versioned backups
    - Reconciling with broker on restore
    """
    
    def __init__(self, state_dir: str = "data/state"):
        """
        Initialize state manager.
        
        Args:
            state_dir: Directory for state files
        """
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        # State store
        self.store = FileSystemStateStore(state_dir, max_backups=MAX_STATE_BACKUPS)
        
        # Current state file
        self.current_state_file = self.state_dir / "system_state.json"
        
        # Logging
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def save_state(self, state: SystemState) -> bool:
        """
        Save system state to disk atomically.
        
        Process:
        1. Serialize state to JSON
        2. Write to temporary file
        3. Validate temp file
        4. Atomic rename (overwrites current)
        5. Create timestamped backup
        6. Cleanup old backups
        
        Args:
            state: System state to save
        
        Returns:
            True if successful
        """
        try:
            # Serialize state
            state_dict = self._serialize_state(state)
            
            # Save via store (handles atomic write)
            success = self.store.save(state_dict)
            
            if success:
                self.logger.info(
                    "State saved successfully",
                    positions=len(state.positions),
                    orders=len(state.open_orders),
                    balance=float(state.account_balance),
                    equity=float(state.account_equity)
                )
            else:
                self.logger.error("Failed to save state")
            
            return success
            
        except Exception as e:
            self.logger.error(
                "Error saving state",
                error=str(e),
                exc_info=True
            )
            return False
    
    def load_state(self) -> Optional[SystemState]:
        """
        Load system state from disk.
        
        Returns:
            SystemState if successful, None if no state file
        
        Raises:
            StateCorruptedError if state file is corrupted
        """
        try:
            # Load from store
            state_dict = self.store.load()
            
            if state_dict is None:
                self.logger.info("No saved state found - starting fresh")
                return None
            
            # Deserialize
            state = self._deserialize_state(state_dict)
            
            self.logger.info(
                "State loaded successfully",
                positions=len(state.positions),
                orders=len(state.open_orders),
                timestamp=state.timestamp.isoformat()
            )
            
            return state
            
        except StateCorruptedError:
            # Try to load from backup
            self.logger.error("State file corrupted, attempting backup restore")
            return self._load_from_backup()
            
        except Exception as e:
            self.logger.error(
                "Error loading state",
                error=str(e),
                exc_info=True
            )
            return None
    
    def restore_from_crash(
        self,
        mt5_positions: Dict[str, Position],
        mt5_account_info: Dict[str, Decimal]
    ) -> SystemState:
        """
        Restore state after crash and reconcile with MT5.
        
        Process:
        1. Load last saved state
        2. Get current state from MT5
        3. Reconcile differences
        4. Return merged state
        
        Reconciliation rules:
        - MT5 is source of truth for positions
        - Pending orders are checked against MT5
        - Account balance from MT5
        - Daily P&L preserved from saved state
        
        Args:
            mt5_positions: Current positions from MT5
            mt5_account_info: Current account info from MT5
        
        Returns:
            Reconciled SystemState
        """
        self.logger.info("Starting crash recovery")
        
        # Load saved state
        saved_state = self.load_state()
        
        if saved_state is None:
            # No saved state - create fresh from MT5
            self.logger.warning("No saved state - creating fresh state from MT5")
            saved_state = SystemState(
                account_balance=mt5_account_info.get('balance', Decimal("0")),
                account_equity=mt5_account_info.get('equity', Decimal("0"))
            )
        
        # Reconcile positions
        reconciled_positions = self._reconcile_positions(
            saved_positions=saved_state.positions,
            mt5_positions=mt5_positions
        )
        
        # Reconcile orders (pending orders may have filled during downtime)
        reconciled_orders = self._reconcile_orders(
            saved_orders=saved_state.open_orders,
            mt5_positions=mt5_positions
        )
        
        # Create reconciled state
        reconciled_state = SystemState(
            timestamp=datetime.now(timezone.utc),
            positions=reconciled_positions,
            open_orders=reconciled_orders,
            account_balance=mt5_account_info.get('balance', Decimal("0")),
            account_equity=mt5_account_info.get('equity', Decimal("0")),
            equity_high_water_mark=saved_state.equity_high_water_mark,
            daily_start_equity=saved_state.daily_start_equity,
            daily_pnl=saved_state.daily_pnl,
            total_pnl=saved_state.total_pnl,
            kill_switch_active=saved_state.kill_switch_active,
            circuit_breaker_active=saved_state.circuit_breaker_active,
            last_trade_time=saved_state.last_trade_time,
            metadata={
                'reconciled_at': datetime.now(timezone.utc).isoformat(),
                'crash_recovery': True,
                'saved_state_age_seconds': (
                    datetime.now(timezone.utc) - saved_state.timestamp
                ).total_seconds()
            }
        )
        
        # Save reconciled state
        self.save_state(reconciled_state)
        
        self.logger.info(
            "Crash recovery complete",
            positions_reconciled=len(reconciled_positions),
            orders_reconciled=len(reconciled_orders)
        )
        
        return reconciled_state
    
    def get_state_age(self) -> Optional[float]:
        """
        Get age of saved state in seconds.
        
        Returns:
            Age in seconds, or None if no state exists
        """
        try:
            state = self.load_state()
            if state is None:
                return None
            
            age = (datetime.now(timezone.utc) - state.timestamp).total_seconds()
            return age
        except Exception:
            return None
    
    def get_backup_list(self) -> List[str]:
        """
        Get list of available backups.
        
        Returns:
            List of backup filenames (newest first)
        """
        return self.store.list_backups()
    
    def restore_from_specific_backup(self, backup_filename: str) -> Optional[SystemState]:
        """
        Restore state from a specific backup file.
        
        Args:
            backup_filename: Name of backup file
        
        Returns:
            SystemState if successful, None on failure
        """
        try:
            state_dict = self.store.load_backup(backup_filename)
            state = self._deserialize_state(state_dict)
            
            self.logger.info(
                "Restored from specific backup",
                backup_file=backup_filename,
                timestamp=state.timestamp.isoformat()
            )
            
            return state
            
        except Exception as e:
            self.logger.error(
                "Failed to restore from backup",
                backup_file=backup_filename,
                error=str(e)
            )
            return None
    
    def _reconcile_positions(
        self,
        saved_positions: Dict[UUID, Position],
        mt5_positions: Dict[str, Position]
    ) -> Dict[UUID, Position]:
        """
        Reconcile saved positions with MT5 reality.
        
        MT5 is source of truth.
        """
        reconciled = {}
        discrepancies = []
        
        # Check MT5 positions
        for mt5_pos_id, mt5_pos in mt5_positions.items():
            # Try to find matching saved position
            found = False
            
            for saved_pos_id, saved_pos in saved_positions.items():
                if saved_pos.symbol and mt5_pos.symbol:
                    if saved_pos.symbol.ticker == mt5_pos.symbol.ticker:
                        found = True
                        
                        # Check for discrepancies
                        if saved_pos.quantity != mt5_pos.quantity:
                            discrepancies.append({
                                'type': 'quantity_mismatch',
                                'symbol': mt5_pos.symbol.ticker,
                                'saved': float(saved_pos.quantity),
                                'mt5': float(mt5_pos.quantity)
                            })
                        
                        # Use MT5 data (source of truth)
                        reconciled[saved_pos_id] = mt5_pos
                        break
            
            if not found:
                # Position in MT5 but not in saved state
                discrepancies.append({
                    'type': 'unknown_position',
                    'symbol': mt5_pos.symbol.ticker if mt5_pos.symbol else 'unknown',
                    'quantity': float(mt5_pos.quantity)
                })
                reconciled[mt5_pos.position_id] = mt5_pos
        
        # Check for phantom positions (in saved state but not MT5)
        for saved_pos_id, saved_pos in saved_positions.items():
            if saved_pos.symbol:
                matched = any(
                    p.symbol and p.symbol.ticker == saved_pos.symbol.ticker 
                    for p in reconciled.values()
                )
                if not matched:
                    discrepancies.append({
                        'type': 'phantom_position',
                        'symbol': saved_pos.symbol.ticker,
                        'quantity': float(saved_pos.quantity)
                    })
        
        if discrepancies:
            self.logger.warning(
                "Position reconciliation found discrepancies",
                count=len(discrepancies),
                discrepancies=discrepancies
            )
        
        return reconciled
    
    def _reconcile_orders(
        self,
        saved_orders: Dict[UUID, Order],
        mt5_positions: Dict[str, Position]
    ) -> Dict[UUID, Order]:
        """
        Reconcile saved orders with MT5 state.
        
        Orders may have:
        - Filled (now appear as positions)
        - Cancelled
        - Still pending
        """
        reconciled = {}
        
        for order_id, order in saved_orders.items():
            # Check if order filled (appears as position)
            filled = False
            if order.symbol:
                filled = any(
                    p.symbol and p.symbol.ticker == order.symbol.ticker
                    for p in mt5_positions.values()
                )
            
            if filled:
                # Mark order as filled
                order.status = OrderStatus.FILLED
                self.logger.info(
                    "Order filled during downtime",
                    order_id=str(order_id),
                    symbol=order.symbol.ticker if order.symbol else 'unknown'
                )
            elif order.status in {OrderStatus.PENDING, OrderStatus.SENT}:
                # Assume cancelled if not filled
                order.status = OrderStatus.CANCELLED
                self.logger.info(
                    "Pending order cancelled during downtime",
                    order_id=str(order_id)
                )
            
            # Keep terminal orders for record
            reconciled[order_id] = order
        
        return reconciled
    
    def _serialize_state(self, state: SystemState) -> Dict[str, Any]:
        """Convert SystemState to JSON-serializable dict."""
        return state.to_dict()
    
    def _deserialize_state(self, state_dict: Dict[str, Any]) -> SystemState:
        """Convert dict back to SystemState."""
        # Reconstruct positions
        positions = {}
        for pos_id_str, pos_data in state_dict.get('positions', {}).items():
            symbol = Symbol(ticker=pos_data['symbol']) if pos_data.get('symbol') else None
            
            position = Position(
                position_id=UUID(pos_id_str),
                symbol=symbol,
                side=PositionSide[pos_data['side']],
                quantity=Decimal(pos_data['quantity']),
                entry_price=Decimal(pos_data['entry_price']),
                current_price=Decimal(pos_data['current_price']),
                stop_loss=Decimal(pos_data['stop_loss']) if pos_data.get('stop_loss') else None,
                take_profit=Decimal(pos_data['take_profit']) if pos_data.get('take_profit') else None,
                unrealized_pnl=Decimal(pos_data['unrealized_pnl']),
                realized_pnl=Decimal(pos_data['realized_pnl']),
                opened_at=datetime.fromisoformat(pos_data['opened_at']),
                metadata=pos_data.get('metadata', {})
            )
            
            positions[UUID(pos_id_str)] = position
        
        # Reconstruct orders
        open_orders = {}
        for order_id_str, order_data in state_dict.get('open_orders', {}).items():
            symbol = Symbol(ticker=order_data['symbol']) if order_data.get('symbol') else None
            
            order = Order(
                order_id=UUID(order_id_str),
                symbol=symbol,
                side=OrderSide[order_data['side']] if order_data.get('side') else None,
                order_type=OrderType[order_data['order_type']] if order_data.get('order_type') else OrderType.MARKET,
                quantity=Decimal(order_data['quantity']),
                price=Decimal(order_data['price']) if order_data.get('price') else None,
                stop_loss=Decimal(order_data['stop_loss']) if order_data.get('stop_loss') else None,
                take_profit=Decimal(order_data['take_profit']) if order_data.get('take_profit') else None,
                status=OrderStatus[order_data['status']],
                created_at=datetime.fromisoformat(order_data['created_at']),
                metadata=order_data.get('metadata', {})
            )
            
            open_orders[UUID(order_id_str)] = order
        
        # Create SystemState
        last_trade_time = None
        if state_dict.get('last_trade_time'):
            last_trade_time = datetime.fromisoformat(state_dict['last_trade_time'])
        
        state = SystemState(
            timestamp=datetime.fromisoformat(state_dict['timestamp']),
            positions=positions,
            open_orders=open_orders,
            account_balance=Decimal(state_dict['account_balance']),
            account_equity=Decimal(state_dict['account_equity']),
            equity_high_water_mark=Decimal(state_dict.get('equity_high_water_mark', 0)),
            daily_start_equity=Decimal(state_dict.get('daily_start_equity', 0)),
            daily_pnl=Decimal(state_dict['daily_pnl']),
            total_pnl=Decimal(state_dict.get('total_pnl', 0)),
            kill_switch_active=state_dict['kill_switch_active'],
            circuit_breaker_active=state_dict.get('circuit_breaker_active', False),
            last_trade_time=last_trade_time,
            metadata=state_dict.get('metadata', {})
        )
        
        return state
    
    def _load_from_backup(self) -> Optional[SystemState]:
        """Attempt to load from most recent backup."""
        backups = self.store.list_backups()
        
        for backup_file in backups:
            try:
                state_dict = self.store.load_backup(backup_file)
                state = self._deserialize_state(state_dict)
                
                self.logger.info(
                    "Loaded state from backup",
                    backup_file=backup_file
                )
                return state
                
            except Exception as e:
                self.logger.error(
                    "Failed to load backup",
                    backup_file=backup_file,
                    error=str(e)
                )
                continue
        
        self.logger.error("All backups failed - cannot restore state")
        return None
