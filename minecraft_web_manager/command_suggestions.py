"""Command suggestion indexes for the web console.

MCDR commands are delegated to MCDR itself.  This module only handles the
fallback index built from the Minecraft/mod ``help`` output.  The format is a
compact usage grammar rather than a full Brigadier tree, but it is enough to
preserve literals, alternatives, required/optional arguments and aliases.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HELP_LINE = re.compile(r"^\s*/(?P<body>\S.*)$")
_ALIAS = re.compile(r"^\s*(?P<alias>\S+)\s+->\s+(?P<target>\S+)(?:\s+.*)?$")


@dataclass(frozen=True)
class _Token:
    text: str
    argument: bool = False


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    start = 0
    round_depth = 0
    square_depth = 0
    angle_depth = 0
    for index, char in enumerate(text):
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth = max(0, round_depth - 1)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif char == separator and round_depth == 0 and square_depth == 0 and angle_depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _usage_atoms(text: str) -> list[str]:
    """Split a usage expression on whitespace outside syntax groups.

    Angle brackets describe one argument node, and the label inside them may
    contain spaces (for example ``<log name>``).  Treating them as a group is
    important: otherwise that expression becomes the two bogus literals
    ``<log`` and ``name>``.
    """
    atoms: list[str] = []
    start: int | None = None
    round_depth = 0
    square_depth = 0
    angle_depth = 0
    for index, char in enumerate(text):
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth = max(0, round_depth - 1)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        if char.isspace() and round_depth == 0 and square_depth == 0 and angle_depth == 0:
            if start is not None:
                atoms.append(text[start:index])
                start = None
        elif start is None:
            start = index
    if start is not None:
        atoms.append(text[start:])
    return atoms


def _is_wrapped_group(atom: str, opening: str, closing: str) -> bool:
    if not (atom.startswith(opening) and atom.endswith(closing)):
        return False
    depth = 0
    for index, char in enumerate(atom):
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0 and index != len(atom) - 1:
                return False
    return depth == 0


def _expand_atom(atom: str) -> list[list[_Token]]:
    atom = atom.strip().replace("\\<", "<").replace("\\>", ">")
    if not atom:
        return [[]]
    if _is_wrapped_group(atom, "(", ")"):
        inner = atom[1:-1]
        result: list[list[_Token]] = []
        for option in _split_top_level(inner, "|"):
            result.extend(_expand_sequence(option))
        return result
    if _is_wrapped_group(atom, "[", "]"):
        inner = atom[1:-1]
        result = [[]]
        for option in _split_top_level(inner, "|"):
            result.extend(_expand_sequence(option))
        return result
    return [[_Token(atom, atom.startswith("<") and atom.endswith(">"))]]


def _expand_sequence(text: str) -> list[list[_Token]]:
    sequences: list[list[_Token]] = [[]]
    for atom in _usage_atoms(text):
        alternatives = _expand_atom(atom)
        sequences = [prefix + suffix for prefix in sequences for suffix in alternatives]
    return sequences


def _clean_line(content: str) -> str:
    return _ANSI_ESCAPE.sub("", str(content)).replace("\\<", "<").replace("\\>", ">")


def is_help_line(content: str) -> bool:
    return bool(_HELP_LINE.match(_clean_line(content).strip()))


def parse_help_lines(lines: list[str]) -> list[tuple[_Token, ...]]:
    patterns: list[tuple[_Token, ...]] = []
    aliases: list[tuple[str, str]] = []
    for raw_line in lines:
        match = _HELP_LINE.match(_clean_line(raw_line).strip())
        if not match:
            continue
        body = match.group("body")
        alias_match = _ALIAS.match(body)
        if alias_match:
            aliases.append((alias_match.group("alias"), alias_match.group("target")))
            continue
        patterns.extend(tuple(sequence) for sequence in _expand_sequence(body))

    # ``/xp -> experience`` and similar aliases have no independent usage
    # grammar. Reuse the canonical command's suffix so aliases behave like the
    # command shown by the server, rather than only being suggested at root.
    for alias, target in aliases:
        for pattern in tuple(patterns):
            if pattern and not pattern[0].argument and pattern[0].text == target:
                patterns.append((_Token(alias),) + pattern[1:])

    unique: list[tuple[_Token, ...]] = []
    seen: set[tuple[tuple[str, bool], ...]] = set()
    for pattern in patterns:
        key = tuple((token.text, token.argument) for token in pattern)
        if key not in seen:
            seen.add(key)
            unique.append(pattern)
    return unique


class HelpSuggestionIndex:
    """Thread-safe in-memory suggestion index populated from ``help`` output."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._patterns: tuple[tuple[_Token, ...], ...] = ()
        self._line_count = 0

    def replace(self, lines: list[str]) -> None:
        patterns = parse_help_lines(lines)
        with self._lock:
            self._patterns = tuple(patterns)
            self._line_count = len(lines)

    @property
    def available(self) -> bool:
        with self._lock:
            return bool(self._patterns)

    def suggest(self, command: str, limit: int = 80) -> list[dict[str, str]]:
        text = command[1:] if command.startswith("/") else command
        if not text.strip():
            return []
        trailing_space = text[-1].isspace()
        parts = text.split()
        prefix = "" if trailing_space else (parts[-1] if parts else "")
        consumed = parts if trailing_space else parts[:-1]
        with self._lock:
            patterns = self._patterns

        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for pattern in patterns:
            if len(consumed) >= len(pattern):
                continue
            if not self._matches(consumed, pattern):
                continue
            candidate = pattern[len(consumed)]
            # ``<...>`` is a placeholder for a value supplied by the user,
            # not a command word. Never put it in the menu (and never try to
            # match a partial value such as ``<log`` as a literal).
            if candidate.argument:
                continue
            if prefix and not candidate.text.lower().startswith(prefix.lower()):
                continue
            display = candidate.text
            if trailing_space:
                insert = command + display
            else:
                insert = command[: len(command) - len(prefix)] + display
            key = (display, insert)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "display": display,
                "insert": insert,
                "kind": "literal",
            })
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _matches(consumed: list[str], pattern: tuple[_Token, ...]) -> bool:
        for typed, expected in zip(consumed, pattern):
            if not expected.argument and typed.lower() != expected.text.lower():
                return False
        return True
