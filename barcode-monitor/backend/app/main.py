"""Barcode Monitor FastAPI application.

Wires together the PostgreSQL data layer, the synthetic event generator, the
REST API, and the authenticated WebSocket for live updates.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import db, seed
from .auth import (
    authenticate_user,
    create_token,
    current_user,
    require_control_role,
    verify_token,
)
from .config import settings
from .generator import generator
from .models import (
    EventListResponse,
    GeneratorConfigRequest,
    GeneratorStatus,
    HourlyBucket,
    HourlyResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    MonitoringStatus,
    MonitoringSummary,
    ResetRequest,
    ScannerActivity,
    ScannerStatus,
    UserInfo,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("barcode_monitor")


# --------------------------------------------------------------------------- #
# WebSocket connection manager
# --------------------------------------------------------------------------- #
class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    @property
    def count(self) -> int:
        return len(self._connections)

    async def broadcast(self, message: dict) -> None:
        # Snapshot the set so concurrent disconnects don't mutate during iteration.
        async with self._lock:
            targets = list(self._connections)
        dead: list[WebSocket] = []
        for socket in targets:
            try:
                await asyncio.wait_for(socket.send_json(message), timeout=2.0)
            except Exception:  # noqa: BLE001 - slow/broken client must not block others
                dead.append(socket)
        for socket in dead:
            await self.disconnect(socket)


manager = ConnectionManager()


def _display_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.display_tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _parse_range(range_hours: float) -> tuple[datetime, datetime]:
    # Return tz-aware datetimes in the configured display timezone so all
    # serialized timestamps use a single, consistent offset.
    tz = _display_tz()
    end = datetime.now(tz)
    start = end - timedelta(hours=max(0.1, min(range_hours, 24 * 30)))
    return start, end


def _iso(value: datetime | None) -> str | None:
    """Serialize a datetime in the display timezone, or None."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_display_tz()).isoformat()


def _event_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "barcode": row["barcode"],
        "scanner_id": row["scanner_id"],
        "event_type": row["event_type"],
        "status": row["status"],
        "timestamp": _iso(row["timestamp"]) if isinstance(row["timestamp"], datetime) else row["timestamp"],
        "error_message": row.get("error_message"),
        "is_simulated": True,
    }


def _scanner_to_dict(row: dict) -> dict:
    return {
        "scanner_id": row["scanner_id"],
        "state": row["state"],
        "last_event_at": _iso(row["last_event_at"]) if row["last_event_at"] else None,
        "events_count": int(row["events_count"]),
        "error_count": int(row["error_count"]),
        "is_simulated": True,
    }


# --------------------------------------------------------------------------- #
# Application lifecycle
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise schema, scanners, and seed data. Retry if the DB is still starting.
    ready = False
    for attempt in range(1, 16):
        try:
            db.init_schema()
            db.ensure_scanners(seed.scanner_ids())
            inserted = seed.seed_if_empty()
            if inserted:
                logger.info("Seeded %s synthetic events", inserted)
            ready = True
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Database not ready (attempt %s/15): %s", attempt, exc)
            await asyncio.sleep(1)
    if not ready:
        logger.error("Starting without a reachable database; API will report degraded.")

    generator.bind_broadcast(manager.broadcast)
    if ready and settings.generator_enabled:
        await generator.start()

    try:
        yield
    finally:
        await generator.stop()


app = FastAPI(title="Barcode Monitor API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# --------------------------------------------------------------------------- #
# Health / status
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict:
    db_ok = db.check_connection()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "down",
        "generator_running": generator.running,
        "websocket_clients": manager.count,
        "is_simulated": True,
    }


