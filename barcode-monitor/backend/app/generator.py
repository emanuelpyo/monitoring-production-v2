"""Backend-driven asynchronous synthetic event generator.

The generator runs as a single asyncio task started from the FastAPI startup
hook. It reads its configuration from PostgreSQL (``generator_control``) on each
tick so pause/resume and interval changes take effect without restarting.

Design notes
------------
* Exactly one task is created; ``start()`` is idempotent and guards against
  duplicate tasks during uvicorn ``--reload``.
* The generator never blocks on slow WebSocket clients: broadcasts are
  dispatched through a non-blocking callback owned by the connection manager.
* Failures are caught, logged to ``last_error`` and surfaced through the API,
  never crashing the process.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Awaitable, Callable

from . import db, seed
from .config import settings

logger = logging.getLogger("barcode_monitor.generator")

BroadcastFn = Callable[[dict], Awaitable[None]]

FAILURE_MESSAGES = [
    "Barcode unreadable",
    "Scanner timeout",
    "No route to host",
    "Barcode checksum mismatch",
    "Scanner buffer overflow",
    "Activation rejected by host",
]


def _iso_utc(value) -> str | None:
    return value.isoformat() if value else None


def _iso_local(value) -> str:
    """Serialize a datetime in the configured display timezone."""
    try:
        from zoneinfo import ZoneInfo

        return value.astimezone(ZoneInfo(settings.display_tz)).isoformat()
    except Exception:  # noqa: BLE001 - fall back to raw iso
        return value.isoformat()


class EventGenerator:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_generated_at: datetime | None = None
        self._last_error: str | None = None
        self._sequence = 0
        # Non-blocking broadcast callback is registered by the app (connection manager).
        self._broadcast: BroadcastFn | None = None

    # -- lifecycle ----------------------------------------------------------
    def bind_broadcast(self, fn: BroadcastFn) -> None:
        self._broadcast = fn

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._task and not self._task.done():
            return  # already running, do not spawn duplicates
        self._sequence = db.next_event_sequence() - 1
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Event generator task started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        logger.info("Event generator task stopped")

    # -- main loop ----------------------------------------------------------
    async def _loop(self) -> None:
        try:
            while self._running:
                try:
                    config = db.get_generator_config()
                except db.DatabaseUnavailable as exc:
                    self._last_error = str(exc)
                    await asyncio.sleep(2)
                    continue

                if not config["enabled"]:
                    await asyncio.sleep(0.5)
                    continue

                try:
                    await self._emit_cycle(config)
                except Exception as exc:  # noqa: BLE001 - never let the task die
                    self._last_error = str(exc)
                    logger.warning("Generator cycle failed: %s", exc)

                await asyncio.sleep(max(0.2, config["interval_ms"] / 1000))
        except asyncio.CancelledError:
            logger.info("Event generator loop cancelled")
            raise

    async def _emit_cycle(self, config: dict) -> None:
        scanners = seed.scanner_ids()
        if not scanners:
            return

        # Occasionally flip a scanner offline for a short window, then back online.
        if random.random() < 0.02:
            sid = random.choice(scanners)
            await asyncio.to_thread(db.set_scanner_state, sid, "offline")
            await self._push({"type": "scanner.updated", "payload": {"scanner_id": sid, "state": "offline"}})
            asyncio.create_task(self._reconnect_later(sid))

        online = [s for s in db.list_scanners() if s["state"] == "online"]
        target_scanners = [s["scanner_id"] for s in online] or scanners
        scanner_id = random.choices(
            target_scanners,
            weights=[random.uniform(0.6, 1.4) for _ in target_scanners],
            k=1,
        )[0]

        self._sequence += 1
        event_id = f"EVT-{self._sequence:06d}"
        barcode = f"TEST-{self._sequence:06d}"
        failed = random.random() > float(config["success_rate"])
        ts = datetime.now(timezone.utc)

        event = {
            "event_id": event_id,
            "barcode": barcode,
            "scanner_id": scanner_id,
            "event_type": "barcode_activation",
            "status": "failed" if failed else "success",
            "ts": ts,
            "error_message": random.choice(FAILURE_MESSAGES) if failed else None,
        }

        inserted = await asyncio.to_thread(
            db.insert_event,
            event_id=event["event_id"],
            barcode=event["barcode"],
            scanner_id=event["scanner_id"],
            event_type=event["event_type"],
            status=event["status"],
            ts=event["ts"],
            error_message=event["error_message"],
        )
        if not inserted:
            return  # duplicate event id, skip

        await asyncio.to_thread(db.bump_scanner, scanner_id, ts, failed=failed)
        self._last_generated_at = ts
        self._last_error = None

        payload = {
            "id": self._sequence,
            "event_id": event["event_id"],
            "barcode": event["barcode"],
            "scanner_id": event["scanner_id"],
            "event_type": event["event_type"],
            "status": event["status"],
            "timestamp": _iso_local(ts),
            "error_message": event["error_message"],
            "is_simulated": True,
        }
        await self._push({"type": "event.created", "payload": payload})

        summary = await asyncio.to_thread(self._summary_payload)
        await self._push({"type": "metrics.updated", "payload": summary})

    async def _reconnect_later(self, scanner_id: str) -> None:
        await asyncio.sleep(random.uniform(15, 45))
        try:
            await asyncio.to_thread(db.set_scanner_state, scanner_id, "online")
            await self._push({"type": "scanner.updated", "payload": {"scanner_id": scanner_id, "state": "online"}})
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)

    # -- helpers ------------------------------------------------------------
    def _summary_payload(self) -> dict:
        start, end = db.default_range(24.0)
        counts = db.summary_counts(start, end)
        return {
            "total_activations": counts["total"],
            "successful_activations": counts["successful"],
            "failed_activations": counts["failed"],
            "success_rate": counts["success_rate"],
            "active_scanners": counts["online_scanners"],
            "total_scanners": counts["total_scanners"],
            "latest_event_at": _iso_utc(counts["latest"]),
        }

    async def _push(self, message: dict) -> None:
        if self._broadcast is None:
            return
        try:
            await self._broadcast(message)
        except Exception as exc:  # noqa: BLE001 - a bad client must not kill the loop
            logger.warning("Broadcast failed: %s", exc)

    # -- status -------------------------------------------------------------
    def status(self) -> dict:
        try:
            config = db.get_generator_config()
        except db.DatabaseUnavailable:
            config = {
                "enabled": settings.generator_enabled,
                "interval_ms": settings.generator_interval_ms,
                "success_rate": settings.generator_success_rate,
            }
        return {
            "running": self._running and bool(config["enabled"]),
            "enabled": bool(config["enabled"]),
            "interval_ms": int(config["interval_ms"]),
            "success_rate": float(config["success_rate"]),
            "scanner_count": settings.scanner_count,
            "last_generated_at": self._last_generated_at.isoformat() if self._last_generated_at else None,
            "last_error": self._last_error,
            "is_simulated": True,
        }


generator = EventGenerator()
