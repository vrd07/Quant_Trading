"""
Heartbeat Monitor for MT5 Connection.

Periodically checks if MT5 is responding and raises alerts if not.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .mt5_connector import MT5Connector
from ..core.exceptions import ConnectionLostError, HeartbeatTimeoutError


logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """
    Background thread that monitors MT5 connection health.
    
    Sends periodic heartbeat requests and tracks response times.
    Raises exception if heartbeat fails for too long.
    """
    
    def __init__(
        self,
        connector: MT5Connector,
        interval_seconds: int = 10,
        timeout_seconds: int = 30,
        on_connection_lost: Optional[Callable] = None
    ):
        """
        Initialize heartbeat monitor.
        
        Args:
            connector: MT5Connector instance to monitor
            interval_seconds: How often to send heartbeat
            timeout_seconds: How long to wait before considering connection lost
            on_connection_lost: Callback function to call if connection lost
        """
        self.connector = connector
        self.interval = interval_seconds
        self.timeout = timeout_seconds
        self.on_connection_lost = on_connection_lost
        
        self.running = False
        self._stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_successful_heartbeat: Optional[datetime] = None
        self.consecutive_failures = 0
        self.max_failures = 3
        
        logger.info(
            "HeartbeatMonitor initialized: interval=%ds, timeout=%ds, max_failures=%d",
            interval_seconds, timeout_seconds, self.max_failures
        )
    
    def start(self) -> None:
        """Start heartbeat monitoring in background thread."""
        if self.running:
            logger.warning("Heartbeat monitor already running, ignoring start() call")
            return
        
        logger.info("Starting heartbeat monitor")
        self.running = True
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="HeartbeatMonitor")
        self.thread.start()
        logger.info("Heartbeat monitor started successfully")
    
    def stop(self) -> None:
        """Stop heartbeat monitoring."""
        if not self.running:
            logger.debug("Heartbeat monitor not running, ignoring stop() call")
            return
        
        logger.info("Stopping heartbeat monitor")
        self.running = False
        self._stop_event.set()
        
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                logger.warning("Heartbeat monitor thread did not stop cleanly within timeout")
            else:
                logger.info("Heartbeat monitor stopped successfully")
        
        self.thread = None
    
    def _run(self) -> None:
        """Main heartbeat loop (runs in background thread)."""
        logger.info("Heartbeat monitor loop started")
        
        while self.running and not self._stop_event.is_set():
            try:
                # Send heartbeat
                logger.debug("Sending heartbeat to MT5")
                success = self.connector.heartbeat()
                
                if success:
                    self.last_successful_heartbeat = datetime.now(timezone.utc)
                    self.consecutive_failures = 0
                    logger.debug(
                        "Heartbeat successful at %s",
                        self.last_successful_heartbeat.isoformat()
                    )
                else:
                    logger.warning("Heartbeat failed")
                    self._handle_heartbeat_failure()
                
            except Exception as e:
                logger.error("Heartbeat exception: %s", e, exc_info=True)
                self._handle_heartbeat_failure(error=str(e))
            
            # Sleep until next heartbeat, using Event.wait() for clean shutdown
            self._stop_event.wait(timeout=self.interval)
        
        logger.info("Heartbeat monitor loop ended")
    
    def _handle_heartbeat_failure(self, error: Optional[str] = None) -> None:
        """Handle failed heartbeat."""
        self.consecutive_failures += 1
        
        logger.warning(
            "Heartbeat failure #%d (max: %d)%s",
            self.consecutive_failures,
            self.max_failures,
            f" - Error: {error}" if error else ""
        )
        
        if self.consecutive_failures >= self.max_failures:
            # Connection is definitely lost
            logger.error(
                "Connection lost after %d consecutive heartbeat failures",
                self.consecutive_failures
            )
            
            if self.on_connection_lost:
                logger.info("Calling connection lost callback")
                try:
                    self.on_connection_lost()
                except Exception as callback_error:
                    logger.error(
                        "Connection lost callback raised exception: %s",
                        callback_error,
                        exc_info=True
                    )
            
            raise ConnectionLostError(
                f"Heartbeat failed {self.consecutive_failures} times",
                last_success=self.last_successful_heartbeat.isoformat() if self.last_successful_heartbeat else None,
                error=error
            )
    
    def is_healthy(self) -> bool:
        """
        Check if connection is currently healthy.
        
        Returns:
            True if recent heartbeat successful
        """
        if self.last_successful_heartbeat is None:
            logger.debug("Connection not healthy: no successful heartbeat yet")
            return False
        
        age = (datetime.now(timezone.utc) - self.last_successful_heartbeat).total_seconds()
        healthy = age < self.timeout
        
        if not healthy:
            logger.warning(
                "Connection not healthy: last heartbeat %.1fs ago (timeout: %ds)",
                age, self.timeout
            )
        else:
            logger.debug("Connection healthy: last heartbeat %.1fs ago", age)
        
        return healthy
    
    def get_status(self) -> dict:
        """
        Get current heartbeat status.
        
        Returns:
            {
                'healthy': bool,
                'last_success': datetime,
                'seconds_since_success': float,
                'consecutive_failures': int
            }
        """
        if self.last_successful_heartbeat:
            age = (datetime.now(timezone.utc) - self.last_successful_heartbeat).total_seconds()
        else:
            age = float('inf')
        
        status = {
            'healthy': self.is_healthy(),
            'last_success': self.last_successful_heartbeat,
            'seconds_since_success': age,
            'consecutive_failures': self.consecutive_failures
        }
        
        logger.debug("Heartbeat status: %s", status)
        return status
