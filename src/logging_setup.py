from __future__ import annotations

import logging
import sys

# Attributes present on every LogRecord — never render these as extra fields.
_STDLIB_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class _VerboseFormatter(logging.Formatter):
    """Appends all extra fields as key=value pairs after the log message."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STDLIB_ATTRS and not k.startswith("_")
        }
        if extras:
            fields = " ".join(f"{k}={v!r}" for k, v in sorted(extras.items()))
            return f"{base}  [{fields}]"
        return base


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_VerboseFormatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    ))
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)
