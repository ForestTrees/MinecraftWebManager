"""Forward MCDR logger output to the web console.

MCDR server output is delivered to plugins through ``on_info``, but plugin
loggers (including logs emitted from worker threads) write directly through
``MCDReforgedLogger`` and never create an ``Info`` object. This module bridges
that second output path without replacing MCDR's normal console handlers.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from mcdreforged.logging.logger import MCDReforgedLogger


_capture_state = threading.local()
_original_handle = None
_publisher: Callable[[dict[str, Any]], None] | None = None


def _is_suppressed() -> bool:
    return getattr(_capture_state, "suppressed", 0) > 0


@contextlib.contextmanager
def suppress_current_thread() -> Iterator[None]:
    """Temporarily suppress logger capture for a command reply.

    ``WebCommandSource`` still forwards these replies to its operation
    collector. Suppressing the logger copy prevents the same line from being
    published twice now that all MCDR logger output is captured globally.
    """

    _capture_state.suppressed = getattr(_capture_state, "suppressed", 0) + 1
    try:
        yield
    finally:
        _capture_state.suppressed -= 1


def _format_record(logger: MCDReforgedLogger, record: Any) -> str:
    try:
        handler = logger.console_handler
        formatted = handler.format(record)
    except Exception:
        timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        formatted = f"[MCDR] [{timestamp}] [{record.threadName}/{record.levelname}]: {record.getMessage()}"
    # Keep MCDR's ANSI SGR sequences. The native console uses them for the
    # level label and colored message text; the web console converts them to
    # safe HTML spans on the browser side.
    return str(formatted)


def _capture(logger: MCDReforgedLogger, record: Any) -> None:
    publisher = _publisher
    if publisher is None or _is_suppressed():
        return
    try:
        raw = _format_record(logger, record)
        # Keep both Minecraft ``§`` codes and ANSI codes in the plain payload.
        # ``raw`` is normally the fully formatted logger line, while ``content``
        # is useful to callers that do not want the logger prefix.
        content = str(record.getMessage())
        publisher(
            {
                "content": content,
                "raw": raw,
                "source": "mcdr",
                "timestamp": time.strftime("%H:%M:%S", time.localtime(record.created)),
            }
        )
    except Exception:
        # Never let a web-socket shutdown or formatter mismatch break MCDR's
        # own logging path.
        pass


def install(publish: Callable[[dict[str, Any]], None]) -> None:
    """Start forwarding records from every current and future MCDR logger."""

    global _original_handle, _publisher
    _publisher = publish
    if _original_handle is not None:
        return

    original_handle = MCDReforgedLogger.handle
    _original_handle = original_handle

    def capturing_handle(logger: MCDReforgedLogger, record: Any):
        result = original_handle(logger, record)
        _capture(logger, record)
        return result

    MCDReforgedLogger.handle = capturing_handle  # type: ignore[method-assign]


def uninstall() -> None:
    """Stop forwarding records and restore MCDR's original logger method."""

    global _original_handle, _publisher
    _publisher = None
    if _original_handle is not None:
        MCDReforgedLogger.handle = _original_handle  # type: ignore[method-assign]
        _original_handle = None
