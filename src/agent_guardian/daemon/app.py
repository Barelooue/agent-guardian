"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agent_guardian.daemon.channels import build_channels
from agent_guardian.daemon.config import DaemonConfig, load_config
from agent_guardian.daemon.db import init_db
from agent_guardian.daemon.events import EventHub
from agent_guardian.daemon.media import MediaStore
from agent_guardian.daemon.service import InterventionService
from agent_guardian.daemon.store import InterventionStore, StoreError
from agent_guardian.daemon.takeover_store import TakeoverStore
from agent_guardian.daemon.telegram_poller import TelegramPoller
from agent_guardian.schemas import (
    ChannelName,
    Envelope,
    ErrorCode,
    ErrorPayload,
    InterventionCancel,
    InterventionDecision,
    InterventionRequest,
    MessageType,
    make_envelope,
)

logger = logging.getLogger(__name__)


def _http_status(code: ErrorCode) -> int:
    mapping = {
        ErrorCode.AG_INVALID_REQUEST: 400,
        ErrorCode.AG_UNSUPPORTED_VERSION: 400,
        ErrorCode.AG_NOT_FOUND: 404,
        ErrorCode.AG_STATE_CONFLICT: 409,
        ErrorCode.AG_ALREADY_TERMINAL: 409,
        ErrorCode.AG_TIMEOUT: 408,
        ErrorCode.AG_PERSISTENCE_ERROR: 500,
        ErrorCode.AG_INTERNAL: 500,
        ErrorCode.AG_CHANNEL_UNAVAILABLE: 502,
        ErrorCode.AG_CHANNEL_RETRY_EXHAUSTED: 502,
    }
    return mapping.get(code, 500)


def _error_envelope(exc: StoreError) -> Envelope:
    payload = ErrorPayload(
        code=exc.code,
        message=exc.message,
        retryable=exc.code
        in {
            ErrorCode.AG_PERSISTENCE_ERROR,
            ErrorCode.AG_INTERNAL,
            ErrorCode.AG_CHANNEL_UNAVAILABLE,
        },
        details=exc.kwargs.get("details", {}),
        intervention_id=exc.kwargs.get("intervention_id"),
        current_status=exc.kwargs.get("current_status"),
    )
    return make_envelope(MessageType.ERROR, payload)


