"""Read and selectively rewrite the vanilla server.properties file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _unescape(value: str) -> str:
    """Undo the Java-properties escaping the server writes (e.g. minecraft\\:normal)."""
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            out.append(value[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _escape(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")
    return escaped


def read(path: Path) -> list[dict[str, str]]:
    """Ordered key/value entries; comments and blank lines are skipped."""
    entries: list[dict[str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        entries.append({"key": key.strip(), "value": _unescape(value.strip())})
    return entries


def update(path: Path, changes: dict[str, Any]) -> dict[str, list[str]]:
    """Rewrite only the given keys in place, preserving order, comments and other keys.

    Keys that do not already exist are reported as ignored rather than appended, so a
    typo can't quietly inject a bogus setting. Returns {"applied": [...], "ignored": [...]}.
    """
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ValueError(f"Cannot read {path}: {error}") from error

    normalised: dict[str, str] = {}
    for key, value in changes.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value)
        # A newline would terminate the line and inject a second setting.
        if "\n" in text or "\r" in text:
            raise ValueError(f"Value for {key} must be a single line")
        normalised[str(key)] = text

    applied: list[str] = []
    lines = original.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        key, separator, _ = stripped.partition("=")
        if not separator:
            continue
        key = key.strip()
        if key in normalised:
            lines[index] = f"{key}={_escape(normalised[key])}"
            applied.append(key)

    ignored = [key for key in normalised if key not in applied]
    if applied:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    return {"applied": applied, "ignored": ignored}
