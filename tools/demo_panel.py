"""Run Minecraft Web Manager against an in-memory demo MCDR bridge.

This is intentionally a development-only harness.  It uses the real FastAPI
application and bundled frontend, but replaces Minecraft/MCDR calls with safe,
in-memory data so the panel can be inspected without starting a game server.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

# Keep the documented command runnable from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from minecraft_web_manager.config import ConfigStore
from minecraft_web_manager.web import WebService


ConsoleCallback = Callable[[dict[str, Any]], None]


class DemoHistory:
    """Small deterministic history provider for the performance charts."""

    def query(self, range_key: str, points: int) -> dict[str, Any]:
        now = int(time.time())
        count = max(10, min(points, 300))
        timestamps = [now - (count - index - 1) * 10 for index in range(count)]
        phase = now / 30
        return {
            "t": timestamps,
            "cpu": [round(18 + 7 * ((index % 11) / 10), 1) for index in range(count)],
            "mem_used": [5_100_000_000 + index * 1_000_000 for index in range(count)],
            "mc_mem": [1_050_000_000 + index * 400_000 for index in range(count)],
            "net_rx": [round(18_000 + 3_000 * ((index + int(phase)) % 8) / 7) for index in range(count)],
            "net_tx": [round(9_000 + 2_000 * ((index + 3) % 8) / 7) for index in range(count)],
            "first_sample_at": timestamps[0],
            "resolution_seconds": 10,
        }


class DemoBridge:
    """A bridge-shaped fake that keeps all demo state in memory."""

    def __init__(
        self,
        *,
        updated: bool = False,
        always_update: bool = False,
        on_reload: Callable[[], None] | None = None,
    ) -> None:
        self.started_at = time.time() - 3_726
        self.running = True
        self.whitelist_enabled = True
        self.always_update = always_update
        self.self_update_available = always_update or not updated
        self.on_reload = on_reload
        self.properties = {
            "motd": "Demo server · Web Manager preview",
            "difficulty": "normal",
            "gamemode": "survival",
            "max-players": "20",
            "online-mode": "true",
            "white-list": "true",
            "view-distance": "12",
        }
        self.mcdr_values: dict[str, Any] = {
            "language": "zh_cn",
            "working_directory": "server",
            "start_command": "java -Xmx2G -jar server.jar nogui",
            "encoding": "utf8",
            "decoding": "utf8",
            "rcon.enable": True,
            "rcon.address": "127.0.0.1",
            "rcon.port": 25575,
            "plugin_directories": ["plugins"],
            "check_update": True,
            "advanced_console": False,
        }
        self.players = [
            {
                "uuid": "2f4c2c1a-3b1a-4f8b-9b63-111111111111",
                "name": "Steve",
                "op": True,
                "op_level": 4,
                "whitelisted": True,
                "banned": False,
                "ban_reason": None,
                "has_played": True,
                "last_seen": None,
                "in_usercache": True,
                "online": True,
                "ip": "127.0.0.1",
                "joined_at": time.time() - 1_245,
                "online_seconds": 1_245,
                "dimension": "minecraft:overworld",
                "position": [128.5, 72.0, -42.0],
                "is_bot": False,
                "bot_source": None,
            },
            {
                "uuid": "3a8ea3b5-2e2c-4bb6-a09d-222222222222",
                "name": "Bot_Alex",
                "op": False,
                "op_level": None,
                "whitelisted": False,
                "banned": False,
                "ban_reason": None,
                "has_played": True,
                "last_seen": time.time() - 3_600,
                "in_usercache": False,
                "online": False,
                "ip": None,
                "joined_at": None,
                "online_seconds": None,
                "dimension": None,
                "position": None,
                "is_bot": True,
                "bot_source": "pattern",
            },
        ]
        self.plugin_files = {
            "demo_plugin/config.json": '{\n  "welcome": "Hello from the demo bridge",\n  "enabled": true\n}\n',
        }
        self.mod_files = {"demo-mod.toml": "[demo]\nenabled = true\n"}

    @staticmethod
    def _emit(callback: ConsoleCallback, content: str) -> None:
        if callback:
            callback({"content": content, "raw": f"[Demo MCDR] {content}", "source": "mcdr", "timestamp": ""})

    def status(self) -> dict[str, Any]:
        online = [player["name"] for player in self.players if player["online"]]
        return {
            "running": self.running,
            "startup": True,
            "rcon_running": True,
            "pid": 4242,
            "players": online,
            "player_count": len(online),
            "minecraft_version": "1.21.8-demo",
            "server_name": "Demo Minecraft Server",
            "mcdr_version": "2.15.7-demo",
            "uptime_seconds": int(time.time() - self.started_at),
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "system": {
                "cpu_percent": 23.5,
                "memory_used": 5_400_000_000,
                "memory_total": 16_000_000_000,
                "swap_used": 120_000_000,
                "swap_total": 4_000_000_000,
                "disk_used": 80_000_000_000,
                "disk_total": 500_000_000_000,
                "load_average": [0.42, 0.38, 0.31],
                "network_bytes_sent": 12_000_000,
                "network_bytes_recv": 20_000_000,
            },
            "minecraft": {"pids": [4242], "cpu_percent": 12.2, "memory_used": 1_200_000_000},
            "tick": {"available": True, "raw": "TPS: 20.0, MSPT: 12.4 ms", "tps": 20.0, "mspt": 12.4},
            "collected_at": int(time.time()),
        }

    def world(self) -> dict[str, Any]:
        return {
            "level_name": "demo-world",
            "minecraft_version": "1.21.8-demo",
            "difficulty": "normal",
            "seed": "-6248842859130421337",
            "from_save": True,
        }

    def players_detail(self) -> list[dict[str, Any]]:
        return [dict(player) for player in self.players]

    def roster(self, include_details: bool = True) -> dict[str, Any]:
        return {
            "players": self.players_detail(),
            "whitelist_enabled": self.whitelist_enabled,
            "whitelist_enforced": True,
            "whitelist": [{"name": "Steve", "uuid": self.players[0]["uuid"]}],
            "ops": [{"name": "Steve", "level": 4}],
            "banned_players": [],
            "banned_ips": [],
        }

    def _properties_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "key": key,
                "value": value,
                "effective": value,
                "pending": False,
                "sensitive": False,
                "has_value": True,
                "removed": False,
            }
            for key, value in self.properties.items()
        ]

    def server_properties(self) -> dict[str, Any]:
        return {"path": "server/server.properties", "entries": self._properties_entries()}

    def update_server_properties(self, changes: dict[str, str]) -> dict[str, Any]:
        applied = []
        ignored = []
        for key, value in changes.items():
            if key in self.properties:
                self.properties[key] = str(value)
                applied.append(key)
            else:
                ignored.append(key)
        return {"applied": applied, "ignored": ignored}

    def _mcdr_entries(self) -> list[dict[str, Any]]:
        meta = {
            "language": ("basic", "str", False),
            "working_directory": ("server", "str", False),
            "start_command": ("server", "str", False),
            "encoding": ("server", "str", True),
            "decoding": ("server", "str", True),
            "rcon.enable": ("server", "bool", False),
            "rcon.address": ("server", "str", True),
            "rcon.port": ("server", "int", True),
            "plugin_directories": ("plugin", "list", False),
            "check_update": ("misc", "bool", False),
            "advanced_console": ("misc", "bool", False),
        }
        return [
            {
                "key": key,
                "value": self.mcdr_values[key],
                "category": category,
                "type": value_type,
                "nullable": nullable,
                "sensitive": False,
            }
            for key, (category, value_type, nullable) in meta.items()
        ]

    def mcdr_config(self) -> dict[str, Any]:
        return {
            "path": "config.yml",
            "entries": self._mcdr_entries(),
            "categories": ["basic", "server", "plugin", "misc"],
            "editable": True,
            "size": 4096,
        }

    def update_mcdr_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        self.mcdr_values.update(changes)
        return {"saved": True, "reloaded": True}

    def plugins(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "minecraft_web_manager",
                "name": "Minecraft Web Manager",
                "version": "1.2.1",
                "description": "Web dashboard for MCDR",
                "description_zh": "MCDR 网页管理面板",
                "description_en": "Web dashboard for MCDR",
                "self": True,
                "state": "loaded",
                "disabled": False,
                "unloaded": False,
                "builtin": False,
                "type": "packed",
                "updatable": True,
                "file": "minecraft_web_manager.mcdr",
                "file_name": "minecraft_web_manager.mcdr",
            },
            {
                "id": "demo_plugin",
                "name": "Demo Plugin",
                "version": "2.4.0",
                "description": "A sample plugin exposed by the test bridge",
                "description_zh": "测试桥接提供的示例插件",
                "description_en": "A sample plugin exposed by the test bridge",
                "self": False,
                "state": "loaded",
                "disabled": False,
                "unloaded": False,
                "builtin": False,
                "type": "packed",
                "updatable": True,
                "file": "demo_plugin.mcdr",
                "file_name": "demo_plugin.mcdr",
            },
        ]

    def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        return {"accepted": True, "plugin_id": plugin_id}

    def plugin_check_update(self, plugin_id: str | None = None, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        checked = [plugin_id] if plugin_id else ["minecraft_web_manager", "demo_plugin"]
        updates = []
        if "minecraft_web_manager" in checked and self.self_update_available:
            updates.append({"plugin_id": "minecraft_web_manager", "current": "1.2.1", "latest": "1.2.2"})
        if "demo_plugin" in checked:
            updates.append({"plugin_id": "demo_plugin", "current": "2.4.0", "latest": "2.5.0"})
        self._emit(on_console_line, f"Checked {', '.join(checked)}")
        return {
            "accepted": True,
            "lines": [],
            "checked": checked,
            "updates": updates,
            "failed": False,
            "success": True,
        }

    def self_plugin_check_update(self, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        return {"plugin_id": "minecraft_web_manager", **self.plugin_check_update("minecraft_web_manager", on_console_line)}

    def self_plugin_update(self, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        self.self_update_available = self.always_update
        self._emit(on_console_line, "Demo update accepted; the panel would reload here.")
        if self.on_reload:
            threading.Thread(target=self.on_reload, name="demo-panel-reload", daemon=True).start()
        return {"accepted": True, "plugin_id": "minecraft_web_manager", "reloading": True}

    def plugin_update(self, plugin_id: str | None = None, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        self._emit(on_console_line, f"Demo update completed: {plugin_id or 'all'}")
        return {"accepted": True, "completed": True, "success": True, "failed": False, "noop": False, "skipped": False, "lines": []}

    def plugin_disable(self, plugin_id: str, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        return {"accepted": True, "plugin_id": plugin_id}

    def plugin_enable(self, file_name: str, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        return {"accepted": True, "file_name": file_name}

    def plugin_load(self, file_name: str) -> dict[str, Any]:
        return {"accepted": True, "file_name": file_name}

    def plugin_delete(self, plugin_id: str | None, file_name: str | None, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        return {"accepted": True, "plugin_id": plugin_id, "file_name": file_name}

    def plugin_configs(self) -> dict[str, Any]:
        return {
            "files": [
                {"plugin_id": "demo_plugin", "plugin_name": "Demo Plugin", "path": "config.json", "size": len(content), "editable": True}
                for path, content in self.plugin_files.items()
                for _plugin, _separator, _rel_path in [path.partition("/")]
            ]
        }

    def read_plugin_config(self, plugin_id: str, rel_path: str) -> dict[str, Any]:
        content = self.plugin_files.get(f"{plugin_id}/{rel_path}", "")
        return {"path": rel_path, "content": content, "size": len(content.encode()), "editable": True}

    def update_plugin_config(self, plugin_id: str, rel_path: str, content: str) -> dict[str, Any]:
        self.plugin_files[f"{plugin_id}/{rel_path}"] = content
        return {"saved": True}

    def mods(self) -> list[dict[str, Any]]:
        return [
            {"file": "demo-mod.jar", "name": "Demo Mod", "id": "demo_mod", "version": "0.8.0", "size": 2_400_000, "disabled": False},
            {"file": "minimap.jar.disabled", "name": "Mini Map", "id": "mini_map", "version": "1.1.0", "size": 1_100_000, "disabled": True},
        ]

    def mod_configs(self) -> dict[str, Any]:
        return {"files": [{"path": path, "size": len(content), "editable": True} for path, content in self.mod_files.items()]}

    def read_mod_config(self, rel_path: str) -> dict[str, Any]:
        content = self.mod_files.get(rel_path, "")
        return {"path": rel_path, "content": content, "size": len(content.encode()), "editable": True}

    def update_mod_config(self, rel_path: str, content: str) -> dict[str, Any]:
        self.mod_files[rel_path] = content
        return {"saved": True}

    def upload_mod(self, file_name: str, file_obj: Any, overwrite: bool) -> dict[str, Any]:
        return {"file": file_name or "uploaded-demo.jar", "overwritten": overwrite}

    def set_mod_enabled(self, filename: str, enabled: bool) -> dict[str, Any]:
        return {"file": filename, "enabled": enabled}

    def delete_mod(self, filename: str) -> dict[str, Any]:
        return {"file": filename}

    def player_action(self, action: str, target: str | None = None, reason: str | None = None) -> dict[str, Any]:
        if action == "whitelist_on":
            self.whitelist_enabled = True
        elif action == "whitelist_off":
            self.whitelist_enabled = False
        self._emit(None, "")
        return {"accepted": True, "action": action, "target": target, "result": "Demo action accepted"}

    def set_bot_flag(self, name: str, is_bot: bool) -> dict[str, Any]:
        for player in self.players:
            if player["name"] == name:
                player["is_bot"] = is_bot
                player["bot_source"] = "manual"
        return {"name": name, "is_bot": is_bot}

    def execute(self, command: str, transport: str, on_console_line: ConsoleCallback = None) -> dict[str, Any]:
        self._emit(on_console_line, f"Demo executed via {transport}: {command}")
        return {"accepted": True, "transport": transport, "result": "Demo command accepted"}

    def server_action(self, action: str) -> bool:
        if action == "stop":
            self.running = False
        elif action in {"start", "restart"}:
            self.running = True
        return True


class DemoRuntime:
    """Own the demo service so a self-update can simulate a reload cycle."""

    def __init__(self, config: ConfigStore, logger: logging.Logger, *, always_update: bool = False) -> None:
        self.config = config
        self.logger = logger
        self.always_update = always_update
        self.updated = False
        self.service: WebService | None = None

    def _new_service(self) -> WebService:
        bridge = DemoBridge(updated=self.updated, always_update=self.always_update, on_reload=self.reload)
        return WebService(bridge, self.config, self.logger, DemoHistory())

    def start(self) -> None:
        self.service = self._new_service()
        self.service.start()

    def reload(self) -> None:
        time.sleep(0.5)
        old_service = self.service
        if old_service is not None:
            old_service.stop()
        self.updated = True
        self.service = self._new_service()
        self.service.start()

    def stop(self) -> None:
        if self.service is not None:
            self.service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Web Manager with a fake MCDR bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18088)
    parser.add_argument(
        "--always-update",
        action="store_true",
        help="keep simulating an available panel update after the demo reloads",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[demo] %(levelname)s %(message)s")
    config_path = Path("/private/tmp/minecraft_web_manager-demo/config.json")
    config = ConfigStore(config_path)
    config.data["host"] = args.host
    config.data["port"] = args.port
    config.data["username"] = "admin"
    config.set_password("demo-password-123")
    runtime = DemoRuntime(
        config,
        logging.getLogger("minecraft_web_manager.demo"),
        always_update=args.always_update,
    )
    runtime.start()
    print(f"Demo panel: http://{args.host}:{args.port}/")
    print("Username: admin")
    print("Password: demo-password-123")
    print("Press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        runtime.stop()


if __name__ == "__main__":
    main()