async def _bootstrap_state(
    app: FastAPI,
    *,
    db_path: str | Path,
    enable_terminal_stdin: bool,
    config: DaemonConfig | None = None,
) -> None:
    if hasattr(app.state, "service") and app.state.service is not None:
        from agent_guardian.swarm import AgentHubManager

        if getattr(app.state, "swarm_hub", None) is None:
            app.state.swarm_hub = AgentHubManager()
        takeover_store = getattr(app.state, "takeover_store", None)
        if takeover_store is None and getattr(app.state, "db", None) is not None:
            takeover_store = TakeoverStore(app.state.db)
            app.state.takeover_store = takeover_store
        if takeover_store is not None:
            app.state.swarm_hub.attach_takeover_store(takeover_store)
        return

    cfg = config or getattr(app.state, "config", None) or load_config()
    conn = await init_db(str(db_path))
    store = InterventionStore(conn)
    takeover_store = TakeoverStore(conn)
    hub = EventHub()
    media_root = Path(db_path).resolve().parent / "agent_guardian_media"
    media = MediaStore(media_root)
    channels = build_channels(
        cfg,
        media_root=media_root,
        public_base_url=cfg.public_base_url,
    )
    default_channels = [ChannelName(c) for c in cfg.default_channels]
    service = InterventionService(
        store,
        hub,
        channels=channels,
        enable_terminal_stdin=enable_terminal_stdin,
        default_channels=default_channels,
        media=media,
    )
    app.state.db = conn
    app.state.store = store
    app.state.takeover_store = takeover_store
    app.state.hub = hub
    app.state.service = service
    app.state.config = cfg
    app.state.media = media
    app.state.telegram_poller = None
    if not hasattr(app.state, "swarm_hub") or app.state.swarm_hub is None:
        from agent_guardian.swarm import AgentHubManager

        app.state.swarm_hub = AgentHubManager(takeover_store=takeover_store)
        logger.info("Swarm AgentHubManager ready")
    else:
        app.state.swarm_hub.attach_takeover_store(takeover_store)

    tg = channels.get(ChannelName.TELEGRAM)
    if tg is not None:
        from agent_guardian.daemon.channels.telegram import TelegramChannel

        if isinstance(tg, TelegramChannel):
            poller = TelegramPoller(tg, service)
            poller.start()
            app.state.telegram_poller = poller
            logger.info(
                "Telegram channel enabled (chat_id=%s); poller started",
                cfg.telegram_chat_id,
            )
    else:
        logger.info("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    logger.info("Web console: %s/ui/", cfg.public_base_url)
    await service.recover_open()


def create_app(
    *,
    db_path: str | Path = "agent_guardian.db",
    enable_terminal_stdin: bool = True,
    config: DaemonConfig | None = None,
) -> FastAPI:
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _bootstrap_state(
            app,
            db_path=db_path,
            enable_terminal_stdin=enable_terminal_stdin,
            config=cfg,
        )
        try:
            yield
        finally:
            poller = getattr(app.state, "telegram_poller", None)
            if poller is not None:
                await poller.stop()
            service = getattr(app.state, "service", None)
            if service is not None:
                await service.aclose()
            conn = getattr(app.state, "db", None)
            if conn is not None:
                await conn.close()

    app = FastAPI(title="Agent Guardian Daemon", version="0.2.0", lifespan=lifespan)
    app.state.db_path = str(db_path)
    app.state.enable_terminal_stdin = enable_terminal_stdin
    app.state.config = cfg

    static_dir = Path(__file__).resolve().parent / "static" / "ui"
    if static_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    from agent_guardian.daemon.swarm_api import build_swarm_router

    app.include_router(build_swarm_router())

    @app.middleware("http")
    async def ensure_state(request: Request, call_next):
        await _bootstrap_state(
            request.app,
            db_path=request.app.state.db_path,
            enable_terminal_stdin=request.app.state.enable_terminal_stdin,
            config=getattr(request.app.state, "config", None),
        )
        return await call_next(request)

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        cfg_now: DaemonConfig = getattr(request.app.state, "config", cfg)
        swarm = getattr(request.app.state, "swarm_hub", None)
        return {
            "status": "ok",
            "protocol_version": "1.0.0",
            "ui": "/ui/",
            "channels": {
                "telegram": cfg_now.telegram_enabled,
                "bark": cfg_now.bark_enabled,
                "webhook": cfg_now.webhook_enabled,
                "terminal": True,
            },
            "default_channels": list(cfg_now.default_channels),
            "swarm_agents": swarm.size if swarm is not None else 0,
        }

    @app.get("/v1/interventions")
    async def list_interventions(request: Request, status: str = "open") -> JSONResponse:
        service: InterventionService = request.app.state.service
        if status != "open":
            return JSONResponse(
                {"error": "only status=open is supported in Phase 2"},
                status_code=400,
            )
        items = await service.list_open_summaries()
        return JSONResponse({"items": [i.model_dump(mode="json") for i in items]})

    @app.get("/v1/media/{filename}")
    async def get_media(filename: str, request: Request) -> FileResponse:
        media: MediaStore | None = getattr(request.app.state, "media", None)
        if media is None:
            raise HTTPException(status_code=404, detail="media store unavailable")
        path = media.resolve_path(filename)
        if path is None:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path)

    @app.post("/v1/interventions")
    async def create_intervention(request: Request) -> JSONResponse:
        service: InterventionService = request.app.state.service
        body = await request.json()
        try:
            envelope = Envelope.model_validate(body)
            if envelope.message_type != MessageType.INTERVENTION_CREATE:
                raise StoreError(
                    ErrorCode.AG_INVALID_REQUEST,
                    f"expected intervention.create, got {envelope.message_type}",
                )
            payload = InterventionRequest.model_validate(envelope.payload)
            created = await service.create(payload)
            resp = make_envelope(MessageType.INTERVENTION_CREATED, created)
            return JSONResponse(resp.model_dump(mode="json"))
        except StoreError as exc:
            env = _error_envelope(exc)
            return JSONResponse(env.model_dump(mode="json"), status_code=_http_status(exc.code))
        except Exception as exc:
            env = make_envelope(
                MessageType.ERROR,
                ErrorPayload(
                    code=ErrorCode.AG_INVALID_REQUEST,
                    message=str(exc),
                    retryable=False,
                ),
            )
            return JSONResponse(env.model_dump(mode="json"), status_code=400)

    @app.get("/v1/interventions/{intervention_id}")
    async def get_intervention(intervention_id: str, request: Request) -> JSONResponse:
        service: InterventionService = request.app.state.service
        try:
            updated = await service.get_updated(intervention_id)
            resp = make_envelope(MessageType.INTERVENTION_UPDATED, updated)
            return JSONResponse(resp.model_dump(mode="json"))
        except StoreError as exc:
            env = _error_envelope(exc)
            return JSONResponse(env.model_dump(mode="json"), status_code=_http_status(exc.code))

    @app.post("/v1/interventions/{intervention_id}/decision")
    async def post_decision(intervention_id: str, request: Request) -> JSONResponse:
        service: InterventionService = request.app.state.service
        body = await request.json()
        try:
            envelope = Envelope.model_validate(body)
            if envelope.message_type != MessageType.INTERVENTION_DECISION:
                raise StoreError(
                    ErrorCode.AG_INVALID_REQUEST,
                    "expected intervention.decision",
                )
            decision = InterventionDecision.model_validate(envelope.payload)
            if decision.intervention_id != intervention_id:
                raise StoreError(
                    ErrorCode.AG_INVALID_REQUEST,
                    "path intervention_id mismatch",
                )
            updated = await service.decide(decision)
            resp = make_envelope(MessageType.INTERVENTION_UPDATED, updated)
            return JSONResponse(resp.model_dump(mode="json"))
        except StoreError as exc:
            env = _error_envelope(exc)
            return JSONResponse(env.model_dump(mode="json"), status_code=_http_status(exc.code))
        except Exception as exc:
            env = make_envelope(
                MessageType.ERROR,
                ErrorPayload(
                    code=ErrorCode.AG_INVALID_REQUEST,
                    message=str(exc),
                    retryable=False,
                ),
            )
            return JSONResponse(env.model_dump(mode="json"), status_code=400)

    @app.post("/v1/interventions/{intervention_id}/cancel")
    async def post_cancel(intervention_id: str, request: Request) -> JSONResponse:
        service: InterventionService = request.app.state.service
        body = await request.json()
        try:
            envelope = Envelope.model_validate(body)
            if envelope.message_type != MessageType.INTERVENTION_CANCEL:
                raise StoreError(
                    ErrorCode.AG_INVALID_REQUEST,
                    "expected intervention.cancel",
                )
            cancel = InterventionCancel.model_validate(envelope.payload)
            if cancel.intervention_id != intervention_id:
                raise StoreError(
                    ErrorCode.AG_INVALID_REQUEST,
                    "path intervention_id mismatch",
                )
            updated = await service.cancel(cancel)
            resp = make_envelope(MessageType.INTERVENTION_UPDATED, updated)
            return JSONResponse(resp.model_dump(mode="json"))
        except StoreError as exc:
            env = _error_envelope(exc)
            return JSONResponse(env.model_dump(mode="json"), status_code=_http_status(exc.code))
        except Exception as exc:
            env = make_envelope(
                MessageType.ERROR,
                ErrorPayload(
                    code=ErrorCode.AG_INVALID_REQUEST,
                    message=str(exc),
                    retryable=False,
                ),
            )
            return JSONResponse(env.model_dump(mode="json"), status_code=400)

    @app.websocket("/v1/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await _bootstrap_state(
            websocket.app,
            db_path=websocket.app.state.db_path,
            enable_terminal_stdin=websocket.app.state.enable_terminal_stdin,
            config=getattr(websocket.app.state, "config", None),
        )
        await websocket.accept()
        hub: EventHub = websocket.app.state.hub
        intervention_id = websocket.query_params.get("intervention_id")
        queue = await hub.subscribe(intervention_id)
        try:
            # Push current snapshot first if id provided
            if intervention_id:
                service: InterventionService = websocket.app.state.service
                try:
                    current = await service.get_updated(intervention_id)
                    env0 = make_envelope(MessageType.INTERVENTION_UPDATED, current)
                    await websocket.send_json(env0.model_dump(mode="json"))
                    if current.status.value in {
                        "RESOLVED",
                        "TIMEOUT",
                        "CANCELLED",
                        "FAILED",
                    }:
                        return
                except StoreError:
                    pass
            while True:
                update = await queue.get()
                env = make_envelope(MessageType.INTERVENTION_UPDATED, update)
                await websocket.send_json(env.model_dump(mode="json"))
                if update.status.value in {
                    "RESOLVED",
                    "TIMEOUT",
                    "CANCELLED",
                    "FAILED",
                }:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await hub.unsubscribe(queue, intervention_id)

    _ = HTTPException
    return app


def build_default_app() -> FastAPI:
    return create_app()
