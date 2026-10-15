"""Single-instance locking and cooperative stop requests for the Linux driver.

No hardware or project runtime modules are imported here. Acquire the lock
before importing the driver modules: some of those create devices/sockets.
The descriptor stays open until process exit, including the driver's cleanup.
This lock is separate from the onboard-memory operation lock.
"""
from __future__ import annotations

import atexit
import errno
import fcntl
import os
from pathlib import Path
import signal
import stat
import threading
from types import FrameType
from typing import Any
from driver_service import start_service_watchdog


class DriverAlreadyRunning(RuntimeError):
    """Another driver in the same user runtime directory owns the lock."""


class DriverLifecycle:
    def __init__(self) -> None:
        self.stop_requested = False
        self.stop_signal: int | None = None
        self._lock_fd: int | None = None
        self._previous_handlers: dict[int, Any] = {}

    @property
    def stop_reason(self) -> str:
        if self.stop_signal is None:
            return "stop requested"
        return signal.Signals(self.stop_signal).name

    def request_stop(self) -> None:
        """Request normal cleanup without raising or doing work in a thread."""
        self.stop_requested = True

    def _request_stop(
        self,
        signum: int,
        _frame: FrameType | None,
    ) -> None:
        # Do not raise, print, acquire a thread lock, or touch hardware here.
        # Repeated signals must not interrupt the existing shutdown cleanup.
        self.stop_requested = True
        if self.stop_signal is None:
            self.stop_signal = signum

    def start(self) -> None:
        if self._lock_fd is not None:
            raise RuntimeError("Driver lifecycle is already started.")

        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Start the driver from the main Python thread.")

        runtime_dir = Path(
            os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        )
        if not runtime_dir.is_absolute():
            raise RuntimeError("XDG_RUNTIME_DIR must be an absolute path.")

        info = runtime_dir.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError(
                "The driver runtime directory must belong to the current user."
            )

        lock_path = runtime_dir / f"g13-driver-{os.getuid()}.lock"
        fd = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            lock_info = os.fstat(fd)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid():
                raise RuntimeError("The driver lock is not a user-owned regular file.")

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise DriverAlreadyRunning(
                        "G13 driver is already running. "
                        "No second instance was started."
                    ) from None
                raise
        except BaseException:
            os.close(fd)
            raise

        self._lock_fd = fd
        self.stop_requested = False
        self.stop_signal = None
        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                self._previous_handlers[signum] = signal.signal(
                    signum, self._request_stop
                )
            atexit.register(self.close)
            start_service_watchdog(self.request_stop)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release at process exit; do not call before driver cleanup finishes."""
        try:
            for signum, handler in self._previous_handlers.items():
                signal.signal(signum, handler)
        finally:
            self._previous_handlers.clear()
            fd = self._lock_fd
            self._lock_fd = None
            if fd is not None:
                os.close(fd)
            atexit.unregister(self.close)
            # Never unlink the lock path: replacing its inode defeats flock.
