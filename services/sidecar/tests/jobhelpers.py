"""Stage functions for job-queue tests. Module-level so spawned worker processes can import them."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ivsidecar.worker import run_stage


def crash_on_bad(stage, payload, ctx):
    """Kills the worker process (like a native segfault) for files named *crash*."""
    if "crash" in Path(payload["path"]).name and stage == "identify":
        os._exit(3)
    return run_stage(stage, payload, ctx)


def flaky(stage, payload, ctx):
    """An ordinary, retryable error for files named *flaky*."""
    if "flaky" in Path(payload["path"]).name and stage == "identify":
        raise OSError("file is locked by another program")
    return run_stage(stage, payload, ctx)


def hang(stage, payload, ctx):
    if "hang" in Path(payload["path"]).name and stage == "identify":
        time.sleep(60)
    return run_stage(stage, payload, ctx)
