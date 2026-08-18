"""The only place where the HTTP thread touches MCDR and system state."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from io import StringIO
from pathlib import Path
from typing import Any, Callable

import psutil
from mcdreforged.command.command_source import PluginCommandSource
from mcdreforged.constants import core_constant
from mcdreforged.minecraft.rtext.text import RTextBase
from ruamel.yaml import YAML

from . import nbt, pending, properties, roster

# Minecraft usernames are 1-16 of [A-Za-z0-9_]; anything else could smuggle a second
# command (or arguments) into the console/RCON line, so targets are validated not escaped.
_PLAYER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# Mod / config file safety limits. Mods can be hundreds of MB, but config files
# are plain text and should stay small enough to edit in a browser textarea.
_MAX_MOD_CONFIG_BYTES = 1 * 1024 * 1024
_MAX_MOD_CONFIG_CHARS = 1_000_000
_PLUGIN_INTERNAL_CONFIG_FILES = {"pending_properties.json"}
# Give big plugin downloads time to finish; the frontend reports an honest timeout
# when the PIM operation is still running after this many seconds.
_CHECK_UPDATE_TIMEOUT = 120.0
_PIM_POLL_INTERVAL = 0.25

# Best-effort detection of MCDR plugin-installer outcome from its plain-text output.
# MCDR itself is bilingual (en/zh), so both languages are matched. ``success`` /
# ``noop`` markers only count when no failure marker is present; if the operation
# finished without any known marker the panel reports "not confirmed" instead of
# guessing success.
_PIM_FAILURE_MARKER = re.compile(
    r"there's ongoing operations|发现正在进行的操作|"
    r"dependency resolution failed|依赖解析失败|"
    r"fetch failed|元数据更新失败|"
    r"installation error|插件安装失败|"
    r"cannot be reinstalled|无法被重新安装|"
    r"cannot install builtin|无法安装内置插件|"
    r"cannot check update for builtin|不可为内置插件|"
    r"invalid plugin id|无效的插件id|"
    r"not found|不存在|"
    r"package installation failed|包依赖安装失败|"
    r"abort|安装终止",
    re.IGNORECASE,
)
_PIM_INSTALL_SUCCESS_MARKER = re.compile(r"installation done|插件安装完成", re.IGNORECASE)
_PIM_INSTALL_NOOP_MARKER = re.compile(r"nothing needs to be installed|无需安装", re.IGNORECASE)
_PIM_CHECK_SUCCESS_MARKER = re.compile(
    r"found \d|are up-to-date|no updates found|找到了|均为最新版本", re.IGNORECASE
)

# Carpet fake players always get the Java offline-mode UUID derived from their name,
# no matter how the server itself is configured.
_OFFLINE_UUID_PREFIX = "OfflinePlayer:"

# Values never sent to the browser; submitting an empty string leaves them unchanged.
_SENSITIVE_PROPERTY_KEYS = {
    "rcon.password",
    "management-server-secret",
    "management-server-tls-keystore-password",
}

# Field metadata for MCDR's config.yml. It intentionally covers several MCDR
# versions: only keys actually present in the running MCDR's config are sent to the
# browser, so older/newer keys degrade to a plain text input instead of disappearing.
_MCDR_CONFIG_META: dict[str, dict[str, Any]] = {
    "language": {"category": "basic", "type": "str", "options": ["en_us", "zh_cn", "zh_tw"]},
    "working_directory": {"category": "server", "type": "str", "input": True},
    "start_command": {"category": "server", "input": True},
    "handler": {
        "category": "server",
        "type": "str",
        "options": [
            "vanilla_handler",
            "beta18_handler",
            "bukkit_handler",
            "bukkit14_handler",
            "forge_handler",
            "cat_server_handler",
            "arclight_handler",
            "bungeecord_handler",
            "waterfall_handler",
            "velocity_handler",
        ],
    },
    "encoding": {"category": "server", "type": "str", "nullable": True, "input": True},
    "decoding": {"category": "server", "nullable": True, "input": True},
    "rcon": {"category": "server"},
    "rcon.enable": {"category": "server", "type": "bool"},
    "rcon.address": {"category": "server", "type": "str", "nullable": True, "input": True},
    "rcon.port": {"category": "server", "type": "int", "nullable": True},
    "rcon.password": {"category": "server", "type": "str", "nullable": True, "sensitive": True},
    "plugin_directories": {"category": "plugin", "type": "list", "input": True},
    "catalogue_meta_cache_ttl": {"category": "plugin", "type": "int"},
    "catalogue_meta_fetch_timeout": {"category": "plugin", "type": "float"},
    "catalogue_meta_url": {"category": "plugin", "type": "str", "nullable": True},
    "plugin_download_url": {"category": "plugin", "type": "str", "nullable": True},
    "plugin_download_timeout": {"category": "plugin", "type": "float"},
    "plugin_pip_install_extra_args": {"category": "plugin", "type": "str", "nullable": True},
    "check_update": {"category": "misc", "type": "bool"},
    "advanced_console": {"category": "misc", "type": "bool"},
    "http_proxy": {"category": "misc", "type": "str", "nullable": True},
    "https_proxy": {"category": "misc", "type": "str", "nullable": True},
    "telemetry": {"category": "misc", "type": "bool"},
    "disable_console_thread": {"category": "advanced", "type": "bool"},
    "disable_console_color": {"category": "advanced", "type": "bool"},
    "custom_handlers": {"category": "advanced", "type": "list", "nullable": True},
    "custom_info_reactors": {"category": "advanced", "type": "list", "nullable": True},
    "watchdog_threshold": {"category": "advanced", "type": "int"},
    "handler_detection": {"category": "advanced", "type": "bool"},
    "debug": {"category": "debug"},
    "write_server_output_to_log_file": {"category": "debug", "type": "bool"},
}
_MCDR_CONFIG_CATEGORY_ORDER = ["basic", "server", "plugin", "misc", "advanced", "debug", "other"]
_MISSING = object()

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
        self._mcdr_config_cache: tuple[tuple[tuple[str, int], str], dict[str, Any]] | None = None
        self._mcdr_config_lock = threading.Lock()
        self.pending: pending.PendingProperties | None = None
        self._instant_keys: set[str] = set()
        self._instant_lock = threading.Lock()

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

    def _resolve_paths(self) -> tuple[Path, Path]:
        """(server working directory, world save directory).

        Only call this on MCDR's TaskExecutor thread (it touches MCDR config API).
        """
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

    def _paths(self) -> tuple[Path, Path]:
        """(server working directory, world save directory), resolved on MCDR's thread."""
        return self.call(self._resolve_paths)

    def _current_properties(self) -> dict[str, str]:
        """Current server.properties as {key: value}; executor-thread only."""
        server_directory, _ = self._resolve_paths()
        return {entry["key"]: entry["value"] for entry in properties.read(server_directory / "server.properties")}

    def bootstrap_pending(self, logger=None) -> None:
        """Load (or create) the persisted effective-value baseline. Call from on_load."""
        if self.config is None:
            return
        self.pending = pending.PendingProperties(
            self.config.path.with_name("pending_properties.json"), self.config, logger
        )
        try:
            current = self._current_properties()
        except Exception:
            current = {}
        self.pending.load_or_bootstrap(current)

    def mark_server_started(self) -> None:
        """The server (re)started: current file values are now in effect."""
        if self.pending is None:
            return
        try:
            current = self._current_properties()
        except Exception:
            return
        self.pending.reset(current)

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
        if re.match(r"(?i)^whitelist\s+(on|off)\b", command):
            # whitelist on/off rewrites server.properties and takes effect immediately,
            # even when typed by hand in the console — keep the baseline in sync.
            with self._instant_lock:
                self._instant_keys.add("white-list")
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
            entries: list[dict[str, Any]] = []
            for plugin_id, item in sorted(metadata.items()):
                plugin_type = self.server.get_plugin_type(plugin_id)
                type_name = getattr(plugin_type, "name", None)
                file_path = self.server.get_plugin_file_path(plugin_id)
                file_name = Path(file_path).name if file_path else None
                entries.append(
                    {
                        "id": plugin_id,
                        "name": self._string_or_none(getattr(item, "name", plugin_id)),
                        "version": self._string_or_none(getattr(item, "version", None)),
                        "description": self._string_or_none(getattr(item, "description", None)),
                        "self": plugin_id == self_plugin_id,
                        "state": "loaded",
                        "disabled": False,
                        "unloaded": False,
                        "builtin": type_name == "builtin",
                        "type": type_name,
                        "updatable": type_name == "packed",
                        "file": file_name,
                        "file_name": file_name,
                        "file_path": file_path,
                    }
                )

            for file_path in self.server.get_disabled_plugin_list():
                file_name = Path(file_path).name
                entries.append(
                    {
                        "id": None,
                        "name": file_name,
                        "version": None,
                        "description": None,
                        "self": False,
                        "state": "disabled",
                        "disabled": True,
                        "unloaded": False,
                        "builtin": False,
                        "type": None,
                        "updatable": False,
                        "file": file_name,
                        "file_name": file_name,
                        "file_path": file_path,
                    }
                )

            for file_path in self.server.get_unloaded_plugin_list():
                file_name = Path(file_path).name
                entries.append(
                    {
                        "id": None,
                        "name": file_name,
                        "version": None,
                        "description": None,
                        "self": False,
                        "state": "unloaded",
                        "disabled": False,
                        "unloaded": True,
                        "builtin": False,
                        "type": None,
                        "updatable": False,
                        "file": file_name,
                        "file_name": file_name,
                        "file_path": file_path,
                    }
                )

            state_order = {"loaded": 0, "disabled": 1, "unloaded": 2}
            entries.sort(
                key=lambda entry: (
                    state_order.get(entry["state"], 9),
                    str(entry.get("name") or entry.get("id") or "").lower(),
                )
            )
            return entries

        return self.call(get_plugins)

    def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        if not _PLUGIN_ID_PATTERN.match(plugin_id or ""):
            raise ValueError(f"Invalid plugin id: {plugin_id!r}")

        # Reloading this very plugin is safe: MCDR unloads the plugin (the web
        # thread shuts down; uvicorn releases the listening socket first, so
        # on_load can bind the same port again) and loads it back, restarting
        # the panel. The in-flight request rides out the shutdown, so it may
        # take ~10 s (WebService.stop joins the web thread with a 10 s timeout)
        # before the response arrives; the browser's WebSocket reconnects
        # automatically once the panel is back.
        def do_reload() -> bool:
            return bool(self.server.reload_plugin(plugin_id))

        result = self.call(do_reload, timeout=20.0)
        return {"accepted": bool(result), "plugin_id": plugin_id}

    # ---------- plugin online management (via MCDR commands / APIs) ----------

    @staticmethod
    def _validate_plugin_id(plugin_id: str) -> str:
        if not _PLUGIN_ID_PATTERN.match(plugin_id or ""):
            raise ValueError(f"Invalid plugin id: {plugin_id!r}")
        return plugin_id

    @staticmethod
    def _validate_plugin_file_name(file_name: str) -> str:
        if not isinstance(file_name, str) or not file_name:
            raise ValueError("Invalid plugin file name")
        if (
            Path(file_name).drive
            or "/" in file_name
            or "\\" in file_name
            or "\x00" in file_name
            or "\n" in file_name
            or "\r" in file_name
            or file_name in (".", "..")
        ):
            raise ValueError("Invalid plugin file name")
        return file_name

    @staticmethod
    def _quote_mcdr_arg(value: str) -> str:
        """Quote a command argument the way MCDR's QuotableText parser expects."""
        if re.fullmatch(r"[A-Za-z0-9_.\-]+", value):
            return value
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _plugin_command(
        self, command: str, on_console_line: Callable[[dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        return self.execute(command, "console", on_console_line)

    def plugin_check_update(
        self, plugin_id: str | None = None, on_console_line: Callable[[dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        if plugin_id is not None:
            self._validate_plugin_id(plugin_id)
            command = f"!!MCDR plugin checkupdate {plugin_id}"
            checked = [plugin_id]
        else:
            command = "!!MCDR plugin checkupdate"
            checked = [
                entry["id"]
                for entry in self.plugins()
                if entry["state"] == "loaded" and not entry["builtin"] and entry["id"]
            ]

        collected: list[str] = []
        collect_lock = threading.Lock()

        def collect(line: dict[str, Any]) -> None:
            with collect_lock:
                collected.append(str(line.get("content") or ""))
            if on_console_line is not None:
                on_console_line(line)

        result = self._plugin_command(command, collect)
        self._wait_for_pim_operation("check_update")
        time.sleep(0.2)  # drain any final replies queued right after the thread ends
        with collect_lock:
            lines = list(collected)
        text = "\n".join(lines)
        failed = _PIM_FAILURE_MARKER.search(text) is not None
        return {
            **result,
            "lines": lines,
            "checked": checked,
            "updates": self._parse_plugin_updates(lines),
            # A rejected check (another PIM operation running, bad id, dependency
            # resolution failure, ...) must NOT be reported as "all up to date".
            "failed": failed,
            "success": not failed and _PIM_CHECK_SUCCESS_MARKER.search(text) is not None,
        }

    def _wait_for_pim_operation(self, operation_key: str) -> bool:
        """Wait for MCDR's plugin installer operation (e.g. checkupdate) to finish.

        Returns ``True`` if the operation finished within the deadline, ``False``
        if it was still running when the deadline passed (frontend reports timeout).
        """
        deadline = time.time() + _CHECK_UPDATE_TIMEOUT
        while time.time() < deadline:
            if self.call(lambda: self._pim_operation_finished(operation_key)):
                return True
            time.sleep(_PIM_POLL_INTERVAL)
        return False

    def _pim_operation_finished(self, operation_key: str) -> bool:
        mcdr_plugin = self.server.get_plugin_instance(core_constant.PACKAGE_NAME)
        if mcdr_plugin is None:
            return True
        for sub_command in getattr(mcdr_plugin, "main_sub_commands", []) or []:
            pim_ext = getattr(sub_command, "pim_ext", None)
            if pim_ext is None:
                continue
            operation = getattr(pim_ext, "current_operation", None)
            if operation is None:
                return True
            # A checkupdate that was rejected (e.g. another PIM operation is running)
            # never starts a thread of its own, so there is nothing to wait for.
            if operation.thread is None or operation.op_key != operation_key:
                return True
            return False
        return True

    @staticmethod
    def _parse_plugin_updates(lines: list[str]) -> list[dict[str, str]]:
        """Best-effort parse of ``!!MCDR plugin checkupdate`` plain-text output.

        Updatable entries are printed like ``  plugin_id 1.0.0 -> 1.1.0``; only those
        lines (not the "not updatable" / "up to date" sections) contain an arrow.
        """
        updates: list[dict[str, str]] = []
        pattern = re.compile(r"([a-z][a-z0-9_]{0,63})\s+(\S+)\s*->\s*(\S+)")
        for line in lines:
            match = pattern.search(line)
            if match is not None:
                updates.append(
                    {
                        "plugin_id": match.group(1),
                        "current": match.group(2),
                        "latest": match.group(3),
                    }
                )
        return updates

    def plugin_update(
        self, plugin_id: str | None = None, on_console_line: Callable[[dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        if plugin_id is not None:
            self._validate_plugin_id(plugin_id)
            if plugin_id == self.call(lambda: self.server.get_self_metadata().id):
                # MCDR replaces the file AND reloads the plugin right after the
                # install, so updating this very plugin would stop the web server
                # mid-request and leave the caller with a dead connection.
                raise ValueError(
                    "Cannot update this plugin from the web panel; "
                    f"use !!MCDR plugin install -U -y {plugin_id} in the MCDR console instead"
                )
            command = f"!!MCDR plugin install -U -y {plugin_id}"
            skipped = False
        else:
            # Update all = enumerate updatable plugins explicitly instead of `*`,
            # because `*` also targets this very plugin and would kill the panel.
            ids = [
                entry["id"]
                for entry in self.plugins()
                if entry["state"] == "loaded" and entry["updatable"] and entry["id"] and not entry["self"]
            ]
            if not ids:
                return {
                    "accepted": True,
                    "completed": True,
                    "success": True,
                    "failed": False,
                    "noop": False,
                    "skipped": True,
                    "lines": [],
                }
            command = "!!MCDR plugin install -U -y " + " ".join(ids)
            skipped = False

        collected: list[str] = []
        collect_lock = threading.Lock()

        def collect(line: dict[str, Any]) -> None:
            with collect_lock:
                collected.append(str(line.get("content") or ""))
            if on_console_line is not None:
                on_console_line(line)

        result = self._plugin_command(command, collect)
        finished = self._wait_for_pim_operation("install")
        time.sleep(0.2)
        with collect_lock:
            lines = list(collected)
        text = "\n".join(lines)
        failed = _PIM_FAILURE_MARKER.search(text) is not None
        success = not failed and _PIM_INSTALL_SUCCESS_MARKER.search(text) is not None
        noop = not failed and not success and _PIM_INSTALL_NOOP_MARKER.search(text) is not None
        return {
            **result,
            "completed": finished,
            "success": success,
            "failed": failed,
            "noop": noop,
            "skipped": skipped,
            "lines": lines,
        }

    def plugin_disable(
        self, plugin_id: str, on_console_line: Callable[[dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        self._validate_plugin_id(plugin_id)

        def check_disable() -> None:
            if plugin_id == self.server.get_self_metadata().id:
                raise ValueError(
                    "Cannot disable this plugin from the web panel; "
                    f"use !!MCDR plugin disable {plugin_id} in the MCDR console instead"
                )
            plugin_type = self.server.get_plugin_type(plugin_id)
            if plugin_type is None:
                raise ValueError(f"Plugin not found or not loaded: {plugin_id}")
            if getattr(plugin_type, "name", "") == "builtin":
                raise ValueError(f"Cannot disable builtin plugin: {plugin_id}")

        self.call(check_disable)
        return self._plugin_command(f"!!MCDR plugin disable {plugin_id}", on_console_line)

    def plugin_enable(
        self, file_name: str, on_console_line: Callable[[dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        self._validate_plugin_file_name(file_name)
        command = f"!!MCDR plugin enable {self._quote_mcdr_arg(file_name)}"
        return self._plugin_command(command, on_console_line)

    def plugin_load(self, file_name: str) -> dict[str, Any]:
        """Load an unloaded plugin file (e.g. one that failed to load at startup).

        ``reload_plugin`` only works for loaded plugins, so this uses MCDR's
        ``load_plugin`` API on the unloaded file path instead.
        """
        self._validate_plugin_file_name(file_name)

        def do_load() -> bool:
            candidates = [
                Path(path)
                for path in self.server.get_unloaded_plugin_list()
                if Path(path).name == file_name
            ]
            if len(candidates) != 1:
                raise ValueError(f"Plugin file not found or ambiguous: {file_name}")
            return bool(self.server.load_plugin(str(candidates[0])))

        accepted = bool(self.call(do_load, timeout=30.0))
        return {"accepted": accepted, "file": file_name}

    @staticmethod
    def _remove_plugin_path(path: Path) -> None:
        if path.is_symlink() or not path.exists():
            raise ValueError(f"Plugin file not found: {path}")
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as error:
            raise ValueError(f"Cannot delete plugin file {path}: {error}") from error

    def plugin_delete(
        self,
        plugin_id: str | None = None,
        file_name: str | None = None,
        on_console_line: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if plugin_id and file_name:
            raise ValueError("Specify either plugin_id or file_name, not both")

        if plugin_id:
            self._validate_plugin_id(plugin_id)

            def unload_and_get_path() -> Path:
                if plugin_id == self.server.get_self_metadata().id:
                    raise ValueError(
                        "Cannot delete this plugin from the web panel; "
                        f"use the MCDR console to manage {plugin_id}"
                    )
                path_text = self.server.get_plugin_file_path(plugin_id)
                if path_text is None:
                    raise ValueError(f"Plugin not found or not loaded: {plugin_id}")
                result = self.server.unload_plugin(plugin_id)
                if result is not True:
                    raise ValueError(f"Failed to unload plugin: {plugin_id}")
                return Path(path_text)

            path = self.call(unload_and_get_path, timeout=30.0)
            self._remove_plugin_path(path)
            return {"plugin_id": plugin_id, "file": path.name, "deleted": True}

        if file_name:
            self._validate_plugin_file_name(file_name)

            def resolve_path() -> Path:
                candidates = [
                    Path(path)
                    for path in self.server.get_disabled_plugin_list() + self.server.get_unloaded_plugin_list()
                    if Path(path).name == file_name
                ]
                if len(candidates) != 1:
                    raise ValueError(f"Plugin file not found or ambiguous: {file_name}")
                return candidates[0]

            path = self.call(resolve_path)
            self._remove_plugin_path(path)
            return {"file": path.name, "deleted": True}

        raise ValueError("plugin_id or file_name is required")

    def mods(self) -> list[dict[str, Any]]:
        """Mods found in the server's mods/ folder.

        Fabric mods are named from their fabric.mod.json; other loaders (Forge /
        NeoForge) fall back to the file name. Disabled mods are the same jars with a
        ``.jar.disabled`` suffix, so they are listed too and can be re-enabled.
        """
        server_directory, _ = self._paths()
        found: list[dict[str, Any]] = []
        try:
            mods_directory = server_directory / "mods"
            jars = sorted(
                list(mods_directory.glob("*.jar")) + list(mods_directory.glob("*.jar.disabled")),
                key=lambda path: path.name,
            )
        except OSError:
            return found
        for jar in jars:
            try:
                stat = jar.stat()
            except OSError:
                stat = None
            entry: dict[str, Any] = {
                "file": jar.name,
                "id": None,
                "name": None,
                "version": None,
                "description": None,
                "disabled": jar.name.endswith(".jar.disabled"),
                "size": stat.st_size if stat is not None else None,
                "mtime": stat.st_mtime if stat is not None else None,
            }
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

    @staticmethod
    def _mod_file_name(filename: str, *, allow_disabled: bool = True, require_active: bool = False, require_disabled: bool = False) -> str:
        """Validate a mod file name and return it untouched.

        Only the file name is accepted (no separators), and only ``.jar`` /
        ``.jar.disabled`` names are allowed, so a crafted upload or delete target
        can never escape the mods directory.
        """
        if not isinstance(filename, str) or not filename:
            raise ValueError("Invalid mod file name")
        if (
            Path(filename).drive
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
            or filename in (".", "..")
        ):
            raise ValueError("Invalid mod file name")
        disabled = filename.endswith(".jar.disabled")
        active = filename.endswith(".jar") and not disabled
        if not (active or (disabled and allow_disabled)):
            raise ValueError("Mod file must end with .jar or .jar.disabled")
        if require_active and not active:
            raise ValueError("Only an enabled .jar mod can be disabled")
        if require_disabled and not disabled:
            raise ValueError("Only a .jar.disabled mod can be enabled")
        return filename

    def _mods_directory(self) -> Path:
        server_directory, _ = self._paths()
        return server_directory / "mods"

    def upload_mod(self, filename: str, source, overwrite: bool = False) -> dict[str, Any]:
        """Stream an uploaded jar into mods/ (through a temp file, atomically)."""
        name = self._mod_file_name(filename, allow_disabled=False)
        mods_directory = self._mods_directory()
        mods_directory.mkdir(parents=True, exist_ok=True)
        target = mods_directory / name
        if target.exists():
            if target.is_dir() or not overwrite:
                raise ValueError(f"Mod file already exists: {name}")
        temporary = mods_directory / f".{name}.{uuid.uuid4().hex}.upload"
        try:
            try:
                source.seek(0)
            except (AttributeError, OSError):
                pass
            with temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if not zipfile.is_zipfile(temporary):
                raise ValueError("Uploaded file is not a valid .jar archive")
            if target.exists() and not overwrite:
                raise ValueError(f"Mod file already exists: {name}")
            os.replace(temporary, target)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        return {"file": name, "size": target.stat().st_size, "disabled": False}

    def set_mod_enabled(self, filename: str, enabled: bool) -> dict[str, Any]:
        """Disable a mod by renaming ``foo.jar`` -> ``foo.jar.disabled`` (or back)."""
        if enabled:
            name = self._mod_file_name(filename, require_disabled=True)
            target_name = name[: -len(".disabled")]
        else:
            name = self._mod_file_name(filename, require_active=True)
            target_name = name + ".disabled"
        mods_directory = self._mods_directory()
        source = mods_directory / name
        target = mods_directory / target_name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Mod file not found: {name}")
        if target.exists():
            raise ValueError(f"Target file already exists: {target_name}")
        try:
            os.replace(source, target)
        except OSError as error:
            raise ValueError(f"Cannot modify mod file {name}: {error}") from error
        return {"file": target_name, "enabled": enabled, "disabled": not enabled}

    def delete_mod(self, filename: str) -> dict[str, Any]:
        """Permanently remove a mod file from mods/."""
        name = self._mod_file_name(filename)
        mods_directory = self._mods_directory()
        path = mods_directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Mod file not found: {name}")
        try:
            path.unlink()
        except OSError as error:
            raise ValueError(f"Cannot delete mod file {name}: {error}") from error
        return {"file": name, "deleted": True}

    # ---------- mod config files ----------

    def _config_root(self) -> Path:
        server_directory, _ = self._paths()
        return server_directory / "config"

    @staticmethod
    def _resolve_text_path(root: Path, relative: str) -> Path:
        """Resolve a config file path strictly inside ``root``."""
        if not isinstance(relative, str) or not relative:
            raise ValueError("Config path cannot be empty")
        if (
            Path(relative).drive
            or Path(relative).is_absolute()
            or "\\" in relative
            or "\x00" in relative
        ):
            raise ValueError("Invalid config path")
        if ".DS_Store" in Path(relative).parts:
            raise ValueError(".DS_Store files are not editable")
        root = root.resolve()
        raw = root / relative
        if raw.is_symlink():
            raise ValueError("Symlinked config files are not editable")
        candidate = raw.resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError("Config file not found")
        return candidate

    def _resolve_config_path(self, relative: str) -> Path:
        return self._resolve_text_path(self._config_root(), relative)

    def _resolve_plugin_config_path(self, plugin_id: str, relative: str) -> Path:
        self._validate_plugin_id(plugin_id)
        if plugin_id in (".", "..") or "/" in plugin_id or "\\" in plugin_id:
            raise ValueError("Invalid plugin id")
        if any(part in _PLUGIN_INTERNAL_CONFIG_FILES for part in Path(relative).parts):
            raise ValueError("Internal plugin state files are not editable")
        return self._resolve_text_path(Path("config") / plugin_id, relative)

    def mod_configs(self) -> dict[str, Any]:
        """Every file under the server's config/ directory (relative paths)."""
        root = self._config_root()
        files: list[dict[str, Any]] = []
        if root.is_dir():
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = sorted(
                    directory
                    for directory in dirnames
                    if not (Path(dirpath) / directory).is_symlink()
                )
                for filename in sorted(filenames):
                    path = Path(dirpath) / filename
                    if filename == ".DS_Store" or path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    files.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "name": filename,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                        }
                    )
        return {"path": str(root), "files": files}

    @staticmethod
    def _read_text_config(path: Path, relative: str) -> dict[str, Any]:
        """Read a config file as text; binary / oversized files are reported read-only."""
        try:
            stat = path.stat()
            raw = path.read_bytes()
        except OSError as error:
            raise ValueError(f"Cannot read config file: {error}") from error
        base = {
            "path": relative,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
        if stat.st_size > _MAX_MOD_CONFIG_BYTES:
            return {**base, "editable": False, "reason": "too_large", "content": None}
        bom = raw.startswith(b"\xef\xbb\xbf")
        body = raw[3:] if bom else raw
        if b"\x00" in body:
            return {**base, "editable": False, "reason": "binary", "content": None}
        encoding = "utf-8"
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            # ISO-8859-1 can decode every byte; it preserves legacy .properties files
            # and is a safe fallback for small non-UTF-8 configs.
            encoding = "latin-1"
            text = body.decode("latin-1")
        crlf = body.count(b"\r\n")
        line_ending = "crlf" if crlf and crlf * 2 >= body.count(b"\n") else "lf"
        return {
            **base,
            "editable": True,
            "reason": None,
            "content": text,
            "encoding": encoding,
            "bom": bom,
            "line_ending": line_ending,
        }

    @staticmethod
    def _write_text_config(path: Path, relative: str, content: str) -> dict[str, Any]:
        """Write a config file atomically, preserving encoding, BOM and line endings."""
        if not isinstance(content, str):
            raise ValueError("Config content must be text")
        if len(content) > _MAX_MOD_CONFIG_CHARS:
            raise ValueError("Config file is too large to save")
        try:
            raw = path.read_bytes()
            original_mode = path.stat().st_mode & 0o7777
        except OSError as error:
            raise ValueError(f"Cannot read config file: {error}") from error
        bom = raw.startswith(b"\xef\xbb\xbf")
        body = raw[3:] if bom else raw
        encoding = "utf-8"
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            encoding = "latin-1"
        crlf = body.count(b"\r\n")
        line_ending = "crlf" if crlf and crlf * 2 >= body.count(b"\n") else "lf"
        try:
            encoded = content.encode(encoding)
        except UnicodeEncodeError as error:
            raise ValueError(f"Content cannot be encoded as {encoding}: {error}") from error
        if line_ending == "crlf":
            encoded = encoded.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        if bom:
            encoded = b"\xef\xbb\xbf" + encoded
        if len(encoded) > _MAX_MOD_CONFIG_BYTES:
            raise ValueError("Config file is too large to save")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(encoded)
            os.chmod(temporary, original_mode)
            os.replace(temporary, path)
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise ValueError(f"Cannot write config file: {error}") from error
        try:
            stat = path.stat()
        except OSError:
            stat = None
        return {
            "path": relative,
            "size": stat.st_size if stat is not None else len(encoded),
            "mtime": stat.st_mtime if stat is not None else None,
        }

    def read_mod_config(self, relative: str) -> dict[str, Any]:
        return self._read_text_config(self._resolve_config_path(relative), relative)

    def update_mod_config(self, relative: str, content: str) -> dict[str, Any]:
        return self._write_text_config(self._resolve_config_path(relative), relative, content)

    # ---------- plugin config files ----------

    def plugin_configs(self) -> dict[str, Any]:
        """Every file under every loaded plugin's ``config/<plugin_id>/`` folder."""

        def collect() -> list[dict[str, Any]]:
            metadata = self.server.get_all_metadata()
            files: list[dict[str, Any]] = []
            for plugin_id, item in sorted(metadata.items()):
                plugin_type = getattr(self.server.get_plugin_type(plugin_id), "name", None)
                if plugin_type == "builtin":
                    continue
                root = Path("config") / plugin_id
                if not root.is_dir():
                    continue
                for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                    dirnames[:] = sorted(
                        directory
                        for directory in dirnames
                        if not (Path(dirpath) / directory).is_symlink()
                    )
                    for filename in sorted(filenames):
                        path = Path(dirpath) / filename
                        if filename == ".DS_Store" or filename in _PLUGIN_INTERNAL_CONFIG_FILES or path.is_symlink():
                            continue
                        try:
                            stat = path.stat()
                        except OSError:
                            continue
                        files.append(
                            {
                                "plugin_id": plugin_id,
                                "plugin_name": self._string_or_none(getattr(item, "name", plugin_id)),
                                "path": path.relative_to(root).as_posix(),
                                "name": filename,
                                "size": stat.st_size,
                                "mtime": stat.st_mtime,
                            }
                        )
            return files

        return {"files": self.call(collect)}

    def read_plugin_config(self, plugin_id: str, relative: str) -> dict[str, Any]:
        return self._read_text_config(self._resolve_plugin_config_path(plugin_id, relative), relative)

    def update_plugin_config(self, plugin_id: str, relative: str, content: str) -> dict[str, Any]:
        return self._write_text_config(
            self._resolve_plugin_config_path(plugin_id, relative), relative, content
        )

    # ---------- MCDR config ----------

    @staticmethod
    def _mcdr_config_path() -> Path:
        """Resolve MCDR's own config.yml inside MCDR's working directory."""
        return MCDRBridge._resolve_text_path(Path("."), core_constant.CONFIG_FILE_PATH)

    @staticmethod
    def _plain_yaml(value: Any) -> Any:
        """Turn ruamel's comment-preserving containers into plain Python values."""
        if isinstance(value, bool) or type(value).__name__ == "ScalarBoolean":
            return bool(value)
        if isinstance(value, dict):
            return {key: MCDRBridge._plain_yaml(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [MCDRBridge._plain_yaml(item) for item in value]
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return float(value)
        if isinstance(value, str):
            return str(value)
        return value

    @staticmethod
    def _merge_mcdr_defaults(defaults: dict[str, Any], file_data: dict[str, Any]) -> dict[str, Any]:
        """Merge file values over the running MCDR's effective defaults.

        Keys missing from config.yml (MCDR fills them with defaults) still appear in
        the visual editor, and keys that only exist in this MCDR version stay visible.
        """
        if not isinstance(file_data, dict):
            file_data = {}
        result: dict[str, Any] = {}
        for key, default_value in defaults.items():
            file_value = file_data.get(key, _MISSING)
            if isinstance(default_value, dict):
                result[key] = MCDRBridge._merge_mcdr_defaults(
                    default_value, file_value if isinstance(file_value, dict) else {}
                )
            else:
                result[key] = file_value if file_value is not _MISSING else default_value
        for key, file_value in file_data.items():
            if key not in result:
                result[key] = file_value
        return result

    @staticmethod
    def _mcdr_value_type(value: Any, meta: dict[str, Any]) -> str:
        meta_type = meta.get("type")
        if meta_type:
            return str(meta_type)
        if isinstance(value, bool) or type(value).__name__ == "ScalarBoolean":
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        if isinstance(value, list):
            return "list"
        return "none"

    def _flatten_mcdr_entries(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        def walk(obj: dict[str, Any], prefix: str = "", inherited_category: str | None = None) -> None:
            for key, value in obj.items():
                path = f"{prefix}.{key}" if prefix else key
                meta = _MCDR_CONFIG_META.get(path, {})
                category = str(meta.get("category") or inherited_category or "other")
                if isinstance(value, dict):
                    walk(value, path, category)
                    continue
                entry_type = MCDRBridge._mcdr_value_type(value, meta)
                nullable = bool(meta.get("nullable", False)) or (value is None and entry_type == "none")
                sensitive = bool(meta.get("sensitive", False))
                options = [str(option) for option in (meta.get("options") or [])]
                if isinstance(value, str) and value and value not in options:
                    options.append(value)
                entries.append(
                    {
                        "key": path,
                        "category": category,
                        "type": entry_type,
                        "value": "" if sensitive else value,
                        "has_value": value is not None and value != "",
                        "nullable": nullable,
                        "sensitive": sensitive,
                        "input": bool(meta.get("input", False)),
                        "options": options or None,
                    }
                )

        walk(data)
        return entries

    def _mcdr_config_data(self) -> dict[str, Any]:
        """Build the visual MCDR config schema. Call from MCDR's TaskExecutor."""
        path = self._mcdr_config_path()
        stat = path.stat()
        loaded = MCDRBridge._plain_yaml(self.server.get_mcdr_config() or {})
        try:
            with path.open("r", encoding="utf-8") as file:
                file_data = MCDRBridge._plain_yaml(YAML().load(file) or {})
        except Exception:
            file_data = {}
        merged = MCDRBridge._merge_mcdr_defaults(
            loaded if isinstance(loaded, dict) else {},
            file_data if isinstance(file_data, dict) else {},
        )
        entries = self._flatten_mcdr_entries(merged)
        categories = [
            category
            for category in _MCDR_CONFIG_CATEGORY_ORDER
            if any(entry["category"] == category for entry in entries)
        ]
        return {
            "path": core_constant.CONFIG_FILE_PATH,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "editable": True,
            "entries": entries,
            "categories": categories,
        }

    @staticmethod
    def _mcdr_config_signature(path: Path) -> tuple[tuple[tuple[str, int], str], str]:
        try:
            stat = path.stat()
        except OSError:
            return ((str(path), 0), ""), core_constant.VERSION
        return ((str(path), stat.st_mtime_ns), ""), core_constant.VERSION

    def mcdr_config(self) -> dict[str, Any]:
        """Return a version-aware visual schema for MCDR's config.yml."""
        path = self._mcdr_config_path()
        signature = self._mcdr_config_signature(path)
        with self._mcdr_config_lock:
            if self._mcdr_config_cache is not None and self._mcdr_config_cache[0] == signature:
                return self._mcdr_config_cache[1]
        data = self.call(self._mcdr_config_data)
        with self._mcdr_config_lock:
            self._mcdr_config_cache = (signature, data)
        return data

    @staticmethod
    def _coerce_mcdr_value(existing: Any, value: Any) -> Any:
        if isinstance(existing, bool) or type(existing).__name__ == "ScalarBoolean":
            return bool(value)
        if isinstance(existing, int) and not isinstance(existing, bool):
            try:
                return int(value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"Expected an integer, got {value!r}") from error
        if isinstance(existing, float):
            try:
                return float(value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"Expected a number, got {value!r}") from error
        if isinstance(existing, list):
            if value is None:
                return None
            if not isinstance(value, list):
                raise ValueError(f"Expected a list, got {value!r}")
            return value
        return value

    def _apply_mcdr_changes(self, path: Path, changes: dict[str, Any]) -> list[str]:
        try:
            with path.open("r", encoding="utf-8") as file:
                data = YAML().load(file)
        except Exception as error:
            raise ValueError(f"Cannot parse config.yml: {error}") from error
        if not isinstance(data, dict):
            data = {}
        applied: list[str] = []
        for key_path, raw_value in changes.items():
            parts = key_path.split(".")
            if not parts or any(not part for part in parts):
                raise ValueError(f"Invalid MCDR config key: {key_path}")
            if _MCDR_CONFIG_META.get(key_path, {}).get("sensitive") and raw_value == "":
                continue
            node: Any = data
            for part in parts[:-1]:
                if not isinstance(node, dict):
                    raise ValueError(f"Invalid MCDR config key: {key_path}")
                if part not in node:
                    node[part] = {}
                node = node[part]
            if not isinstance(node, dict):
                raise ValueError(f"Invalid MCDR config key: {key_path}")
            if parts[-1] in node:
                new_value = MCDRBridge._coerce_mcdr_value(node[parts[-1]], raw_value)
            else:
                new_value = raw_value
            node[parts[-1]] = new_value
            applied.append(key_path)
        if not applied:
            raise ValueError("No changes supplied")
        yaml = YAML()
        yaml.width = 1048576  # don't wrap long strings, same as MCDR
        buffer = StringIO()
        yaml.dump(data, buffer)
        encoded = buffer.getvalue().encode("utf-8")
        original_mode = path.stat().st_mode & 0o7777
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(encoded)
            os.chmod(temporary, original_mode)
            os.replace(temporary, path)
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise ValueError(f"Cannot write config.yml: {error}") from error
        return applied

    def update_mcdr_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply structured changes to config.yml, then reload MCDR config immediately."""
        if not isinstance(changes, dict) or not changes:
            raise ValueError("No changes supplied")
        path = self._mcdr_config_path()
        applied = self._apply_mcdr_changes(path, changes)

        def reload_config() -> None:
            # Equivalent to the console command `!!MCDR reload config`
            self.server.reload_config_file(log=True)

        try:
            self.call(reload_config, timeout=10.0)
        except Exception as error:
            raise RuntimeError(f"config.yml saved, but !!MCDR reload config failed: {error}") from error
        with self._mcdr_config_lock:
            self._mcdr_config_cache = None
        return {"applied": applied, "reloaded": True}

    def prewarm_mcdr_config(self) -> None:
        """Detect the current MCDR config schema during plugin load.

        Best-effort: a missing/invalid config.yml only degrades the first page load,
        it never prevents the panel from starting.
        """
        try:
            path = self._mcdr_config_path()
            data = self._mcdr_config_data()
            signature = self._mcdr_config_signature(path)
        except Exception:
            return
        with self._mcdr_config_lock:
            self._mcdr_config_cache = (signature, data)

    def _sync_instant_keys(self, current: dict[str, str]) -> None:
        """Fold server commands that apply immediately (e.g. whitelist on/off) into
        the baseline so they are not misreported as pending a restart."""
        with self._instant_lock:
            instant_keys = set(self._instant_keys)
            self._instant_keys.clear()
        if instant_keys and self.pending is not None:
            for key in instant_keys:
                if key in current:
                    self.pending.set_key(key, current[key])

    def server_properties(self) -> dict[str, Any]:
        server_directory, _ = self._paths()
        path = server_directory / "server.properties"
        entries = properties.read(path)
        current = {entry["key"]: entry["value"] for entry in entries}
        self._sync_instant_keys(current)

        diff = self.pending.diff(current) if self.pending is not None else {}
        result_entries: list[dict[str, Any]] = []
        for entry in entries:
            key = entry["key"]
            effective, _file_value = diff.get(key, (None, None))
            entry["pending"] = key in diff
            if key in _SENSITIVE_PROPERTY_KEYS:
                # Never ship secrets to the browser; an empty submission means "keep".
                entry["sensitive"] = True
                entry["has_value"] = bool(entry["value"])
                entry["effective_set"] = bool(effective)
                entry["value"] = ""
            elif key in diff:
                entry["effective"] = effective
            result_entries.append(entry)

        # Keys that were removed from the file while still in the baseline.
        for key, (effective, file_value) in diff.items():
            if file_value is not None:
                continue
            if key in _SENSITIVE_PROPERTY_KEYS:
                result_entries.append(
                    {
                        "key": key,
                        "value": "",
                        "sensitive": True,
                        "has_value": False,
                        "pending": True,
                        "effective_set": bool(effective),
                        "removed": True,
                    }
                )
            else:
                result_entries.append(
                    {"key": key, "value": None, "pending": True, "effective": effective, "removed": True}
                )
        return {"path": str(path), "entries": result_entries}

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

    def roster(self, include_details: bool = True) -> dict[str, Any]:
        """Every player the server has ever recorded, merged with live online state.

        ``include_details=False`` skips the per-player RCON position/dimension
        queries, making it cheap enough to refresh right after a whitelist toggle
        without the roster table stalling.
        """
        server_directory, world_directory = self._paths()
        data = roster.build(server_directory, world_directory)
        # Vanilla's /whitelist on|off persists to server.properties, so that is the
        # source of truth for whether the whitelist is currently enforced. The
        # *effective* value (baseline) is shown when a file change is still pending
        # a restart, keeping this toggle consistent with the actual server state.
        settings = {entry["key"]: entry["value"] for entry in properties.read(server_directory / "server.properties")}
        self._sync_instant_keys(settings)
        effective_whitelist = (
            self.pending.get_effective("white-list", settings.get("white-list")) if self.pending is not None else settings.get("white-list")
        )
        effective_enforce = (
            self.pending.get_effective("enforce-whitelist", settings.get("enforce-whitelist"))
            if self.pending is not None
            else settings.get("enforce-whitelist")
        )
        data["whitelist_enabled"] = effective_whitelist == "true"
        data["whitelist_enforced"] = effective_enforce == "true"
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
        if include_details:
            online = {entry["name"].lower(): entry for entry in self.players_detail() if entry.get("name")}
        else:
            def snapshot_players() -> dict[str, dict[str, Any]]:
                return dict(self.players)

            players_state = self.call(snapshot_players) if self.players else {}
            now = time.time()
            online = {}
            for name, state in sorted(players_state.items()):
                joined_at = state.get("joined_at")
                online[name.lower()] = {
                    "name": name,
                    "ip": state.get("ip"),
                    "uuid": state.get("uuid"),
                    "joined_at": joined_at,
                    "online_seconds": max(0, int(now - joined_at)) if joined_at else None,
                    "dimension": None,
                    "position": None,
                }

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
                result_text = self.server.rcon_query(command)
            else:
                self.server.execute(command)
                result_text = None
            if action in ("whitelist_on", "whitelist_off"):
                # These commands rewrite server.properties and take effect immediately,
                # so sync their baseline instead of flagging them as pending restart.
                with self._instant_lock:
                    self._instant_keys.add("white-list")
            return result_text

        result = self.call(perform, timeout=10.0)
        return {"accepted": True, "action": action, "command": command, "result": result}

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return None if value is None else str(value)
