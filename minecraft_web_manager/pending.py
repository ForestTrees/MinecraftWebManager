"""Persistent tracking of saved-but-not-yet-effective server.properties changes.

A server.properties key is "pending a restart" whenever its current file value
differs from the *baseline* — the values the running server actually uses. The
baseline is persisted so the tracking survives plugin reloads and MCDR restarts.

The state file lives next to config.json and is signed with the plugin's
token_secret; the signature is stored inside config.json. If the state file is
missing, corrupted or tampered with, the baseline is rebuilt from the current
server.properties and a warning is logged, so the panel degrades gracefully
instead of trusting a modified file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path
from typing import Any

SIGNATURE_KEY = "_pending_baseline_signature"


class PendingProperties:
    def __init__(self, state_path: Path, config=None, logger=None):
        self.path = state_path
        self.config = config
        self.logger = logger
        self.lock = threading.RLock()
        self.baseline: dict[str, str] | None = None

    # ---------- lifecycle ----------

    def load_or_bootstrap(self, current: dict[str, str]) -> None:
        """Load the persisted baseline; rebuild it from ``current`` if unusable."""
        with self.lock:
            baseline = self._read_validated()
            if baseline is None:
                baseline = {str(key): str(value) for key, value in current.items()}
                self._write(baseline)
                if self.logger is not None:
                    self.logger.warning(
                        "Minecraft Web Manager: pending-properties baseline was missing or invalid; "
                        "rebuilt from the current server.properties (pending-change history lost)"
                    )
            self.baseline = baseline

    def reset(self, current: dict[str, str]) -> None:
        """The server (re)started: the current file values are now in effect."""
        with self.lock:
            self.baseline = {str(key): str(value) for key, value in current.items()}
            self._write(self.baseline)

    def diff(self, current: dict[str, str]) -> dict[str, tuple[str | None, str | None]]:
        """Return {key: (effective_value, file_value)} for every differing key."""
        with self.lock:
            baseline = self.baseline or {}
        result: dict[str, tuple[str | None, str | None]] = {}
        for key, file_value in current.items():
            effective = baseline.get(key)
            if effective != file_value:
                result[key] = (effective, file_value)
        for key, effective in baseline.items():
            if key not in current:
                result[key] = (effective, None)
        return result

    def set_key(self, key: str, value: str | None) -> None:
        """Sync one key's baseline (for changes that apply immediately)."""
        with self.lock:
            if self.baseline is None:
                return
            if value is None:
                self.baseline.pop(key, None)
            else:
                self.baseline[key] = value
            self._write(self.baseline)

    # ---------- persistence ----------

    def _signature(self, payload: bytes) -> str:
        secret = str(self.config.data.get("token_secret") or "") if self.config is not None else ""
        return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def _read_validated(self) -> dict[str, str] | None:
        try:
            payload = self.path.read_bytes()
        except OSError:
            return None
        try:
            data = json.loads(payload.decode("utf-8"))
            stored = data.get("baseline")
            if not isinstance(stored, dict):
                return None
            normalized = {str(key): str(value) for key, value in stored.items()}
            expected = self._signature(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8"))
            actual = str(self.config.data.get(SIGNATURE_KEY) or "") if self.config is not None else ""
            if not hmac.compare_digest(expected, actual):
                return None
            return normalized
        except (ValueError, TypeError, UnicodeDecodeError):
            return None

    def _write(self, baseline: dict[str, str]) -> None:
        try:
            normalized = {str(key): str(value) for key, value in baseline.items()}
            payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8")
            signature = self._signature(payload)
            data = json.dumps({"baseline": normalized}, ensure_ascii=False, indent=2)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(data, encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            if self.config is not None:
                with self.config.lock:
                    self.config.data[SIGNATURE_KEY] = signature
                    self.config.save()
        except OSError:
            if self.logger is not None:
                self.logger.exception("Minecraft Web Manager: failed to persist pending-properties baseline")