@app.get("/api/monitoring/status", response_model=MonitoringStatus)
def monitoring_status() -> MonitoringStatus:
    db_ok = db.check_connection()
    gen = GeneratorStatus(**generator.status())
    return MonitoringStatus(
        status="ok" if db_ok else "down",
        database="ok" if db_ok else "down",
        generator=gen,
        message=None if db_ok else "PostgreSQL is not reachable",
    )


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@app.post("/api/auth/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    user = authenticate_user(request.username, request.password)
    token = create_token(user["username"], user["role"])
    return LoginResponse(
        access_token=token,
        expires_in=settings.token_ttl_seconds,
        user=UserInfo(username=user["username"], display_name=user["display_name"]),
    )


@app.get("/api/auth/me", response_model=UserInfo)
def me(authorization: str | None = Header(default=None)) -> UserInfo:
    user = current_user(authorization)
    return UserInfo(username=user["username"], display_name=user["display_name"])


# --------------------------------------------------------------------------- #
# Monitoring metrics
# --------------------------------------------------------------------------- #
@app.get("/api/monitoring/summary", response_model=MonitoringSummary)
def monitoring_summary(
    range_hours: float = Query(default=24.0, ge=0.1, le=720.0),
    authorization: str | None = Header(default=None),
) -> MonitoringSummary:
    current_user(authorization)
    start, end = _parse_range(range_hours)
    counts = db.summary_counts(start, end)
    return MonitoringSummary(
        total_activations=counts["total"],
        successful_activations=counts["successful"],
        failed_activations=counts["failed"],
        success_rate=counts["success_rate"],
        active_scanners=counts["online_scanners"],
        total_scanners=counts["total_scanners"],
        latest_event_at=counts["latest"],
        range_start=start,
        range_end=end,
        range_hours=range_hours,
    )


@app.get("/api/monitoring/hourly", response_model=HourlyResponse)
def monitoring_hourly(
    range_hours: float = Query(default=24.0, ge=1.0, le=720.0),
    authorization: str | None = Header(default=None),
) -> HourlyResponse:
    current_user(authorization)
    start, end = _parse_range(range_hours)
    rows = db.hourly_counts(start, end, settings.display_tz)
    buckets = [
        HourlyBucket(
            bucket=row["bucket"],
            total=int(row["total"]),
            successful=int(row["successful"]),
            failed=int(row["failed"]),
        )
        for row in rows
    ]
    return HourlyResponse(range_start=start, range_end=end, buckets=buckets, timezone=settings.display_tz)


@app.get("/api/monitoring/scanner-activity", response_model=list[ScannerActivity])
def scanner_activity(
    range_hours: float = Query(default=24.0, ge=0.1, le=720.0),
    authorization: str | None = Header(default=None),
) -> list[ScannerActivity]:
    current_user(authorization)
    start, end = _parse_range(range_hours)
    rows = db.scanner_activity(start, end)
    return [
        ScannerActivity(
            scanner_id=row["scanner_id"],
            total=int(row["total"]),
            successful=int(row["successful"]),
            failed=int(row["failed"]),
        )
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
@app.get("/api/events", response_model=EventListResponse)
def list_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    scanner_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(success|failed)$"),
    event_type: str | None = Query(default=None, max_length=64),
    range_hours: float = Query(default=24.0, ge=0.1, le=720.0),
    sort: str = Query(default="timestamp", pattern="^(timestamp|event_id|scanner_id|status)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    authorization: str | None = Header(default=None),
) -> EventListResponse:
    current_user(authorization)
    start, end = _parse_range(range_hours)
    rows, total = db.query_events(
        range_start=start,
        range_end=end,
        scanner_id=scanner_id,
        status=status,
        event_type=event_type,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return EventListResponse(
        items=[_event_to_dict(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


# --------------------------------------------------------------------------- #
# Scanners
# --------------------------------------------------------------------------- #
@app.get("/api/scanners", response_model=list[ScannerStatus])
def list_scanners(authorization: str | None = Header(default=None)) -> list[ScannerStatus]:
    current_user(authorization)
    return [ScannerStatus(**_scanner_to_dict(row)) for row in db.list_scanners()]


# --------------------------------------------------------------------------- #
# Generator controls
# --------------------------------------------------------------------------- #
@app.post("/api/generator/pause", response_model=GeneratorStatus)
async def pause_generator(authorization: str | None = Header(default=None)) -> GeneratorStatus:
    user = current_user(authorization)
    require_control_role(user)
    await asyncio.to_thread(db.update_generator_config, enabled=False)
    status = generator.status()
    await manager.broadcast({"type": "generator.status", "payload": status})
    return GeneratorStatus(**status)


@app.post("/api/generator/resume", response_model=GeneratorStatus)
async def resume_generator(authorization: str | None = Header(default=None)) -> GeneratorStatus:
    user = current_user(authorization)
    require_control_role(user)
    await asyncio.to_thread(db.update_generator_config, enabled=True)
    status = generator.status()
    await manager.broadcast({"type": "generator.status", "payload": status})
    return GeneratorStatus(**status)


@app.post("/api/generator/config", response_model=GeneratorStatus)
async def configure_generator(
    request: GeneratorConfigRequest,
    authorization: str | None = Header(default=None),
) -> GeneratorStatus:
    user = current_user(authorization)
    require_control_role(user)
    await asyncio.to_thread(
        db.update_generator_config,
        enabled=None,
        interval_ms=request.interval_ms,
        success_rate=request.success_rate,
    )
    status = generator.status()
    await manager.broadcast({"type": "generator.status", "payload": status})
    return GeneratorStatus(**status)


@app.post("/api/test/reset", response_model=MessageResponse)
async def reset_data(request: ResetRequest, authorization: str | None = Header(default=None)) -> MessageResponse:
    user = current_user(authorization)
    require_control_role(user)
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Reset requires confirm=true")
    inserted = await asyncio.to_thread(seed.reseed)
    await manager.broadcast(
        {
            "type": "generator.status",
            "payload": {**generator.status(), "reset": True, "reseeded": inserted},
        }
    )
    return MessageResponse(ok=True, detail=f"Reset complete; reseeded {inserted} synthetic events")


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@app.websocket("/ws/events")
async def events_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
    try:
        verify_token(token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)
    try:
        # Send an initial snapshot: summary + scanners + generator state.
        try:
            start, end = db.default_range(24.0)
            counts = db.summary_counts(start, end)
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "payload": {
                        "summary": {
                            "total_activations": counts["total"],
                            "successful_activations": counts["successful"],
                            "failed_activations": counts["failed"],
                            "success_rate": counts["success_rate"],
                            "active_scanners": counts["online_scanners"],
                            "total_scanners": counts["total_scanners"],
                            "latest_event_at": _iso(counts["latest"]),
                        },
                        "scanners": [_scanner_to_dict(row) for row in db.list_scanners()],
                        "generator": generator.status(),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            await websocket.send_json({"type": "system.error", "payload": {"message": str(exc)}})

        while True:
            # Keep the socket open; we don't expect client messages.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)
