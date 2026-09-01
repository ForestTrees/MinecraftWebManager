"""FastAPI application hosted in a dedicated thread for the MCDR plugin."""

from __future__ import annotations

import asyncio
import collections
import html
import mimetypes
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .auth import issue_token, verify_token
from .config import CONSOLE_BUFFER_SIZE, DEFAULT_TOKEN_TTL_SECONDS
from .metrics import RANGES

# Session cookie: HttpOnly so page scripts never see the token, and SameSite=Strict
# so cross-site pages can't ride an existing session (CSRF).
SESSION_COOKIE = "mwm_session"

# Cheap in-memory login throttle. PBKDF2 already costs ~100ms per attempt, but a
# public-facing panel shouldn't allow an unbounded online brute-force either.
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 10
_login_attempts: dict[str, collections.deque] = {}
_login_lock = threading.Lock()


def _check_login_rate(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    with _login_lock:
        attempts = _login_attempts.setdefault(client_ip, collections.deque())
        while attempts and attempts[0] < now - _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many login attempts, try again later")
        attempts.append(now)


def _clear_login_rate(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    with _login_lock:
        _login_attempts.pop(client_ip, None)

# Explicit rather than left to mimetypes: the OS mapping varies by platform and the
# charset matters (every page and script contains Chinese text).
_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


def load_assets() -> dict[str, tuple[bytes, str]]:
    """Read the bundled static/ files into memory as {filename: (bytes, media type)}.

    A packed plugin (``.mcdr``) is a zip that MCDR imports with zipimport, so
    ``static/`` has no real filesystem path there and Starlette's StaticFiles /
    FileResponse cannot serve from it. Reading the bytes once at load time works for
    both the unpacked folder and the packed archive; the whole bundle is well under
    a megabyte.
    """
    static_directory = Path(__file__).parent / "static"
    if static_directory.is_dir():
        return {
            path.name: (path.read_bytes(), _media_type(path.name))
            for path in sorted(static_directory.iterdir())
            if path.is_file()
        }

    archive = getattr(__loader__, "archive", None)  # set by zipimport for packed plugins
    if archive is None:
        raise RuntimeError(f"Cannot locate the bundled static files at {static_directory}")
    prefix = f"{__package__}/static/"
    with zipfile.ZipFile(archive) as bundle:
        return {
            name[len(prefix):]: (bundle.read(name), _media_type(name))
            for name in bundle.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }


def _media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _MEDIA_TYPES.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=512)


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=32_768)
    transport: str = Field(default="console", pattern="^(console|rcon)$")


class CommandSuggestRequest(BaseModel):
    command: str = Field(max_length=32_768)
    cursor: int | None = Field(default=None, ge=0, le=32_768)


class ActionRequest(BaseModel):
    action: str = Field(pattern="^(start|stop|restart)$")


class PluginReloadRequest(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=64)


class PluginOperationRequest(BaseModel):
    plugin_id: str | None = Field(default=None, max_length=64)
    file_name: str | None = Field(default=None, max_length=255)


class PluginConfigUpdateRequest(BaseModel):
    content: str = Field(max_length=1_000_000)


class PropertiesUpdateRequest(BaseModel):
    changes: dict[str, str] = Field(default_factory=dict)


class PlayerActionRequest(BaseModel):
    action: str = Field(
        pattern="^(op|deop|kick|ban|pardon|ban_ip|pardon_ip|whitelist_add|whitelist_remove|whitelist_on|whitelist_off|whitelist_reload)$"
    )
    target: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=200)


class BotFlagRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    is_bot: bool = True


class ModConfigUpdateRequest(BaseModel):
    content: str = Field(max_length=1_000_000)


class McdrConfigUpdateRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


