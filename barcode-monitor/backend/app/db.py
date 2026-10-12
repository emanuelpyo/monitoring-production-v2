"""PostgreSQL access layer for the Barcode Monitor.

The module owns the schema (idempotent ``CREATE TABLE IF NOT EXISTS`` bootstrap)
o a fresh database can boot without a separate migration tool, mirroring the
FactoryPulse convention. All statements use parameterised queries.

Timezone convention
-------------------
Timestamps are stored as ``TIMESTAMPTZ`` (UTC on disk). Aggregation is performed
in a fixed reporting timezone using ``date_trunc`` with an explicit timezone so
hourly buckets are stable and never shift around midnight.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS barcode_events (
    id            BIGSERIAL PRIMARY KEY,
    event_id      TEXT UNIQUE NOT NULL,
    barcode       TEXT NOT NULL,
    scanner_id    TEXT NOT NULL,
    event_type    TEXT NOT NULL DEFAULT 'barcode_activation',
    status        TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    ts            TIMESTAMPTZ NOT NULL,
    error_message TEXT,
    is_simulated  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_barcode_events_ts
    ON barcode_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_barcode_events_scanner_ts
    ON barcode_events (scanner_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_barcode_events_status_ts
    ON barcode_events (status, ts DESC);
CREATE INDEX IF NOT EXISTS idx_barcode_events_type_ts
    ON barcode_events (event_type, ts DESC);

CREATE TABLE IF NOT EXISTS scanner_status (
    scanner_id    TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'online' CHECK (state IN ('online', 'offline')),
    last_event_at TIMESTAMPTZ,
    events_count  BIGINT NOT NULL DEFAULT 0,
    error_count   BIGINT NOT NULL DEFAULT 0,
    is_simulated  BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS generator_control (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    interval_ms  INTEGER NOT NULL DEFAULT 2500,
    success_rate DOUBLE PRECISION NOT NULL DEFAULT 0.92,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class DatabaseUnavailable(RuntimeError):
    """Raised when PostgreSQL cannot be reached."""


def _connect() -> psycopg.Connection:
    try:
        return psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=5)
    except psycopg.OperationalError as exc:  # pragma: no cover - depends on env
        raise DatabaseUnavailable(str(exc)) from exc


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_connection() -> bool:
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


def init_schema() -> None:
    """Create tables/indexes if they do not yet exist."""
    with get_connection() as conn:
        conn.execute(SCHEMA_SQL)
        conn.execute(
            """
            INSERT INTO generator_control (id, enabled, interval_ms, success_rate)
            VALUES (1, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (settings.generator_enabled, settings.generator_interval_ms, settings.generator_success_rate),
        )


def ensure_scanners(scanner_ids: list[str]) -> None:
    if not scanner_ids:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO scanner_status (scanner_id, state, is_simulated)
                VALUES (%s, 'online', TRUE)
                ON CONFLICT (scanner_id) DO NOTHING
                """,
                [(sid,) for sid in scanner_ids],
            )


# --------------------------------------------------------------------------- #
# Event writes
# --------------------------------------------------------------------------- #
def insert_event(
    *,
    event_id: str,
    barcode: str,
    scanner_id: str,
    event_type: str,
    status: str,
    ts: datetime,
    error_message: str | None,
) -> bool:
    """Insert a single event. Returns False if it was a duplicate (event_id)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO barcode_events
                (event_id, barcode, scanner_id, event_type, status, ts, error_message, is_simulated)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING id
            """,
            (event_id, barcode, scanner_id, event_type, status, ts, error_message),
        ).fetchone()
    return row is not None


def insert_events_bulk(rows: list[dict[str, Any]]) -> int:
    """Insert many events, ignoring duplicates. Returns the number inserted."""
    if not rows:
        return 0
    inserted = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                result = cur.execute(
                    """
                    INSERT INTO barcode_events
                        (event_id, barcode, scanner_id, event_type, status, ts, error_message, is_simulated)
                    VALUES (%(event_id)s, %(barcode)s, %(scanner_id)s, %(event_type)s,
                            %(status)s, %(ts)s, %(error_message)s, TRUE)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING id
                    """,
                    row,
                ).fetchone()
                if result is not None:
                    inserted += 1
    return inserted


def bump_scanner(scanner_id: str, ts: datetime, *, failed: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO scanner_status (scanner_id, state, last_event_at, events_count, error_count, updated_at)
            VALUES (%s, 'online', %s, 1, %s, now())
            ON CONFLICT (scanner_id) DO UPDATE SET
                last_event_at = EXCLUDED.last_event_at,
                events_count = scanner_status.events_count + 1,
                error_count = scanner_status.error_count + EXCLUDED.error_count,
                updated_at = now()
            """,
            (scanner_id, ts, 1 if failed else 0),
        )


