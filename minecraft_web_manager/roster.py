"""Read-only parsing of the vanilla server's player registry files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Where per-player save files live. This server uses world/players/data/, but classic
# vanilla uses world/playerdata/ — scan both rather than assuming either layout.
PLAYER_DATA_DIRECTORIES = (("players", "data"), ("playerdata",))


def _read_list(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _uuid_of(entry: dict[str, Any]) -> str | None:
    value = entry.get("uuid")
    return str(value).lower() if value else None


def _played(world_directory: Path) -> dict[str, float | None]:
    """uuid -> newest player-data mtime. The server rewrites `<uuid>.dat` on save and on
    logout, so its mtime is the best available proxy for "last seen" without our own
    persistence layer (which would not cover players who joined before the plugin)."""
    found: dict[str, float | None] = {}
    for parts in PLAYER_DATA_DIRECTORIES:
        directory = world_directory.joinpath(*parts)
        try:
            files = list(directory.glob("*.dat"))
        except OSError:
            continue
        for file in files:
            uuid = file.stem.lower()
            try:
                mtime: float | None = file.stat().st_mtime
            except OSError:
                mtime = None
            existing = found.get(uuid)
            if uuid not in found or (mtime is not None and (existing is None or mtime > existing)):
                found[uuid] = mtime
    return found


def build(server_directory: Path, world_directory: Path) -> dict[str, Any]:
    """Merge every known player registry into one roster plus the raw access lists."""
    ops = _read_list(server_directory / "ops.json")
    whitelist = _read_list(server_directory / "whitelist.json")
    banned_players = _read_list(server_directory / "banned-players.json")
    banned_ips = _read_list(server_directory / "banned-ips.json")
    usercache = _read_list(server_directory / "usercache.json")

    op_map = {uuid: entry for entry in ops if (uuid := _uuid_of(entry))}
    whitelist_map = {uuid: entry for entry in whitelist if (uuid := _uuid_of(entry))}
    banned_map = {uuid: entry for entry in banned_players if (uuid := _uuid_of(entry))}

    # A name can be missing from usercache but present in ops/whitelist/bans, so merge
    # every source before deciding a player is nameless.
    names: dict[str, str] = {}
    for entry in (*usercache, *ops, *whitelist, *banned_players):
        uuid = _uuid_of(entry)
        name = entry.get("name")
        if uuid and name:
            names.setdefault(uuid, str(name))

    played = _played(world_directory)

    players = []
    for uuid in sorted(names.keys() | played.keys()):
        ban = banned_map.get(uuid)
        players.append(
            {
                "uuid": uuid,
                "name": names.get(uuid),
                "op": uuid in op_map,
                "op_level": op_map.get(uuid, {}).get("level"),
                "whitelisted": uuid in whitelist_map,
                "banned": ban is not None,
                "ban_reason": (ban or {}).get("reason"),
                "has_played": uuid in played,
                "last_seen": played.get(uuid),
            }
        )

    return {
        "players": players,
        "ops": [
            {"uuid": _uuid_of(entry), "name": entry.get("name"), "level": entry.get("level")}
            for entry in ops
        ],
        "whitelist": [{"uuid": _uuid_of(entry), "name": entry.get("name")} for entry in whitelist],
        "banned_players": [
            {
                "uuid": _uuid_of(entry),
                "name": entry.get("name"),
                "reason": entry.get("reason"),
                "created": entry.get("created"),
                "expires": entry.get("expires"),
                "source": entry.get("source"),
            }
            for entry in banned_players
        ],
        "banned_ips": [
            {
                "ip": entry.get("ip"),
                "reason": entry.get("reason"),
                "created": entry.get("created"),
                "expires": entry.get("expires"),
                "source": entry.get("source"),
            }
            for entry in banned_ips
        ],
    }
