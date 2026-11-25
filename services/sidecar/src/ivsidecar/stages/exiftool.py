"""A long-running ExifTool process (`-stay_open True -@ -`).

Starting exiftool.exe per file costs a process launch and Perl start-up each time; one
persistent process answers many requests. Requests are serialised with a lock. A request
that does not answer within `timeout_s` kills the process; the next request restarts it.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path

from ..probes import find_exiftool

_READY = "{ready}"
_CREATE_NO_WINDOW = 0x08000000


class ExifToolError(RuntimeError):
    pass


# ExifTool can write: tag assignments (-Tag=value), -o/-w output files, -overwrite_original and
# friends. The engine only ever reads, so every argument list is checked before it is sent (B20).
_READ_OPTIONS = {"-j", "-n", "-g", "-b", "-charset", "-api", "-execute"}
_TAKES_VALUE = {"-charset", "-api"}
_WRITE_OPTIONS = {
    "-o",
    "-out",
    "-w",
    "-w!",
    "-w+",
    "-overwrite_original",
    "-overwrite_original_in_place",
    "-tagsfromfile",
    "-delete_original",
    "-delete_original!",
    "-restore_original",
    "-geotag",
    "-srcfile",
    "-p",
    "-k",
    "-stay_open",
}


def assert_read_only(args: tuple[str, ...] | list[str]) -> None:
    """Raises ExifToolError for any argument that could make ExifTool write a file."""
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if not a.startswith("-"):
            continue  # a file path
        low = a.lower()
        if low in _WRITE_OPTIONS or any(ch in a for ch in "=<>"):
            raise ExifToolError(f"refusing a writing exiftool argument: {a}")
        if low in _READ_OPTIONS:
            skip = low in _TAKES_VALUE
            continue
        if not all(ch.isalnum() or ch in ":_-" for ch in a[1:]):
            raise ExifToolError(f"refusing an unexpected exiftool argument: {a}")


class ExifTool:
    def __init__(self, exe: str | None = None, timeout_s: float = 30.0):
        self.exe = exe or find_exiftool()
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()

    # -- process management -------------------------------------------------
    def _start(self) -> None:
        if not self.exe:
            raise ExifToolError("exiftool not found")
        kwargs = {"creationflags": _CREATE_NO_WINDOW} if os.name == "nt" else {}
        self._proc = subprocess.Popen(
            [self.exe, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc, self._lines), daemon=True).start()

    @staticmethod
    def _pump(proc: subprocess.Popen, out: queue.Queue) -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            out.put(line.rstrip("\r\n"))
        out.put(None)  # process ended

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.write("-stay_open\nFalse\n")  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def __enter__(self) -> ExifTool:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- requests -----------------------------------------------------------
    def execute(self, *args: str) -> str:
        """Run one ExifTool command; returns its stdout text."""
        assert_read_only(args)
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            cmd = "\n".join([*args, "-execute", ""])
            try:
                self._proc.stdin.write(cmd)  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
            except OSError as e:
                self.close()
                raise ExifToolError(f"exiftool pipe closed: {e}") from e
            out: list[str] = []
            while True:
                try:
                    line = self._lines.get(timeout=self.timeout_s)
                except queue.Empty:
                    self._proc.kill()
                    self._proc = None
                    raise ExifToolError(f"exiftool timed out after {self.timeout_s} s") from None
                if line is None:
                    self._proc = None
                    raise ExifToolError("exiftool exited unexpectedly")
                if line == _READY:
                    return "\n".join(out)
                out.append(line)

    def read_json(self, path: Path) -> dict:
        """All tags of one file, numeric values (-n), grouped by family 0 (-G)."""
        text = self.execute("-j", "-n", "-G", "-charset", "filename=utf8", "-api", "LargeFileSupport=1", str(path))
        try:
            data = json.loads(text) if text.strip() else []
        except json.JSONDecodeError as e:
            raise ExifToolError(f"bad exiftool output: {e}") from e
        return data[0] if data else {}

    def read_binary_tag(self, path: Path, tag: str) -> bytes | None:
        """An embedded binary tag such as PreviewImage, read from stdout of a one-off process.

        Not through the persistent process: its text pipe cannot carry binary data, and
        exiftool's -w option writes files next to the source, which must never happen.
        Only used for camera RAW previews, so the extra process start is acceptable.
        """
        if not self.exe:
            raise ExifToolError("exiftool not found")
        kwargs = {"creationflags": _CREATE_NO_WINDOW} if os.name == "nt" else {}
        # Arguments go through an argfile on stdin, as UTF-8: on Windows, exiftool.exe (Perl)
        # receives command-line arguments in the ANSI code page, which mangles non-ASCII names.
        argv = ["-b", f"-{tag}", "-charset", "filename=utf8", str(path)]
        assert_read_only(argv)
        args = "\n".join(argv) + "\n"
        try:
            res = subprocess.run(
                [self.exe, "-@", "-"],
                input=args.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
                **kwargs,
            )
        except subprocess.TimeoutExpired as e:
            raise ExifToolError(f"exiftool timed out reading {tag}") from e
        return res.stdout or None
