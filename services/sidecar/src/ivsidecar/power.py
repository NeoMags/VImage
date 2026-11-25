"""User-idle time and mains power, for the "only when idle" / "only on AC" settings.

Windows only (the target platform). Elsewhere both return None = unknown, and the queue
treats unknown as allowed so development on Linux is not blocked.
"""

from __future__ import annotations

import sys

IDLE_AFTER_S = 120  # "idle" = no keyboard or mouse input for 2 minutes [proposed]


def idle_seconds() -> float | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def on_ac_power() -> bool | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    st = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return None
    line = st.ACLineStatus & 0xFF
    return None if line == 255 else line == 1  # 0 offline, 1 online, 255 unknown


def gate_reason(only_when_idle: bool, only_on_ac: bool) -> str | None:
    """Why background work must wait right now, or None if it may run."""
    if only_on_ac and on_ac_power() is False:
        return "on_battery"
    if only_when_idle:
        idle = idle_seconds()
        if idle is not None and idle < IDLE_AFTER_S:
            return "user_active"
    return None
