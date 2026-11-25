"""Exit when the desktop shell that started us is gone, so a crashed or force-killed app
never leaves an orphan sidecar holding the library database open."""

from __future__ import annotations

import os
import threading
import time


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        synchronize, wait_timeout = 0x00100000, 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent(pid: int, interval_s: float = 1.0) -> threading.Thread:
    def loop() -> None:
        while _alive(pid):
            time.sleep(interval_s)
        os._exit(0)  # SQLite WAL is crash-safe; nothing to flush

    t = threading.Thread(target=loop, name="parent-watchdog", daemon=True)
    t.start()
    return t