def set_scanner_state(scanner_id: str, state: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO scanner_status (scanner_id, state, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (scanner_id) DO UPDATE SET state = EXCLUDED.state, updated_at = now()
            """,
            (scanner_id, state),
        )


def recompute_scanner_aggregates() -> None:
    """Rebuild scanner_status counters from the events table (used after reseed)."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE scanner_status s SET
                events_count = COALESCE(a.total, 0),
                error_count  = COALESCE(a.failed, 0),
                last_event_at = a.last_event_at
            FROM (
                SELECT scanner_id,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                       MAX(ts) AS last_event_at
                FROM barcode_events
                GROUP BY scanner_id
            ) a
            WHERE s.scanner_id = a.scanner_id
            """
        )


# --------------------------------------------------------------------------- #
# Event reads
# --------------------------------------------------------------------------- #
def count_events() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM barcode_events").fetchone()
    return int(row["c"])


def latest_event_time() -> datetime | None:
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(ts) AS m FROM barcode_events").fetchone()
    return row["m"] if row else None


def query_events(
    *,
    range_start: datetime,
    range_end: datetime,
    scanner_id: str | None,
    status: str | None,
    event_type: str | None,
    sort: str,
    order: str,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    sort_column = {
        "timestamp": "ts",
        "event_id": "event_id",
        "scanner_id": "scanner_id",
        "status": "status",
    }.get(sort, "ts")
    sort_dir = "ASC" if order.lower() == "asc" else "DESC"

    where = ["ts >= %(range_start)s", "ts <= %(range_end)s"]
    params: dict[str, Any] = {"range_start": range_start, "range_end": range_end}
    if scanner_id:
        where.append("scanner_id = %(scanner_id)s")
        params["scanner_id"] = scanner_id
    if status:
        where.append("status = %(status)s")
        params["status"] = status
    if event_type:
        where.append("event_type = %(event_type)s")
        params["event_type"] = event_type
    where_sql = " AND ".join(where)

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    with get_connection() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM barcode_events WHERE {where_sql}",  # noqa: S608 (static clause)
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT id, event_id, barcode, scanner_id, event_type, status, ts AS timestamp, error_message, is_simulated
            FROM barcode_events
            WHERE {where_sql}
            ORDER BY {sort_column} {sort_dir}, event_id {sort_dir}
            LIMIT %(limit)s OFFSET %(offset)s
            """,  # noqa: S608 (sort_column is whitelisted)
            params,
        ).fetchall()
    return list(rows), int(total_row["c"])


def summary_counts(range_start: datetime, range_end: datetime) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'success') AS successful,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                MAX(ts) AS latest
            FROM barcode_events
            WHERE ts >= %s AND ts <= %s
            """,
            (range_start, range_end),
        ).fetchone()
        scanners = conn.execute(
            """
            SELECT
                COUNT(*) AS total_scanners,
                COUNT(*) FILTER (WHERE state = 'online') AS online_scanners
            FROM scanner_status
            """
        ).fetchone()
    total = int(row["total"] or 0)
    successful = int(row["successful"] or 0)
    failed = int(row["failed"] or 0)
    success_rate = round((successful / total) * 100, 2) if total else 0.0
    return {
        "total": total,
        "successful": successful,
        "failed": failed,
        "success_rate": success_rate,
        "latest": row["latest"],
        "total_scanners": int(scanners["total_scanners"] or 0),
        "online_scanners": int(scanners["online_scanners"] or 0),
    }


def hourly_counts(range_start: datetime, range_end: datetime, tz_name: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                date_trunc('hour', ts AT TIME ZONE %(tz)s) AS bucket,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'success') AS successful,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM barcode_events
            WHERE ts >= %(range_start)s AND ts <= %(range_end)s
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            {"tz": tz_name, "range_start": range_start, "range_end": range_end},
        ).fetchall()
    return list(rows)


def scanner_activity(range_start: datetime, range_end: datetime) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                scanner_id,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'success') AS successful,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM barcode_events
            WHERE ts >= %s AND ts <= %s
            GROUP BY scanner_id
            ORDER BY scanner_id ASC
            """,
            (range_start, range_end),
        ).fetchall()
    return list(rows)


def list_scanners() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT scanner_id, state, last_event_at, events_count, error_count, is_simulated
            FROM scanner_status
            ORDER BY scanner_id ASC
            """
        ).fetchall()
    return list(rows)


# --------------------------------------------------------------------------- #
# Generator control state
# --------------------------------------------------------------------------- #
def get_generator_config() -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT enabled, interval_ms, success_rate FROM generator_control WHERE id = 1"
        ).fetchone()
    if not row:
        return {
            "enabled": settings.generator_enabled,
            "interval_ms": settings.generator_interval_ms,
            "success_rate": settings.generator_success_rate,
        }
    return row


def update_generator_config(
    *,
    enabled: bool | None = None,
    interval_ms: int | None = None,
    success_rate: float | None = None,
) -> dict[str, Any]:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE generator_control SET
                enabled      = COALESCE(%(enabled)s, enabled),
                interval_ms  = COALESCE(%(interval_ms)s, interval_ms),
                success_rate = COALESCE(%(success_rate)s, success_rate),
                updated_at   = now()
            WHERE id = 1
            """,
            {"enabled": enabled, "interval_ms": interval_ms, "success_rate": success_rate},
        )
    return get_generator_config()


# --------------------------------------------------------------------------- #
# Reset (scoped to this application's simulated dataset only)
# --------------------------------------------------------------------------- #
def reset_simulated_data(scanner_ids: list[str]) -> None:
    """Truncate only the simulated tables owned by this application."""
    with get_connection() as conn:
        conn.execute("TRUNCATE barcode_events RESTART IDENTITY")
        conn.execute("TRUNCATE scanner_status")
        conn.execute(
            "UPDATE generator_control SET enabled = %s, updated_at = now() WHERE id = 1",
            (settings.generator_enabled,),
        )
    ensure_scanners(scanner_ids)


def next_event_sequence() -> int:
    """Return the next numeric sequence for generating an event id (best-effort)."""
    with get_connection() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM barcode_events").fetchone()
    return int(row["m"]) + 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_range(hours: float = 24.0) -> tuple[datetime, datetime]:
    end = utcnow()
    start = end - timedelta(hours=hours)
    return start, end
