"""The only place where the HTTP thread touches MCDR and system state."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

import psutil
from mcdreforged.command.command_source import PluginCommandSource
from mcdreforged.constants import core_constant
from mcdreforged.minecraft.rtext.text import RTextBase

from . import nbt, properties, roster

# Minecraft usernames are 1-16 of [A-Za-z0-9_]; anything else could smuggle a second
# command (or arguments) into the console/RCON line, so targets are validated not escaped.
_PLAYER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# Carpet fake players always get the Java offline-mode UUID derived from their name,
# no matter how the server itself is configured.
_OFFLINE_UUID_PREFIX = "OfflinePlayer:"

# Values never sent to the browser; submitting an empty string leaves them unchanged.
_SENSITIVE_PROPERTY_KEYS = {
    "rcon.password",
    "management-server-secret",
    "management-server-tls-keystore-password",
}

# action -> (target kind, command template)
_PLAYER_ACTIONS: dict[str, tuple[str, str]] = {
    "op": ("name", "op {target}"),
    "deop": ("name", "deop {target}"),
    "kick": ("name", "kick {target}{reason}"),
    "ban": ("name", "ban {target}{reason}"),
    "pardon": ("name", "pardon {target}"),
    "ban_ip": ("name_or_ip", "ban-ip {target}{reason}"),
    "pardon_ip": ("ip", "pardon-ip {target}"),
    "whitelist_add": ("name", "whitelist add {target}"),
    "whitelist_remove": ("name", "whitelist remove {target}"),
    "whitelist_on": ("none", "whitelist on"),
    "whitelist_off": ("none", "whitelist off"),
    "whitelist_reload": ("none", "whitelist reload"),
}

_PID_REFRESH_SECONDS = 20.0
_TICK_TTL_SECONDS = 5.0

_TPS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:tps|ticks per second)", re.IGNORECASE)
_MSPT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_POS_PATTERN = re.compile(r"\[\s*(-?[\d.eE]+)d?\s*,\s*(-?[\d.eE]+)d?\s*,\s*(-?[\d.eE]+)d?\s*\]")
_DIMENSION_PATTERN = re.compile(r'"([^"]+)"')


class WebCommandSource(PluginCommandSource):
    """A plugin command source whose replies are forwarded to the web console instead of only the MCDR log.

    MCDR's default plugin command source prints command replies (e.g. the output of ``!!MCDR status``)
    straight to the MCDR logger, which never reaches ``on_info`` and therefore never reaches the web UI.
    """

    def __init__(self, server_interface, on_line: Callable[[dict[str, Any]], None]):
        super().__init__(server_interface)
        self._on_line = on_line

    def reply(self, message: Any, **kwargs: Any) -> None:
        super().reply(message, **kwargs)
        timestamp = time.strftime("%H:%M:%S")
        thread_name = threading.current_thread().name
        text = RTextBase.from_any(message).to_plain_text()
        for line in text.splitlines() or [""]:
            self._on_line(
                {
                    "content": line,
                    "raw": f"[MCDR] [{timestamp}] [{thread_name}/INFO]: {line}",
                    "source": "mcdr",
                    "timestamp": timestamp,
                }
            )


class MCDRBridge:
    def __init__(self, server, players: dict[str, dict[str, Any]], config=None):
        self.server = server
        self.players = players
        self.config = config
        self.started_at = time.time()
        self._pids: list[int] = []
        self._pids_at = 0.0
        self._processes: dict[int, psutil.Process] = {}
        self._tick_cache: tuple[float, dict[str, Any]] | None = None
        self._tick_lock = threading.Lock()
        self._world_cache: tuple[tuple[tuple[str, int], ...], dict[str, Any]] | None = None
        self._world_lock = threading.Lock()

    def call(self, operation: Callable[[], Any], timeout: float = 5.0) -> Any:
        """Run an operation on MCDR's TaskExecutor and wait from the web thread."""
        future = self.server.schedule_task(operation, block=False)
        return future.result(timeout=timeout)

    def status(self) -> dict[str, Any]:
        def get_status() -> dict[str, Any]:
            information = self.server.get_server_information()
            return {
                "running": self.server.is_server_running(),
                "startup": self.server.is_server_startup(),
                "rcon_running": self.server.is_rcon_running(),
                "pid": self.server.get_server_pid(),
                "players": sorted(self.players),
                "player_count": len(self.players),
                "minecraft_version": self._string_or_none(getattr(information, "version", None)),
                "server_name": self._string_or_none(getattr(information, "server_name", None)),
                "mcdr_version": core_constant.VERSION,
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
            }

        return self.call(get_status)

    def _server_pids(self) -> list[int]:
        """Minecraft PIDs, refreshed only occasionally so per-second sampling stays cheap.

        Reading them requires a round-trip to MCDR's TaskExecutor, which is far too
        expensive to do once a second; on failure the previous list is kept.
        """
        now = time.monotonic()
        if now - self._pids_at >= _PID_REFRESH_SECONDS:
            self._pids_at = now
            try:
                self._pids = list(self.call(lambda: self.server.get_server_pid_all(), timeout=3.0))
            except Exception:
                pass
            for pid in [pid for pid in self._processes if pid not in self._pids]:
                self._processes.pop(pid, None)
        return self._pids

    def sample(self) -> dict[str, Any]:
        """One lightweight resource sample, safe to call every second.

        ``psutil.Process`` objects are cached because ``cpu_percent(interval=None)``
        is a delta against that object's previous call — recreating them each time
        would always report 0%.
        """
        process_cpu = 0.0
        process_memory = 0
        for pid in self._server_pids():
            process = self._processes.get(pid)
            if process is None:
                try:
                    process = psutil.Process(pid)
                    process.cpu_percent(interval=None)  # prime the delta baseline
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                self._processes[pid] = process
            try:
                process_cpu += process.cpu_percent(interval=None)
                process_memory += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._processes.pop(pid, None)
        virtual_memory = psutil.virtual_memory()
        network = psutil.net_io_counters()
        return {
            "cpu": psutil.cpu_percent(interval=None),
            "mem_used": virtual_memory.used,
            "mem_total": virtual_memory.total,
            "mc_mem": process_memory,
            "mc_cpu": process_cpu,
            "net_sent": network.bytes_sent,
            "net_recv": network.bytes_recv,
        }

    def metrics(self) -> dict[str, Any]:
        sample = self.sample()
        swap_memory = psutil.swap_memory()
        disk = psutil.disk_usage(os.getcwd())
        return {
            "system": {
                "cpu_percent": sample["cpu"],
                "memory_used": sample["mem_used"],
                "memory_total": sample["mem_total"],
                "swap_used": swap_memory.used,
                "swap_total": swap_memory.total,
                "disk_used": disk.used,
                "disk_total": disk.total,
                "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
                "network_bytes_sent": sample["net_sent"],
                "network_bytes_recv": sample["net_recv"],
            },
            "minecraft": {"pids": list(self._pids), "cpu_percent": sample["mc_cpu"], "memory_used": sample["mc_mem"]},
            "tick": self._tick_report(),
            "collected_at": int(time.time()),
        }

    def _tick_report(self) -> dict[str, Any]:
        """Best-effort TPS/MSPT reading via RCON (e.g. Carpet's or vanilla's ``/tick query``).

        Falls back to the raw response text when the wording can't be confidently parsed,
        instead of guessing at numbers that might be wrong. The result is cached for a
        few seconds so every open browser tab doesn't trigger its own RCON round trip.
        """
        now = time.monotonic()
        with self._tick_lock:
            if self._tick_cache is not None and now - self._tick_cache[0] < _TICK_TTL_SECONDS:
                return self._tick_cache[1]

        def query() -> str | None:
            if not self.server.is_rcon_running():
                return None
            return self.server.rcon_query("tick query")

        raw = self.call(query, timeout=5.0)
        if raw is None:
            report: dict[str, Any] = {"available": False, "raw": None, "tps": None, "mspt": None}
        else:
            tps_match = _TPS_PATTERN.search(raw)
            mspt_match = _MSPT_PATTERN.search(raw)
            report = {
                "available": True,
                "raw": raw,
                "tps": float(tps_match.group(1)) if tps_match else None,
                "mspt": float(mspt_match.group(1)) if mspt_match else None,
            }
        with self._tick_lock:
            self._tick_cache = (now, report)
        return report

    def _paths(self) -> tuple[Path, Path]:
        """(server working directory, world save directory), resolved on MCDR's thread."""

        def resolve() -> tuple[Path, Path]:
            mcdr_config = self.server.get_mcdr_config()
            working_directory = Path(mcdr_config.get("working_directory", "server"))
            level_name = "world"
            properties_path = working_directory / "server.properties"
            if properties_path.exists():
                for line in properties_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.strip().startswith("level-name="):
                        level_name = line.split("=", 1)[1].strip() or level_name
                        break
            return working_directory, working_directory / level_name

        return self.call(resolve)

    def _world_signature(self, world_directory: Path) -> tuple[tuple[str, int], ...]:
        """(path, mtime_ns) pairs for every file world() reads.

        The save files only change when the server writes them, so comparing the
        signature is enough to serve the static world info from cache between saves —
        this avoids re-parsing level.dat on every 10s overview poll.
        """
        paths = [
            world_directory / "level.dat",
            world_directory / "data" / "minecraft" / "world_gen_settings.dat",
        ]
        signature = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                signature.append((str(path), 0))
            else:
                signature.append((str(path), stat.st_mtime_ns))
        return tuple(signature)

    def world(self) -> dict[str, Any]:
        _, world_directory = self._paths()
        signature = self._world_signature(world_directory)
        with self._world_lock:
            if self._world_cache is not None and self._world_cache[0] == signature:
                return self._world_cache[1]
        result: dict[str, Any] = {
            "level_name": None,
            "minecraft_version": None,
            "difficulty": None,
            "seed": None,
            "from_save": True,
        }
        try:
            data = nbt.load(world_directory / "level.dat")["Data"]
            result["level_name"] = self._string_or_none(data.get("LevelName"))
            difficulty_settings = data.get("difficulty_settings")
            if isinstance(difficulty_settings, dict):
                result["difficulty"] = self._string_or_none(difficulty_settings.get("difficulty"))
            version = data.get("Version")
            if isinstance(version, dict):
                result["minecraft_version"] = self._string_or_none(version.get("Name"))
        except (OSError, ValueError, KeyError):
            pass
        try:
            gen_settings = nbt.load(world_directory / "data" / "minecraft" / "world_gen_settings.dat")["data"]
            seed = gen_settings.get("seed")
            result["seed"] = str(seed) if seed is not None else None
        except (OSError, ValueError, KeyError):
            pass
        # Everything here comes from the save files, which only change when the server
        # saves, so these values lag reality by minutes — static enough for a cache
        # keyed on file mtimes.
        with self._world_lock:
            self._world_cache = (signature, result)
        return result

    def players_detail(self) -> list[dict[str, Any]]:
        players = self.players

        def query_all() -> tuple[dict[str, tuple[str | None, str | None]], dict[str, dict[str, Any]]]:
            # Snapshot _players on MCDR's thread: join/leave events mutate it there,
            # and iterating the live dict from the web thread could race with a
            # concurrent mutation (RuntimeError mid-iteration).
            snapshot = dict(players)
            if not self.server.is_rcon_running():
                return {name: (None, None) for name in snapshot}, snapshot
            queried: dict[str, tuple[str | None, str | None]] = {}
            for name in snapshot:
                pos = self.server.rcon_query(f"data get entity {name} Pos")
                dimension = self.server.rcon_query(f"data get entity {name} Dimension")
                queried[name] = (pos, dimension)
            return queried, snapshot

        raw_results: dict[str, tuple[str | None, str | None]] = {}
        snapshot: dict[str, dict[str, Any]] = {}
        if players:
            # RCON round trips are serialised on MCDR's thread; give larger rosters a
            # budget that scales with size instead of a fixed 10s wall.
            timeout = max(10.0, min(60.0, len(players) * 2.0))
            try:
                raw_results, snapshot = self.call(query_all, timeout=timeout)
            except TimeoutError:
                # Degrade gracefully: still list everyone, just without positions.
                try:
                    snapshot = dict(players)
                except RuntimeError:
                    snapshot = {}  # player list changed mid-copy; next poll will retry
        now = time.time()
        detail = []
        for name, state in sorted(snapshot.items()):
            pos_text, dimension_text = raw_results.get(name, (None, None))
            position = None
            if pos_text:
                match = _POS_PATTERN.search(pos_text)
                if match:
                    position = [round(float(match.group(index)), 1) for index in (1, 2, 3)]
            dimension = None
            if dimension_text:
                match = _DIMENSION_PATTERN.search(dimension_text)
                if match:
                    dimension = match.group(1)
            joined_at = state.get("joined_at")
            detail.append(
                {
                    "name": name,
                    "ip": state.get("ip"),
                    "uuid": state.get("uuid"),
                    "joined_at": joined_at,
                    "online_seconds": max(0, int(now - joined_at)) if joined_at else None,
                    "dimension": dimension,
                    "position": position,
                }
            )
        return detail

    def execute(
        self, command: str, transport: str, on_console_line: Callable[[dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        command = command.strip()
        if not command:
            raise ValueError("Command cannot be empty")

        def perform() -> str | None:
            if transport == "rcon":
                if not self.server.is_rcon_running():
                    raise RuntimeError("MCDR RCON is not available")
                return self.server.rcon_query(command)
            if transport != "console":
                raise ValueError("Unsupported command transport")
            if command.startswith("!!"):
                source = WebCommandSource(self.server, on_console_line or (lambda _line: None))
                self.server.execute_command(command, source=source)
            else:
                self.server.execute(command)
            return None

        result = self.call(perform, timeout=10.0)
        return {"accepted": True, "transport": transport, "result": result}

    def server_action(self, action: str) -> bool:
        actions = {"start": self.server.start, "stop": self.server.stop, "restart": self.server.restart}
        if action not in actions:
            raise ValueError("Unsupported server action")
        return bool(self.call(actions[action], timeout=5.0))

    def plugins(self) -> list[dict[str, Any]]:
        def get_plugins() -> list[dict[str, Any]]:
            metadata = self.server.get_all_metadata()
            self_plugin_id = self.server.get_self_metadata().id
            return [
                {
                    "id": plugin_id,
                    "name": self._string_or_none(getattr(item, "name", plugin_id)),
                    "version": self._string_or_none(getattr(item, "version", None)),
                    "description": self._string_or_none(getattr(item, "description", None)),
                    "self": plugin_id == self_plugin_id,
                }
                for plugin_id, item in sorted(metadata.items())
            ]

        return self.call(get_plugins)

    def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        if not _PLUGIN_ID_PATTERN.match(plugin_id or ""):
            raise ValueError(f"Invalid plugin id: {plugin_id!r}")

        def do_reload() -> bool:
            # Reloading this very plugin would stop the web server mid-request and
            # leave the caller with a hanging request / reset connection. That flow
            # only works from the MCDR console.
            if plugin_id == self.server.get_self_metadata().id:
                raise ValueError(
                    "Cannot reload this plugin from the web panel; "
                    f"use !!MCDR reload plugin {plugin_id} in the MCDR console instead"
                )
            return bool(self.server.reload_plugin(plugin_id))

        result = self.call(do_reload, timeout=20.0)
        return {"accepted": bool(result), "plugin_id": plugin_id}

    def mods(self) -> list[dict[str, Any]]:
        """Fabric mods found in the server's mods/ folder, named from their fabric.mod.json."""
        server_directory, _ = self._paths()
        found: list[dict[str, Any]] = []
        try:
            jars = sorted((server_directory / "mods").glob("*.jar"))
        except OSError:
            return found
        for jar in jars:
            entry: dict[str, Any] = {"file": jar.name, "id": None, "name": None, "version": None, "description": None}
            try:
                with zipfile.ZipFile(jar) as archive:
                    metadata = json.loads(archive.read("fabric.mod.json").decode("utf-8", errors="replace"))
                entry.update(
                    {
                        "id": self._string_or_none(metadata.get("id")),
                        "name": self._string_or_none(metadata.get("name")),
                        "version": self._string_or_none(metadata.get("version")),
                        "description": self._string_or_none(metadata.get("description")),
                    }
                )
            except (OSError, KeyError, ValueError, zipfile.BadZipFile):
                pass  # not a Fabric mod (or unreadable) — still list the file
            found.append(entry)
        return found

    def server_properties(self) -> dict[str, Any]:
        server_directory, _ = self._paths()
        path = server_directory / "server.properties"
        entries = properties.read(path)
        for entry in entries:
            if entry["key"] in _SENSITIVE_PROPERTY_KEYS:
                # Never ship secrets to the browser; an empty submission means "keep".
                entry["sensitive"] = True
                entry["has_value"] = bool(entry["value"])
                entry["value"] = ""
        return {"path": str(path), "entries": entries}

    def update_server_properties(self, changes: dict[str, Any]) -> dict[str, Any]:
        changes = {
            key: value
            for key, value in (changes or {}).items()
            if not (key in _SENSITIVE_PROPERTY_KEYS and value == "")
        }
        if not changes:
            raise ValueError("No changes supplied")
        server_directory, _ = self._paths()
        return properties.update(server_directory / "server.properties", changes)

    @staticmethod
    def _offline_uuid(name: str) -> uuid.UUID:
        """The UUID Java's UUID.nameUUIDFromBytes("OfflinePlayer:" + name) produces.

        Carpet fake players use exactly this UUID regardless of the server's
        online-mode, so matching it is the core bot signal.
        """
        digest = hashlib.md5((_OFFLINE_UUID_PREFIX + name).encode("utf-8")).digest()
        return uuid.UUID(bytes=digest, version=3)

    @staticmethod
    def _normalize_uuid(value: Any) -> str:
        # Registry files may store UUIDs with or without dashes; compare on the bare hex.
        return str(value).replace("-", "").lower()

    def _classify_bot(
        self,
        player: dict[str, Any],
        online_mode: bool,
        manual_bots: set[str],
        not_bots: set[str],
        bot_patterns: list[re.Pattern],
        patterns_apply_to_all: bool,
    ) -> tuple[bool, str | None]:
        """Return (is_bot, source) where source is "manual" / "pattern" / "auto".

        Priority: the not_bot_names override wins over everything, then the manual
        bot_names list, then the configured name patterns, then the offline-UUID
        check. Pattern rules only apply to players with no usercache record, because
        real accounts that ever joined are always in usercache — that is what keeps a
        genuine player named "bot_XXX" from being misclassified. Several popular
        Carpet extensions (TIS, AMS, RMS, ...) give fake players Mojang-resolved or
        random v4 UUIDs instead of the classic offline UUID, so UUID matching alone
        misses them. On offline-mode servers the offline-UUID check additionally
        requires never being seen in usercache and having no captured IP login line.
        """
        name = (player.get("name") or "").lower()
        if name in not_bots:
            return False, None
        if name in manual_bots:
            return True, "manual"
        player_name = player.get("name") or ""
        if any(pattern.search(player_name) for pattern in bot_patterns) and (
            patterns_apply_to_all or not player.get("in_usercache")
        ):
            return True, "pattern"
        player_uuid = player.get("uuid")
        if not name or not player_uuid:
            return False, None
        try:
            uuid_matches = self._normalize_uuid(player_uuid) == self._normalize_uuid(
                self._offline_uuid(player["name"])
            )
        except (ValueError, TypeError):
            uuid_matches = False
        if not uuid_matches:
            return False, None
        if online_mode:
            return True, "auto"
        if player.get("in_usercache") or player.get("ip"):
            return False, None
        return True, "auto"

    def set_bot_flag(self, name: str, is_bot: bool) -> dict[str, Any]:
        """Force a player into or out of the bot list, persisted to the config.

        Marking adds the name to bot_names; unmarking adds it to not_bot_names so
        name rules or UUID heuristics can never re-flag the same account.
        """
        if not _PLAYER_NAME_PATTERN.match(name or ""):
            raise ValueError(f"Invalid player name: {name!r}")
        if self.config is None:
            raise RuntimeError("Bot flagging requires the plugin config")
        key = name.lower()
        with self.config.lock:
            bot_names = [str(entry) for entry in (self.config.data.get("bot_names") or [])]
            not_bot_names = [str(entry) for entry in (self.config.data.get("not_bot_names") or [])]
            if is_bot:
                if key not in bot_names:
                    bot_names.append(key)
                not_bot_names = [entry for entry in not_bot_names if entry != key]
            else:
                if key not in not_bot_names:
                    not_bot_names.append(key)
                bot_names = [entry for entry in bot_names if entry != key]
            self.config.data["bot_names"] = bot_names
            self.config.data["not_bot_names"] = not_bot_names
            self.config.save()
        return {"name": name, "is_bot": is_bot}

    def roster(self) -> dict[str, Any]:
        """Every player the server has ever recorded, merged with live online state."""
        server_directory, world_directory = self._paths()
        data = roster.build(server_directory, world_directory)
        # Vanilla's /whitelist on|off persists to server.properties, so that is the
        # source of truth for whether the whitelist is currently enforced.
        settings = {entry["key"]: entry["value"] for entry in properties.read(server_directory / "server.properties")}
        data["whitelist_enabled"] = settings.get("white-list") == "true"
        data["whitelist_enforced"] = settings.get("enforce-whitelist") == "true"
        online_mode = settings.get("online-mode") == "true"
        manual_bots = {str(entry).lower() for entry in (self.config.data.get("bot_names") or [])} if self.config else set()
        not_bots = {str(entry).lower() for entry in (self.config.data.get("not_bot_names") or [])} if self.config else set()
        bot_patterns: list[re.Pattern] = []
        if self.config:
            for pattern in (self.config.data.get("bot_name_patterns") or []):
                try:
                    bot_patterns.append(re.compile(str(pattern)))
                except re.error:
                    pass  # a malformed regex in the config is ignored, not fatal
        patterns_apply_to_all = bool(self.config.data.get("bot_name_patterns_apply_to_all", False)) if self.config else False
        online = {entry["name"].lower(): entry for entry in self.players_detail() if entry.get("name")}

        for player in data["players"]:
            name = (player.get("name") or "").lower()
            live = online.pop(name, None)
            player["online"] = live is not None
            player.update(
                {
                    "ip": (live or {}).get("ip"),
                    "joined_at": (live or {}).get("joined_at"),
                    "online_seconds": (live or {}).get("online_seconds"),
                    "dimension": (live or {}).get("dimension"),
                    "position": (live or {}).get("position"),
                }
            )
            player["is_bot"], player["bot_source"] = self._classify_bot(
                player, online_mode, manual_bots, not_bots, bot_patterns, patterns_apply_to_all
            )
        # Anyone online but absent from every registry file (e.g. usercache not flushed yet)
        # still belongs in the roster rather than silently disappearing.
        for live in online.values():
            player = {
                "uuid": live.get("uuid"),
                "name": live.get("name"),
                "op": False,
                "op_level": None,
                "whitelisted": False,
                "banned": False,
                "ban_reason": None,
                "has_played": True,
                "last_seen": None,
                "in_usercache": False,
                "online": True,
                "ip": live.get("ip"),
                "joined_at": live.get("joined_at"),
                "online_seconds": live.get("online_seconds"),
                "dimension": live.get("dimension"),
                "position": live.get("position"),
            }
            player["is_bot"], player["bot_source"] = self._classify_bot(
                player, online_mode, manual_bots, not_bots, bot_patterns, patterns_apply_to_all
            )
            data["players"].append(player)
        return data

    @staticmethod
    def _clean_reason(value: Any) -> str:
        # Newlines would let a reason smuggle a second command into the console/RCON line.
        return re.sub(r"\s+", " ", str(value or "")).strip()[:120]

    @staticmethod
    def _validate_target(action: str, kind: str, target: str | None) -> str:
        if kind == "none":
            return ""
        if not target:
            raise ValueError(f"Action {action} requires a target")
        target = target.strip()
        if kind == "ip":
            ipaddress.ip_address(target)  # raises ValueError on anything else
            return target
        if _PLAYER_NAME_PATTERN.match(target):
            return target
        if kind == "name_or_ip":
            ipaddress.ip_address(target)
            return target
        raise ValueError(f"Invalid player name: {target!r}")

    def player_action(self, action: str, target: str | None = None, reason: Any = None) -> dict[str, Any]:
        spec = _PLAYER_ACTIONS.get(action)
        if spec is None:
            raise ValueError("Unsupported player action")
        kind, template = spec
        safe_target = self._validate_target(action, kind, target)
        reason_text = self._clean_reason(reason)
        command = template.format(target=safe_target, reason=f" {reason_text}" if reason_text else "").strip()

        def perform() -> str | None:
            # RCON is the only transport that returns the server's reply text.
            if self.server.is_rcon_running():
                return self.server.rcon_query(command)
            self.server.execute(command)
            return None

        result = self.call(perform, timeout=10.0)
        return {"accepted": True, "action": action, "command": command, "result": result}

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return None if value is None else str(value)
