"""Kill switch for emergency trading halt.

Once triggered, requires MANUAL intervention to reset.
"""

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Optional


class KillSwitch:
    """
    Emergency kill switch that halts all trading.
    
    Once triggered, this requires manual intervention (file deletion) to reset.
    This is intentional - you should NOT be able to programmatically re-enable trading
    after a critical risk event.
    """
    
    KILL_SWITCH_FILE = Path("data/state/kill_switch.json")
    
    def __init__(self):
        """Initialize kill switch state."""
        self._active = False
        self._triggered_at: Optional[datetime] = None
        self._reason: Optional[str] = None
        
        # Check if kill switch file exists (persistent state)
        self._load_state()
    
    def _load_state(self) -> None:
        """Load kill switch state from file if exists."""
        if self.KILL_SWITCH_FILE.exists():
            try:
                with open(self.KILL_SWITCH_FILE, 'r') as f:
                    data = json.load(f)
                    self._active = data.get('active', False)
                    if data.get('triggered_at'):
                        self._triggered_at = datetime.fromisoformat(data['triggered_at'])
                    self._reason = data.get('reason')
            except (json.JSONDecodeError, IOError):
                # If file is corrupted, assume kill switch is active (fail-safe)
                self._active = True
                self._reason = "Kill switch file corrupted - assuming active"
    
    def _save_state(self) -> None:
        """Persist kill switch state to file."""
        self.KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.KILL_SWITCH_FILE, 'w') as f:
            json.dump({
                'active': self._active,
                'triggered_at': self._triggered_at.isoformat() if self._triggered_at else None,
                'reason': self._reason
            }, f, indent=2)
    
    def is_active(self) -> bool:
        """Check if kill switch is active."""
        # Re-check file in case it was manually modified
        self._load_state()
        return self._active
    
    def trigger(self, reason: str) -> None:
        """
        Trigger the kill switch - EMERGENCY HALT.
        
        Args:
            reason: Why the kill switch was triggered
        """
        self._active = True
        self._triggered_at = datetime.now(timezone.utc)
        self._reason = reason
        self._save_state()
    
    def get_status(self) -> dict:
        """Get current kill switch status."""
        return {
            'active': self._active,
            'triggered_at': self._triggered_at.isoformat() if self._triggered_at else None,
            'reason': self._reason
        }
    
    def reset(self) -> None:
        """
        Reset the kill switch.
        
        WARNING: This should only be called after manual review.
        In production, require kill switch file deletion instead.
        """
        if self.KILL_SWITCH_FILE.exists():
            self.KILL_SWITCH_FILE.unlink()
        
        self._active = False
        self._triggered_at = None
        self._reason = None
