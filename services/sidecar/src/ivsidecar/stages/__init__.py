"""Per-file processing stages (doc 06 §1, stages 1-4).

Each stage is a plain function: one file in, a result or a `StageError` out. No database
access here; the job queue (B5) persists results. Source files are only ever opened for
reading.
"""

PIPELINE_VERSION = "1"


class StageError(Exception):
    """Expected, per-file failure (corrupt, unsupported, too large). Not retried."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
