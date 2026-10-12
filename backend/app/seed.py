"""Deterministic 24-hour synthetic history seeding.

The seed runs only when the ``barcode_events`` table is empty. It uses a fixed
random seed so repeated runs on a fresh database produce the same dataset,
which keeps tests and demos reproducible.

Activity model
--------------
* Events are distributed across ``SEED_HOURS`` hours using a day/night volume
  curve so some hours are busy and others are quiet.
* Each of ``SCANNER_COUNT`` scanners has a slightly different baseline volume.
* A small number of scanner offline windows are simulated; events are not
  generated inside those windows.
* Failures follow a per-hour probability and carry a plausible error message.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db
from .config import settings

FAILURE_MESSAGES = [
    "Barcode unreadable",
    "Scanner timeout",
    "No route to host",
    "Barcode checksum mismatch",
    "Scanner buffer overflow",
    "Activation rejected by host",
]

# Relative volume per hour-of-day (index 0..23). Quiet at night, busy in shifts.
HOUR_VOLUME = [
    0.20, 0.15, 0.12, 0.12, 0.15, 0.30,  # 00-05
    0.60, 0.90, 1.00, 1.00, 0.95, 0.80,  # 06-11
    0.55, 0.70, 0.95, 1.00, 0.95, 0.85,  # 12-17
    0.65, 0.55, 0.45, 0.35, 0.30, 0.25,  # 18-23
]


def scanner_ids() -> list[str]:
    return [f"SCANNER-{i:02d}" for i in range(1, settings.scanner_count + 1)]


def _event_id(seq: int) -> str:
    return f"EVT-{seq:06d}"


def _barcode(seq: int) -> str:
    return f"TEST-{seq:06d}"


def build_seed_rows(
    *,
    seed: int = 20260112,
    hours: int | None = None,
    events_per_hour: int | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build the seed dataset in memory (does not touch the database)."""
    rng = random.Random(seed)
    hours = hours if hours is not None else settings.seed_hours
    events_per_hour = events_per_hour if events_per_hour is not None else settings.seed_events_per_hour
    scanners = scanner_ids()
    end = end or db.utcnow()
    start = end - timedelta(hours=hours)

    # Per-scanner volume multiplier so scanners differ from each other.
    scanner_mult = {sid: rng.uniform(0.7, 1.3) for sid in scanners}

    # A couple of simulated offline windows (scanner_id, start_offset_hours, duration_minutes).
    offline_windows: list[tuple[str, float, float]] = []
    for sid in rng.sample(scanners, k=min(2, len(scanners))):
        offline_windows.append((sid, rng.uniform(2, hours - 3), rng.uniform(20, 55)))

    def is_offline(sid: str, when: datetime) -> bool:
        offset_hours = (when - start).total_seconds() / 3600
        for window_sid, w_start, w_dur in offline_windows:
            if window_sid == sid and w_start <= offset_hours <= w_start + (w_dur / 60):
                return True
        return False

    rows: list[dict[str, Any]] = []
    seq = 1
    for hour_index in range(hours + 1):
        hour_offset = (hours + 1) - hour_index  # start oldest -> newest
        hour_start = start + timedelta(hours=hour_index)
        hour_volume = HOUR_VOLUME[hour_start.hour]
        base_count = int(events_per_hour * hour_volume)
        if base_count <= 0:
            continue
        for _ in range(base_count):
            sid = rng.choices(scanners, weights=[scanner_mult[s] for s in scanners], k=1)[0]
            ts = hour_start + timedelta(seconds=rng.uniform(0, 3600))
            if ts > end:
                ts = end
            if is_offline(sid, ts):
                continue
            failed = rng.random() < (1.0 - settings.generator_success_rate)
            rows.append(
                {
                    "event_id": _event_id(seq),
                    "barcode": _barcode(seq),
                    "scanner_id": sid,
                    "event_type": "barcode_activation",
                    "status": "failed" if failed else "success",
                    "ts": ts,
                    "error_message": rng.choice(FAILURE_MESSAGES) if failed else None,
                }
            )
            seq += 1
    return rows


def seed_if_empty() -> int:
    """Seed the database only if it currently holds no events. Returns rows inserted."""
    if db.count_events() > 0:
        return 0
    scanners = scanner_ids()
    db.ensure_scanners(scanners)
    rows = build_seed_rows()
    inserted = db.insert_events_bulk(rows)
    db.recompute_scanner_aggregates()
    return inserted


def reseed() -> int:
    """Explicit reset path: clear simulated data and seed a fresh dataset."""
    scanners = scanner_ids()
    db.reset_simulated_data(scanners)
    rows = build_seed_rows()
    inserted = db.insert_events_bulk(rows)
    db.recompute_scanner_aggregates()
    return inserted
