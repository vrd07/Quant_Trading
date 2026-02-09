"""
State Store - File system storage for state persistence.

Implements atomic writes and backup management.
"""

from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime
import json
import shutil


class FileSystemStateStore:
    """
    File-based state storage with atomic writes.
    
    Features:
    - Atomic writes (temp → rename)
    - Timestamped backups
    - Backup rotation (keep last N)
    - Integrity validation
    """
    
    def __init__(self, state_dir: str, max_backups: int = 10):
        """
        Initialize state store.
        
        Args:
            state_dir: Directory for state files
            max_backups: Number of backup files to keep
        """
        self.state_dir = Path(state_dir)
        self.max_backups = max_backups
        
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_file = self.state_dir / "system_state.json"
        self.backup_dir = self.state_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def save(self, state_dict: Dict[str, Any]) -> bool:
        """
        Save state with atomic write.
        
        Process:
        1. Write to temp file
        2. Validate temp file
        3. Create backup of current
        4. Atomic rename temp → current
        5. Cleanup old backups
        
        Args:
            state_dict: State dictionary to save
        
        Returns:
            True if successful
        """
        try:
            # Serialize to JSON
            state_json = json.dumps(state_dict, indent=2, default=str)
            
            # Write to temp file
            temp_file = self.current_file.with_suffix(".tmp")
            with open(temp_file, 'w') as f:
                f.write(state_json)
            
            # Validate temp file
            with open(temp_file, 'r') as f:
                loaded = json.load(f)
                if not self._validate_state_dict(loaded):
                    self.logger.error("State validation failed after write")
                    return False
            
            # Create timestamped backup of current file (if exists)
            if self.current_file.exists():
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                backup_file = self.backup_dir / f"state_{timestamp}.json"
                shutil.copy2(self.current_file, backup_file)
            
            # Atomic rename
            temp_file.replace(self.current_file)
            
            # Cleanup old backups
            self._cleanup_old_backups()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save state: {e}", exc_info=True)
            return False
    
    def load(self) -> Optional[Dict[str, Any]]:
        """
        Load state from current file.
        
        Returns:
            State dictionary or None if file doesn't exist
        
        Raises:
            StateCorruptedError if file is corrupted
        """
        if not self.current_file.exists():
            return None
        
        try:
            with open(self.current_file, 'r') as f:
                state_dict = json.load(f)
            
            if not self._validate_state_dict(state_dict):
                from ..core.exceptions import StateCorruptedError
                raise StateCorruptedError("State file failed validation")
            
            return state_dict
            
        except json.JSONDecodeError as e:
            from ..core.exceptions import StateCorruptedError
            raise StateCorruptedError(f"State file JSON corrupted: {e}")
        except Exception as e:
            self.logger.error(f"Failed to load state: {e}")
            return None
    
    def load_backup(self, backup_file: str) -> Optional[Dict[str, Any]]:
        """Load from specific backup file."""
        backup_path = self.backup_dir / backup_file
        
        if not backup_path.exists():
            return None
        
        try:
            with open(backup_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load backup {backup_file}: {e}")
            return None
    
    def list_backups(self) -> List[str]:
        """
        List backup files sorted by timestamp (newest first).
        
        Returns:
            List of backup filenames
        """
        backups = sorted(
            self.backup_dir.glob("state_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        return [b.name for b in backups]
    
    def _validate_state_dict(self, state_dict: Dict[str, Any]) -> bool:
        """Validate state dictionary has required fields."""
        required_keys = [
            'timestamp',
            'positions',
            'account_balance',
            'account_equity',
            'daily_pnl',
            'kill_switch_active'
        ]
        
        return all(key in state_dict for key in required_keys)
    
    def _cleanup_old_backups(self) -> None:
        """Remove old backup files, keeping only most recent N."""
        backups = sorted(
            self.backup_dir.glob("state_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        for backup in backups[self.max_backups:]:
            backup.unlink()
            self.logger.debug(f"Removed old backup: {backup.name}")
