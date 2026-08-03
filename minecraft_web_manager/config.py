"""Persistent configuration and password helpers."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any


# JSON has no comment syntax, so the usage notes ship as a "_readme" key that is
# rewritten on every load (and ignored when reading settings back).
README_LINES = [
    "Minecraft Web Manager 配置文件。",
    "重置密码：把 password.salt 和 password.hash 都改成空字符串 \"\"，保存后执行 !!MCDR reload plugin minecraft_web_manager，新的一次性密码会打印在 MCDR 日志里。",
    "token_secret 是登录令牌的签名密钥，由插件自动生成；清空它会让所有已登录会话立即失效。",
    "token_ttl_seconds 是登录会话有效期（秒），默认 2592000（30 天）；活跃使用时会自动续期。",
    "bot_names 是手动标记为假人（Carpet bot）的玩家名列表（小写）；自动识别之外的手动兜底，可留空。",
    "bot_name_patterns 是额外的假人名称正则列表（默认匹配 Bot_/bot- 等前缀）；默认只对不在 usercache 中的玩家生效，避免误伤同名真玩家。",
    "bot_name_patterns_apply_to_all 设为 true 时名称规则对所有玩家生效（若你的假人也写入了 usercache）；同名真玩家请加入 not_bot_names。",
    "not_bot_names 是反向名单：即使匹配了名称规则或离线 UUID，也强制视为真人（小写）；误判时点行内「取消标记」会自动写入。",
    "host/port 是网页面板的监听地址，默认仅本机可访问；修改后需重载插件。",
    "RCON 不在这里配置：Minecraft 端在 server/server.properties，MCDR 端在 config.yml，两边的端口和密码必须一致。",
]

# Login sessions default to 30 days; active use slides the expiry forward, so a
# browser that is opened occasionally stays logged in without daily re-login.
DEFAULT_TOKEN_TTL_SECONDS = 30 * 24 * 3600
CONSOLE_BUFFER_SIZE = 1000

DEFAULT_CONFIG: dict[str, Any] = {
    "_readme": README_LINES,
    "host": "127.0.0.1",
    "port": 8088,
    "username": "admin",
    "password": {"salt": "", "hash": ""},
    "token_secret": "",
    "token_ttl_seconds": DEFAULT_TOKEN_TTL_SECONDS,
    "bot_names": [],
    "not_bot_names": [],
    "bot_name_patterns": ["(?i)^bot[_-]"],
    "bot_name_patterns_apply_to_all": False,
}


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # deepcopy so the nested password dict is never shared with DEFAULT_CONFIG
        data = copy.deepcopy(DEFAULT_CONFIG)
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            # Keys no longer in DEFAULT_CONFIG are dropped here, which migrates configs
            # written by older versions without any extra upgrade step.
            data.update({key: value for key, value in loaded.items() if key in data})
        data["_readme"] = list(README_LINES)  # always rewrite with the current notes
        if not data["token_secret"]:
            data["token_secret"] = secrets.token_urlsafe(48)
        self._write(data)
        return data

    def _write(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def save(self) -> None:
        with self.lock:
            self._write(self.data)

    def has_password(self) -> bool:
        password = self.data["password"]
        return bool(password["salt"] and password["hash"])

    def set_password(self, password: str) -> None:
        if len(password) < 12:
            raise ValueError("Password must be at least 12 characters long")
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        with self.lock:
            self.data["password"] = {
                "salt": base64.urlsafe_b64encode(salt).decode(),
                "hash": base64.urlsafe_b64encode(password_hash).decode(),
            }
            self.save()

    def verify_password(self, password: str) -> bool:
        credentials = self.data["password"]
        if not credentials["salt"] or not credentials["hash"]:
            return False
        try:
            salt = base64.urlsafe_b64decode(credentials["salt"])
            expected = base64.urlsafe_b64decode(credentials["hash"])
        except (ValueError, TypeError, binascii.Error):
            # Hand-edited config can hold invalid base64; treat it as "wrong password"
            # instead of crashing the login endpoint with a 500.
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return hmac.compare_digest(actual, expected)
