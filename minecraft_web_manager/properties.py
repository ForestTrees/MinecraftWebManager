"""Read and selectively rewrite the vanilla server.properties file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _unescape(value: str) -> str:
    """Undo the Java-properties escaping the server writes.

    Vanilla writes non-ASCII characters as ``\\uXXXX`` (e.g. the MOTD section sign
    becomes ``\\u00A7``) and escapes tabs, newlines and the ``\\:`` / ``\\=``
    separators. Naively stripping backslashes corrupts those values, so every escape
    is decoded back to the character it stands for.
    """
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            out.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        if escaped == "u" and index + 5 < len(value):
            hex_digits = value[index + 2 : index + 6]
            try:
                out.append(chr(int(hex_digits, 16)))
            except ValueError:
                out.append(char)  # malformed escape; keep it as-is
            else:
                index += 6
                continue
        simple = {
            "t": "\t",
            "n": "\n",
            "r": "\r",
            "f": "\f",
            "\\": "\\",
            ":": ":",
            "=": "=",
            " ": " ",
            "#": "#",
            "!": "!",
        }
        out.append(simple.get(escaped, escaped))
        index += 2
    return "".join(out)


def _escape(value: str) -> str:
    """Escape a value the same way Java ``Properties.store`` does.

    Non-ASCII characters become ``\\uXXXX`` so the file stays readable by Java's
    ISO-8859-1 based ``Properties.load`` regardless of the filesystem charset.
    """
    out = []
    for position, char in enumerate(value):
        if char == "\\":
            out.append("\\\\")
        elif char == "\t":
            out.append("\\t")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\f":
            out.append("\\f")
        elif char in (":", "=", "#", "!"):
            out.append("\\" + char)
        elif position == 0 and char == " ":
            out.append("\\ ")
        elif ord(char) < 0x20 or ord(char) > 0x7E:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return "".join(out)


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
