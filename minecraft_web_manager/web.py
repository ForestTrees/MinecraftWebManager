"""FastAPI application hosted in a dedicated thread for the MCDR plugin."""

from __future__ import annotations

import asyncio
import collections
import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import issue_token, verify_token
from .config import CONSOLE_BUFFER_SIZE, TOKEN_TTL_SECONDS
from .metrics import RANGES


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=512)


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=32_768)
    transport: str = Field(default="console", pattern="^(console|rcon)$")


class ActionRequest(BaseModel):
    action: str = Field(pattern="^(start|stop|restart)$")


class PluginReloadRequest(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=64)


class PropertiesUpdateRequest(BaseModel):
    changes: dict[str, str] = Field(default_factory=dict)


class PlayerActionRequest(BaseModel):
    action: str = Field(
        pattern="^(op|deop|kick|ban|pardon|ban_ip|pardon_ip|whitelist_add|whitelist_remove|whitelist_on|whitelist_off|whitelist_reload)$"
    )
    target: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=200)


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
            asyncio.run_coroutine_threadsafe(self._publish(event), self.loop)


class WebService:
    def __init__(self, bridge, config, static_dir: Path, logger, history=None) -> None:
        self.bridge = bridge
        self.config = config
        self.static_dir = static_dir
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

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Minecraft Web Manager", version="0.1.0", docs_url="/api/docs", redoc_url=None)
        app.mount("/assets", StaticFiles(directory=self.static_dir), name="assets")

        @app.on_event("startup")
        async def app_started() -> None:
            self.hub.bind_loop(asyncio.get_running_loop())
            self.started.set()

        async def require_user(request: Request) -> dict[str, Any]:
            authorization = request.headers.get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token:
                raise HTTPException(status_code=401, detail="Bearer token required")
            return self._claims_from_token(token)

        @app.get("/", include_in_schema=False)
        async def login_page() -> FileResponse:
            return FileResponse(self.static_dir / "login.html")

        @app.get("/console", include_in_schema=False)
        async def console_page() -> FileResponse:
            return FileResponse(self.static_dir / "console.html")

        @app.post("/api/auth/login")
        async def login(body: LoginRequest) -> dict[str, Any]:
            if body.username != self.config.data["username"] or not self.config.verify_password(body.password):
                raise HTTPException(status_code=401, detail="Invalid username or password")
            token, expires_at = issue_token(
                self.config.data["token_secret"], body.username, TOKEN_TTL_SECONDS
            )
            return {"access_token": token, "token_type": "bearer", "expires_at": expires_at}

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
        async def players_roster(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.bridge.roster)
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        @app.post("/api/players/action")
        async def player_action(body: PlayerActionRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.player_action, body.action, body.target, body.reason)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except RuntimeError as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
            self.publish("status", {"event": "player_action", "action": body.action, "target": body.target, "by": user["sub"]})
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
            return {"plugins": await asyncio.to_thread(self.bridge.plugins)}

        @app.post("/api/plugins/reload")
        async def reload_plugin(body: PluginReloadRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                result = await asyncio.to_thread(self.bridge.reload_plugin, body.plugin_id)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            self.publish("status", {"event": "plugin_reload", "plugin_id": body.plugin_id, "by": user["sub"]})
            return result

        @app.get("/api/mods")
        async def mods(_: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                return {"mods": await asyncio.to_thread(self.bridge.mods)}
            except Exception as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

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
            self.publish("status", {"event": "properties_updated", "keys": result["applied"], "by": user["sub"]})
            return result

        @app.post("/api/commands")
        async def command(body: CommandRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            # Echo the sent command into the console stream so the operator sees what
            # they issued; server output / replies arrive separately as their own lines.
            self.publish(
                "console",
                {"content": body.command, "raw": f"[Web/{user['sub']}] > {body.command}", "source": "command", "timestamp": ""},
            )
            try:
                return await asyncio.to_thread(
                    self.bridge.execute,
                    body.command,
                    body.transport,
                    lambda line: self.publish("console", line),
                )
            except (RuntimeError, ValueError) as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.post("/api/server/actions")
        async def server_action(body: ActionRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
            try:
                accepted = await asyncio.to_thread(self.bridge.server_action, body.action)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            self.publish("status", {"event": "server_action", "action": body.action, "by": user["sub"]})
            return {"accepted": accepted, "action": body.action}

        @app.websocket("/ws/events")
        async def events_socket(websocket: WebSocket) -> None:
            token = websocket.query_params.get("token", "")
            try:
                self._claims_from_token(token)
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
