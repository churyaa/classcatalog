from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol


class EventLogger(Protocol):
    def info(self, event: str, **values: object) -> None: ...

    def warning(self, event: str, **values: object) -> None: ...

    def error(self, event: str, **values: object) -> None: ...


class _StandardEventLogger:
    def __init__(self, *, json_output: bool) -> None:
        self._logger = logging.getLogger("classcatalog.scraper")
        self._json_output = json_output

    def _write(self, level: int, event: str, values: Mapping[str, object]) -> None:
        if self._json_output:
            message = json.dumps({"event": event, **values}, default=str, sort_keys=True)
        else:
            suffix = " ".join(f"{key}={value!r}" for key, value in values.items())
            message = f"{event} {suffix}".rstrip()
        self._logger.log(level, message)

    def info(self, event: str, **values: object) -> None:
        self._write(logging.INFO, event, values)

    def warning(self, event: str, **values: object) -> None:
        self._write(logging.WARNING, event, values)

    def error(self, event: str, **values: object) -> None:
        self._write(logging.ERROR, event, values)


def configure_logging(*, json_output: bool = False) -> EventLogger:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        structlog: Any = importlib.import_module("structlog")
    except ModuleNotFoundError:
        return _StandardEventLogger(json_output=json_output)

    processors: list[object] = [
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))
    structlog.configure(processors=processors)
    return structlog.get_logger("classcatalog.scraper")