class EventHub:
    """Fan-out events from MCDR's thread to authenticated WebSocket clients."""

    def __init__(self, buffer_size: int):
        self.buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=buffer_size)
        # A client's queue is primed with the whole backlog on connect, so it must be
        # able to hold at least that many events plus some live headroom. Sizing it below
        # the buffer (the old maxsize=256) made register() raise QueueFull once more than
        # 256 events had accumulated, which crashed every new/reconnecting socket and left
        # the panel stuck on "实时已断开".
        self.client_queue_maxsize = buffer_size + 64
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.ready.set()

    async def register(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.client_queue_maxsize)
        self.clients.add(queue)
        # Replay recent history so a freshly-opened panel isn't blank. Guard defensively:
        # a new client must never be able to crash the socket, whatever the backlog size.
        for event in list(self.buffer):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
        return queue

    async def unregister(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.clients.discard(queue)

    async def _publish(self, event: dict[str, Any]) -> None:
        self.buffer.append(event)
        for queue in tuple(self.clients):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def publish(self, event: dict[str, Any]) -> None:
        if self.ready.wait(timeout=1.0) and self.loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._publish(event), self.loop)
            except RuntimeError:
                # The web loop is shutting down (e.g. the panel is reloading
                # itself); the event is dropped instead of failing the caller.
                pass


