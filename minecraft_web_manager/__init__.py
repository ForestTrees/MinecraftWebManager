"""Minecraft Web Manager MCDR plugin entry point."""

from __future__ import annotations

import re
import secrets
import time
from pathlib import Path
from typing import Any

from .bridge import MCDRBridge
from .config import ConfigStore
from .metrics import MetricsHistory
from .web import WebService

PLUGIN_METADATA = {
    "id": "minecraft_web_manager",
    "version": "1.2.1",
    "name": "Minecraft Web Manager",
    "dependencies": {"mcdreforged": ">=2.15.0"},
}

_UUID_PATTERN = re.compile(r"^UUID of player (\S+) is ([0-9a-fA-F-]{36})$")
_LOGIN_PATTERN = re.compile(r"^(\S+)\[/(.+):(\d+)\]\s+logged in")
_LIST_PATTERN = re.compile(r"players online:\s*(.*)$", re.IGNORECASE)

_service: WebService | None = None
_history: MetricsHistory | None = None
_bridge: MCDRBridge | None = None
_players: dict[str, dict[str, Any]] = {}
_pending_player_meta: dict[str, dict[str, str]] = {}


def on_load(server, prev_module) -> None:
    global _service, _history, _bridge, _players, _pending_player_meta
    if prev_module is not None:
        if getattr(prev_module, "_service", None) is not None:
            prev_module._service.stop()
        if getattr(prev_module, "_history", None) is not None:
            prev_module._history.stop()
    _players = {}
    _pending_player_meta = {}
    config_path = Path("config") / "minecraft_web_manager" / "config.json"
    config = ConfigStore(config_path)
    if not config.has_password():
        bootstrap_password = secrets.token_urlsafe(18)
        config.set_password(bootstrap_password)
        server.logger.warning("Minecraft Web Manager bootstrap password: %s", bootstrap_password)
        server.logger.warning("Save it now. To reset it later, clear password.salt and password.hash in the plugin config, then reload.")
    bridge = MCDRBridge(server, _players, config)
    _bridge = bridge
    # Load (or create) the effective-value baseline for tracking saved-but-not-yet
    # effective server.properties changes; survives plugin reloads and MCDR restarts.
    bridge.bootstrap_pending(server.logger)
    # Detect which MCDR config.yml keys this MCDR version exposes, so the web UI can
    # render the matching visual editor even when the schema changes between versions.
    bridge.prewarm_mcdr_config()
    _history = MetricsHistory(bridge.sample, server.logger)
    try:
        _history.start()
        _service = WebService(bridge, config, server.logger, _history)
        _service.start()
    except Exception:
        # Don't leak the metrics thread if the web server fails to come up (e.g. the
        # configured port is already taken) — roll back and let MCDR report the error.
        if _history is not None:
            _history.stop()
            _history = None
        if _service is not None:
            _service.stop()
            _service = None
        raise
    server.logger.info("Minecraft Web Manager is listening on http://%s:%s", config.data["host"], config.data["port"])
    _seed_online_players(server)


def _seed_online_players(server) -> None:
    """Recover the currently-online players after a plugin reload.

    MCDR keeps no online-player list of its own and ``on_player_joined`` only fires for
    *new* joins, so a reload while the server is up would otherwise lose everyone who is
    already connected. We ask the server via RCON ``list``. Join time and IP can't be
    recovered after the fact, so they stay unknown rather than being invented.
    """
    try:
        if not server.is_server_running() or not server.is_rcon_running():
            return
        response = server.rcon_query("list")
    except Exception:
        return
    if not response:
        return
    match = _LIST_PATTERN.search(response)
    if not match:
        return
    for name in (part.strip() for part in match.group(1).split(",")):
        if name and name not in _players:
            _players[name] = {"joined_at": None, "ip": None, "uuid": None, "recovered": True}


def on_unload(server) -> None:
    global _service, _history, _bridge
    if _service is not None:
        _service.stop()
        _service = None
    if _history is not None:
        _history.stop()
        _history = None
    _bridge = None


def on_info(server, info) -> None:
    content = str(info.content) if info.content is not None else str(getattr(info, "raw_content", ""))
    # UUID / login lines are plain server output; capture IP+UUID for the next join event.
    if not info.is_user:
        uuid_match = _UUID_PATTERN.match(content)
        if uuid_match:
            name, player_uuid = uuid_match.groups()
            _pending_player_meta.setdefault(name, {})["uuid"] = player_uuid
        else:
            login_match = _LOGIN_PATTERN.match(content)
            if login_match:
                name, ip, _port = login_match.groups()
                _pending_player_meta.setdefault(name, {})["ip"] = ip

    if _service is None:
        return
    # Echo server output and in-game player chat (is_player) to the web console. Skip
    # MCDR's own console input — our web commands are echoed by the API layer instead,
    # so forwarding console input too would double them up.
    if info.is_from_console:
        return
    timestamp = ":".join(f"{value:02d}" for value in (info.hour or 0, info.min or 0, info.sec or 0))
    raw_content = str(getattr(info, "raw_content", info.content))
    _service.publish(
        "console",
        {
            "content": content,
            "raw": f"[Server] {raw_content}",
            "source": "player" if info.is_player else "server",
            "timestamp": timestamp,
        },
    )


def on_player_joined(server, player: str, info) -> None:
    meta = _pending_player_meta.pop(player, {})
    _players[player] = {"joined_at": time.time(), "ip": meta.get("ip"), "uuid": meta.get("uuid")}
    if _service is not None:
        _service.publish("player", {"action": "joined", "player": player})
        _service.publish("status", {"event": "player_joined"})


def on_player_left(server, player: str) -> None:
    _players.pop(player, None)
    if _service is not None:
        _service.publish("player", {"action": "left", "player": player})
        _service.publish("status", {"event": "player_left"})


def on_server_start(server) -> None:
    if _service is not None:
        _service.publish("status", {"event": "server_start"})


def on_server_startup(server) -> None:
    # The server finished starting: every property in the file is now in effect,
    # so pending-restart markers can be cleared.
    if _bridge is not None:
        _bridge.mark_server_started()
    if _service is not None:
        _service.publish("status", {"event": "server_startup"})


def on_server_stop(server, server_return_code: int) -> None:
    _players.clear()
    _pending_player_meta.clear()
    if _service is not None:
        _service.publish("status", {"event": "server_stop", "return_code": server_return_code})
