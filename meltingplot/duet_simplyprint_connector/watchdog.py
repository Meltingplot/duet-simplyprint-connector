"""Watchdog timer implementation for the Meltingplot Duet SimplyPrint.io Connector."""

import _thread
import asyncio
import threading
import time

# Polling interval for the watchdog thread (seconds)
WATCHDOG_CHECK_INTERVAL = 1


class Watchdog:
    """A simple watchdog timer that raises KeyboardInterrupt if the timer expires."""

    def __init__(self, timeout: float):
        """Initialize the watchdog with a timeout in seconds."""
        self.timeout = timeout
        self._next_reset = time.monotonic() + self.timeout
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._watchdog_thread, daemon=True)

    def start(self):
        """Start the watchdog thread."""
        self._thread.start()

    def stop(self):
        """Stop the watchdog thread."""
        self._stop_event.set()
        self._thread.join()

    async def reset(self):
        """Reset the watchdog timer asynchronously."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.reset_sync)

    def reset_sync(self, offset: float = 0):
        """Reset the watchdog timer synchronously."""
        with self._lock:
            self._next_reset = time.monotonic() + self.timeout + offset

    def _watchdog_thread(self):
        """Thread that checks the watchdog timer."""
        while not self._stop_event.is_set():
            due = False
            with self._lock:
                due = self._next_reset - time.monotonic() <= 0
            if due:
                # Raise KeyboardInterrupt in main thread
                _thread.interrupt_main()
                break
            time.sleep(WATCHDOG_CHECK_INTERVAL)  # Sleep for a short duration to avoid busy waiting