class WebService:
    def __init__(self, bridge, config, logger, history=None) -> None:
        self.bridge = bridge
        self.config = config
        self.assets = load_assets()
        self.logger = logger
        self.history = history
        self.hub = EventHub(CONSOLE_BUFFER_SIZE)
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.started = threading.Event()
        self.start_error: Exception | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="Minecraft Web Manager", daemon=True)
        self.thread.start()
        if not self.started.wait(timeout=10):
            raise RuntimeError("Timed out while starting Minecraft Web Manager")
        if self.start_error is not None:
            raise RuntimeError("Could not start Minecraft Web Manager") from self.start_error

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self.hub.publish({"type": event_type, "data": payload})

    def _run(self) -> None:
        try:
            app = self._build_app()
            settings = uvicorn.Config(
                app,
                host=self.config.data["host"],
                port=self.config.data["port"],
                log_level="warning",
                access_log=False,
            )
            self.server = uvicorn.Server(settings)
            self.server.run()
            self.started.set()
        except BaseException as error:  # keep MCDR alive if the port/dependencies are invalid
            self.start_error = error
            self.started.set()
            self.logger.exception("Minecraft Web Manager failed to start")

    def _claims_from_token(self, token: str) -> dict[str, Any]:
        claims = verify_token(self.config.data["token_secret"], token)
        if claims is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return claims

    def _session_ttl(self) -> int:
        try:
            ttl = int(self.config.data.get("token_ttl_seconds", DEFAULT_TOKEN_TTL_SECONDS))
        except (TypeError, ValueError):
            ttl = DEFAULT_TOKEN_TTL_SECONDS
        return max(300, ttl)

    def _render_page(self, filename: str) -> HTMLResponse:
        """Serve a bundled HTML page with the configurable panel title injected."""
        content = self.assets[filename][0].decode("utf-8")
        panel_title = str(self.config.data.get("panel_title", "MC Web Manager"))
        return HTMLResponse(content.replace("__MWM_PANEL_TITLE__", html.escape(panel_title)))

    def _build_app(self) -> FastAPI:
        # Interactive API docs are disabled: they were publicly reachable without
        # authentication and reveal the whole command surface of the panel.
        app = FastAPI(title="Minecraft Web Manager", version="1.2.4", docs_url=None, openapi_url=None, redoc_url=None)

        @app.middleware("http")
        async def renew_session(request: Request, call_next):
            """Slide the session cookie forward while the admin is actively using it."""
            response = await call_next(request)
            if request.url.path == "/api/auth/logout":
                return response  # never re-issue a cookie on the logout response
            token = request.cookies.get(SESSION_COOKIE)
            if token:
                claims = verify_token(self.config.data["token_secret"], token)
                if claims is not None:
                    ttl = self._session_ttl()
                    expires_at = int(claims.get("exp", 0))
                    if expires_at - time.time() < ttl // 2:
                        fresh, _ = issue_token(
                            self.config.data["token_secret"], str(claims.get("sub", "admin")), ttl
                        )
                        response.set_cookie(
                            SESSION_COOKIE,
                            fresh,
                            max_age=ttl,
                            httponly=True,
                            samesite="strict",
                            secure=request.url.scheme == "https",
                            path="/",
                        )
            return response

        @app.get("/assets/{filename}", include_in_schema=False)
        async def asset(filename: str) -> Response:
            # A path parameter never spans "/", and this is a plain dict lookup, so
            # there is nothing here for a traversal attempt to reach.
            found = self.assets.get(filename)
            if found is None:
                raise HTTPException(status_code=404, detail="Not found")
            content, media_type = found
            return Response(content, media_type=media_type)

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon_ico() -> Response:
            # Browsers use the <link rel="icon"> SVG, but some clients still request the
            # classic /favicon.ico path by default — serve the same logo there.
            found = self.assets.get("favicon.svg")
            if found is None:
                raise HTTPException(status_code=404, detail="Not found")
            content, media_type = found
            return Response(content, media_type=media_type)

        @app.on_event("startup")
        async def app_started() -> None:
            self.hub.bind_loop(asyncio.get_running_loop())
            self.started.set()

        async def require_user(request: Request) -> dict[str, Any]:
            claims = None
            token = request.cookies.get(SESSION_COOKIE)
            if token:
                claims = verify_token(self.config.data["token_secret"], token)
            if claims is None:
                # Bearer header is still accepted (e.g. for scripted API clients),
                # but the browser uses the HttpOnly cookie.
                authorization = request.headers.get("authorization", "")
                scheme, _, token = authorization.partition(" ")
                if scheme.lower() == "bearer" and token:
                    claims = self._claims_from_token(token)
            if claims is None:
                raise HTTPException(status_code=401, detail="Invalid or expired session")
            return claims

        @app.get("/", include_in_schema=False)
        async def login_page(request: Request) -> Response:
            token = request.cookies.get(SESSION_COOKIE)
            if token and verify_token(self.config.data["token_secret"], token) is not None:
                return RedirectResponse("/console")
            return self._render_page("login.html")

        @app.get("/console", include_in_schema=False)
        async def console_page() -> HTMLResponse:
            return self._render_page("console.html")

        @app.post("/api/auth/login")
        async def login(body: LoginRequest, request: Request) -> Response:
            _check_login_rate(request)
            if body.username != self.config.data["username"] or not self.config.verify_password(body.password):
                raise HTTPException(status_code=401, detail="Invalid username or password")
            _clear_login_rate(request)
            ttl = self._session_ttl()
            token, expires_at = issue_token(self.config.data["token_secret"], body.username, ttl)
            response = JSONResponse({"ok": True, "expires_at": expires_at})
            response.set_cookie(
                SESSION_COOKIE,
                token,
                max_age=ttl,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                path="/",
            )
            return response

        @app.post("/api/auth/logout")
        async def logout() -> Response:
            response = JSONResponse({"ok": True})
            response.delete_cookie(SESSION_COOKIE, path="/")
            return response

        @app.get("/api/server/status")
        async def status(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.status)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.get("/api/players")
        async def players(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            status_data = await asyncio.to_thread(self.bridge.status)
            return {"players": status_data["players"], "count": status_data["player_count"]}

        @app.get("/api/players/detail")
        async def players_detail(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return {"players": await asyncio.to_thread(self.bridge.players_detail)}
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.get("/api/players/roster")
        async def players_roster(
            _: dict[str, Any] = Depends(require_user),
            light: bool = Query(False),
        ) -> dict[str, Any]:
            try:
                # light=1 skips per-player RCON queries, so a whitelist toggle can
                # refresh the access lists almost instantly.
                return await asyncio.to_thread(self.bridge.roster, include_details=not light)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.post("/api/players/action")
        async def player_action(body: PlayerActionRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.player_action, body.action, body.target, body.reason)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "player_action", "action": body.action, "target": body.target, "by": user["sub"]})
            return result

        @app.post("/api/players/bot")
        async def set_bot_flag(body: BotFlagRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.set_bot_flag, body.name, body.is_bot)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish(
                "status",
                {"event": "bot_flag", "name": result["name"], "is_bot": result["is_bot"], "by": user["sub"]},
            )
            return result

        @app.get("/api/world")
        async def world(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.world)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.get("/api/performance")
        async def performance(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.metrics)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.get("/api/metrics/history")
        async def metrics_history(
            _: dict[str, Any] = Depends(require_user),
            range_key: str = Query("1h", alias="range"),
            points: int = Query(300, ge=10, le=1440),
        ) -> dict[str, Any]:
            if range_key not in RANGES:
                raise HTTPException(status_code=400, detail=f"Unsupported range, expected one of {sorted(RANGES)}")
            if self.history is None:
                raise HTTPException(status_code=503, detail="Metrics history is not running")
            return await asyncio.to_thread(self.history.query, range_key, points)

        @app.get("/api/plugins")
        async def plugins(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return {"plugins": await asyncio.to_thread(self.bridge.plugins)}
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.post("/api/plugins/reload")
        async def reload_plugin(body: PluginReloadRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.reload_plugin, body.plugin_id)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "plugin_reload", "plugin_id": body.plugin_id, "by": user["sub"]})
            return result

        @app.post("/api/plugins/check_update")
        async def plugin_check_update(
            body: PluginOperationRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(
                    self.bridge.plugin_check_update, body.plugin_id, lambda line: self.publish("console", line)
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "plugin_check_update", "plugin_id": body.plugin_id, "by": user["sub"]})
            return result

        @app.get("/api/plugins/self_update")
        async def self_plugin_check_update(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(
                    self.bridge.self_plugin_check_update, lambda line: self.publish("console", line)
                )
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            update = next(
                (item for item in result.get("updates", []) if item.get("plugin_id") == result["plugin_id"]),
                None,
            )
            return {
                "plugin_id": result["plugin_id"],
                "available": (
                    update is not None
                    and result.get("completed", False)
                    and result.get("success", False)
                    and not result.get("failed", False)
                ),
                "current": update.get("current") if update else None,
                "latest": update.get("latest") if update else None,
                "failed": result.get("failed", False),
                "completed": result.get("completed", False),
                "success": result.get("success", False),
            }

        @app.post("/api/plugins/self_update")
        async def self_plugin_update(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(
                    self.bridge.self_plugin_update, lambda line: self.publish("console", line)
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "self_plugin_update", "plugin_id": result["plugin_id"], "by": user["sub"]})
            return result

        @app.post("/api/plugins/update")
        async def plugin_update(
            body: PluginOperationRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(
                    self.bridge.plugin_update, body.plugin_id, lambda line: self.publish("console", line)
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "plugin_update", "plugin_id": body.plugin_id, "by": user["sub"]})
            return result

        @app.post("/api/plugins/disable")
        async def plugin_disable(
            body: PluginOperationRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            if not body.plugin_id:
                raise HTTPException(status_code=400, detail="plugin_id is required")
            try:
                result = await asyncio.to_thread(
                    self.bridge.plugin_disable, body.plugin_id, lambda line: self.publish("console", line)
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "plugin_disabled", "plugin_id": body.plugin_id, "by": user["sub"]})
            return result

        @app.post("/api/plugins/enable")
        async def plugin_enable(
            body: PluginOperationRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            if not body.file_name:
                raise HTTPException(status_code=400, detail="file_name is required")
            try:
                result = await asyncio.to_thread(
                    self.bridge.plugin_enable, body.file_name, lambda line: self.publish("console", line)
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "plugin_enabled", "file_name": body.file_name, "by": user["sub"]})
            return result

        @app.post("/api/plugins/load")
        async def plugin_load(
            body: PluginOperationRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            if not body.file_name:
                raise HTTPException(status_code=400, detail="file_name is required")
            try:
                result = await asyncio.to_thread(self.bridge.plugin_load, body.file_name)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "plugin_loaded", "file_name": body.file_name, "by": user["sub"]})
            return result

        @app.post("/api/plugins/delete")
        async def plugin_delete(
            body: PluginOperationRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(
                    self.bridge.plugin_delete, body.plugin_id, body.file_name, lambda line: self.publish("console", line)
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "plugin_deleted", "plugin_id": body.plugin_id, "file_name": body.file_name, "by": user["sub"]})
            return result

        @app.get("/api/plugins/configs")
        async def plugin_configs(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.plugin_configs)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.get("/api/plugins/configs/{plugin_id}/{rel_path:path}")
        async def read_plugin_config(
            plugin_id: str, rel_path: str, _: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.read_plugin_config, plugin_id, rel_path)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.put("/api/plugins/configs/{plugin_id}/{rel_path:path}")
        async def update_plugin_config(
            plugin_id: str,
            rel_path: str,
            body: PluginConfigUpdateRequest,
            user: dict[str, Any] = Depends(require_user),
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(
                    self.bridge.update_plugin_config, plugin_id, rel_path, body.content
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish(
                "status",
                {"event": "plugin_config_saved", "plugin_id": plugin_id, "path": rel_path, "by": user["sub"]},
            )
            return result

        @app.get("/api/mods")
        async def mods(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return {"mods": await asyncio.to_thread(self.bridge.mods)}
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.post("/api/mods/upload")
        async def upload_mod(
            user: dict[str, Any] = Depends(require_user),
            file: UploadFile = File(...),
            overwrite: bool = Query(False),
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.upload_mod, file.filename or "", file.file, overwrite)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "mod_upload", "file": result["file"], "by": user["sub"]})
            return result

        @app.post("/api/mods/{filename}/disable")
        async def disable_mod(
            filename: str, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.set_mod_enabled, filename, False)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "mod_disabled", "file": result["file"], "by": user["sub"]})
            return result

        @app.post("/api/mods/{filename}/enable")
        async def enable_mod(
            filename: str, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.set_mod_enabled, filename, True)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "mod_enabled", "file": result["file"], "by": user["sub"]})
            return result

        @app.delete("/api/mods/{filename}")
        async def delete_mod(
            filename: str, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.delete_mod, filename)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "mod_deleted", "file": result["file"], "by": user["sub"]})
            return result

        @app.get("/api/mods/configs")
        async def mod_configs(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.mod_configs)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.get("/api/mods/configs/{rel_path:path}")
        async def read_mod_config(rel_path: str, _: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.read_mod_config, rel_path)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.put("/api/mods/configs/{rel_path:path}")
        async def update_mod_config(
            rel_path: str, body: ModConfigUpdateRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.update_mod_config, rel_path, body.content)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "mod_config_saved", "path": rel_path, "by": user["sub"]})
            return result

        @app.get("/api/mcdr/config")
        async def mcdr_config(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.mcdr_config)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.put("/api/mcdr/config")
        async def update_mcdr_config(
            body: McdrConfigUpdateRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.update_mcdr_config, body.changes)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "mcdr_config_saved", "path": "config.yml", "by": user["sub"]})
            return result

        @app.get("/api/server/properties")
        async def server_properties(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.server_properties)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.post("/api/server/properties")
        async def update_server_properties(
            body: PropertiesUpdateRequest, user: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.update_server_properties, body.changes)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "properties_updated", "keys": result["applied"], "by": user["sub"]})
            return result

        @app.post("/api/commands")
        async def command(body: CommandRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            command_text = body.command.strip()
            if not command_text:
                raise HTTPException(status_code=400, detail="Command cannot be empty")
            # Echo the sent command into the console stream so the operator sees what
            # they issued; server output / replies arrive separately as their own lines.
            self.publish(
                "console",
                {"content": command_text, "raw": f"[Web/{user['sub']}] > {command_text}", "source": "command", "timestamp": ""},
            )
            try:
                return await asyncio.to_thread(
                    self.bridge.execute,
                    command_text,
                    body.transport,
                    lambda line: self.publish("console", line),
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.post("/api/commands/suggest")
        async def suggest_commands(
            body: CommandSuggestRequest, _: dict[str, Any] = Depends(require_user)
        ) -> dict[str, Any]:
            if body.cursor is not None and body.cursor > len(body.command):
                raise HTTPException(status_code=400, detail="Cursor is outside the command")
            try:
                return await asyncio.to_thread(self.bridge.suggest_commands, body.command, body.cursor)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.post("/api/server/actions")
        async def server_action(body: ActionRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                accepted = await asyncio.to_thread(self.bridge.server_action, body.action)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "server_action", "action": body.action, "by": user["sub"]})
            return {"accepted": accepted, "action": body.action}

        @app.websocket("/ws/events")
        async def events_socket(websocket: WebSocket) -> None:
            claims = None
            token = websocket.cookies.get(SESSION_COOKIE)
            if token:
                claims = verify_token(self.config.data["token_secret"], token)
            if claims is None:
                # Legacy fallback for scripted clients that cannot send cookies.
                try:
                    claims = self._claims_from_token(websocket.query_params.get("token", ""))
                except HTTPException:
                    await websocket.close(code=4401)
                    return
            await websocket.accept()
            queue = await self.hub.register()

            async def sender() -> None:
                while True:
                    await websocket.send_json(await queue.get())

            sender_task = asyncio.create_task(sender())
            try:
                while True:
                    # The client may send ping messages; events are sent by sender_task.
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                sender_task.cancel()
                await self.hub.unregister(queue)

        return app
