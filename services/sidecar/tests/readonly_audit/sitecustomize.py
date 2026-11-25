"""B20: loaded into every engine process (and its worker processes) through PYTHONPATH.

When IV_AUDIT_ROOTS is set, a Python audit hook records every attempt to write, create,
delete, rename, re-time or re-permission anything under those roots, one JSON line per event
in IV_AUDIT_LOG. It only records; the test decides. Native code (libvips, ExifTool) is not
visible here: the test's before/after snapshot covers it.
"""

from __future__ import annotations

import json
import os
import sys

_ROOTS = [r for r in os.environ.get("IV_AUDIT_ROOTS", "").split(os.pathsep) if r]
_LOG = os.environ.get("IV_AUDIT_LOG", "")

if _ROOTS and _LOG:
    _norm_roots = [os.path.normcase(os.path.realpath(r)) for r in _ROOTS]
    _WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    _PATH_EVENTS = {
        "os.remove": (0,),
        "os.rmdir": (0,),
        "os.mkdir": (0,),
        "os.utime": (0,),
        "os.chmod": (0,),
        "os.chown": (0,),
        "os.truncate": (0,),
        "os.chflags": (0,),
        "os.rename": (0, 1),
        "os.replace": (0, 1),
        "os.symlink": (1,),
        "os.link": (1,),
        "shutil.copyfile": (1,),
        "shutil.copymode": (1,),
        "shutil.copystat": (1,),
        "shutil.move": (0, 1),
        "shutil.rmtree": (0,),
    }
    _busy = False

    def _inside(p) -> bool:
        if isinstance(p, bytes):
            p = os.fsdecode(p)
        if not isinstance(p, str | os.PathLike):
            return False  # a file descriptor
        try:
            full = os.path.normcase(os.path.realpath(os.fspath(p)))
        except (OSError, ValueError):
            return False
        return any(full == r or full.startswith(r + os.sep) for r in _norm_roots)

    def _record(event: str, args) -> None:
        global _busy
        if _busy:
            return
        _busy = True
        try:
            line = json.dumps({"pid": os.getpid(), "event": event, "args": [repr(a) for a in args]})
            with open(_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        finally:
            _busy = False

    def _hook(event: str, args) -> None:
        if event == "open":
            path, mode, flags = args
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS
            )
            if writes and _inside(path):
                _record(event, args)
        elif event in _PATH_EVENTS:
            if any(i < len(args) and _inside(args[i]) for i in _PATH_EVENTS[event]):
                _record(event, args)

    sys.addaudithook(_hook)
