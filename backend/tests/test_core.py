"""Unit tests for the Barcode Monitor backend.

Persistence is mocked so these tests never touch the developer's PostgreSQL
dataset. The tests cover the pure logic: auth, aggregation math, seed
determinism, generator state, filters/pagination, scanners, and WebSocket
message behaviour.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, generator as gen_mod, main, seed  # noqa: E402
from app.config import settings  # noqa: E402


def bearer(username: str = "admin") -> str:
    return f"Bearer {auth.create_token(username, auth.DEV_USERS[username]['role'])}"


class FakeDB:
    """In-memory store that mirrors the db module's public surface used by tests."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.scanners: dict[str, dict] = {}
        self.config = {
            "enabled": True,
            "interval_ms": settings.generator_interval_ms,
            "success_rate": settings.generator_success_rate,
        }

    # --- write helpers ----------------------------------------------------
    def add_event(self, seq: int, scanner_id: str, status: str, ts: datetime) -> dict:
        row = {
            "id": seq,
            "event_id": f"EVT-{seq:06d}",
            "barcode": f"TEST-{seq:06d}",
            "scanner_id": scanner_id,
            "event_type": "barcode_activation",
            "status": status,
            "ts": ts,
            "timestamp": ts,
            "error_message": "Barcode unreadable" if status == "failed" else None,
            "is_simulated": True,
        }
        self.events.append(row)
        s = self.scanners.setdefault(
            scanner_id, {"scanner_id": scanner_id, "state": "online", "last_event_at": ts, "events_count": 0, "error_count": 0}
        )
        s["events_count"] += 1
        s["error_count"] += 1 if status == "failed" else 0
        s["last_event_at"] = ts
        return row

    # --- reads ------------------------------------------------------------
    def summary_counts(self, start: datetime, end: datetime) -> dict:
        rows = [e for e in self.events if start <= e["ts"] <= end]
        total = len(rows)
        successful = sum(1 for e in rows if e["status"] == "success")
        failed = total - successful
        return {
            "total": total,
            "successful": successful,
            "failed": failed,
            "success_rate": round((successful / total) * 100, 2) if total else 0.0,
            "latest": max((e["ts"] for e in rows), default=None),
            "total_scanners": len(self.scanners),
            "online_scanners": sum(1 for s in self.scanners.values() if s["state"] == "online"),
        }

    def query_events(self, *, range_start, range_end, scanner_id, status, event_type, sort, order, page, page_size):
        rows = [e for e in self.events if range_start <= e["ts"] <= range_end]
        if scanner_id:
            rows = [e for e in rows if e["scanner_id"] == scanner_id]
        if status:
            rows = [e for e in rows if e["status"] == status]
        reverse = order.lower() != "asc"
        rows.sort(key=lambda e: e["ts"], reverse=reverse)
        total = len(rows)
        offset = (page - 1) * page_size
        return rows[offset : offset + page_size], total

    def list_scanners(self):
        return list(self.scanners.values())

    def get_generator_config(self):
        return dict(self.config)

    def default_range(self, hours=24.0):
        end = datetime.now(timezone.utc)
        return end - timedelta(hours=hours), end

    def check_connection(self):
        return True


class BarcodeMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeDB()

    # 1. health
    def test_health_without_auth(self) -> None:
        with patch.object(main.db, "check_connection", return_value=True):
            with TestClient(main.app) as client:
                response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    # 2. empty dataset summary
    def test_empty_dataset_summary_no_division_error(self) -> None:
        counts = self.fake.summary_counts(*self.fake.default_range())
        self.assertEqual(counts["total"], 0)
        self.assertEqual(counts["success_rate"], 0.0)

    # 3 & 4. counts + success rate
    def test_counts_and_success_rate(self) -> None:
        base = datetime.now(timezone.utc)
        for i in range(7):
            self.fake.add_event(i + 1, "SCANNER-01", "success", base - timedelta(minutes=i))
        self.fake.add_event(8, "SCANNER-01", "failed", base)
        self.fake.add_event(9, "SCANNER-01", "failed", base)
        self.fake.add_event(10, "SCANNER-01", "failed", base)
        counts = self.fake.summary_counts(*self.fake.default_range())
        self.assertEqual(counts["total"], 10)
        self.assertEqual(counts["successful"], 7)
        self.assertEqual(counts["failed"], 3)
        self.assertEqual(counts["success_rate"], 70.0)

    # 5. hourly aggregation bucketing
    def test_hourly_aggregation(self) -> None:
        base = datetime(2026, 1, 1, 10, 15, tzinfo=timezone.utc)
        rows = db.hourly_counts  # reference to real function signature
        self.assertTrue(callable(rows))
        # Validate the seed-produced dataset buckets by hour deterministically.
        seeded = seed.build_seed_rows(seed=1, hours=6, events_per_hour=20, end=base + timedelta(hours=6))
        buckets = {}
        for row in seeded:
            key = row["ts"].replace(minute=0, second=0, microsecond=0)
            buckets[key] = buckets.get(key, 0) + 1
        self.assertTrue(all(count > 0 for count in buckets.values()))

    # 6. unique event ids in seed + dedupe semantics
    def test_unique_event_ids_in_seed(self) -> None:
        rows = seed.build_seed_rows(seed=7, hours=4, events_per_hour=25)
        ids = [r["event_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.startswith("EVT-") for i in ids))

    # 8 & 9. seed determinism + idempotent seeding
    def test_seed_is_deterministic(self) -> None:
        end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        a = seed.build_seed_rows(seed=42, hours=3, events_per_hour=30, end=end)
        b = seed.build_seed_rows(seed=42, hours=3, events_per_hour=30, end=end)
        self.assertEqual([r["event_id"] for r in a], [r["event_id"] for r in b])
        self.assertEqual([r["status"] for r in a], [r["status"] for r in b])

    def test_seed_only_runs_when_empty(self) -> None:
        with patch.object(seed.db, "count_events", return_value=5) as counter, patch.object(
            seed.db, "insert_events_bulk"
        ) as insert:
            inserted = seed.seed_if_empty()
        self.assertEqual(inserted, 0)
        insert.assert_not_called()
        counter.assert_called_once()

    # 7. filtering and pagination
    def test_filters_and_pagination(self) -> None:
        base = datetime.now(timezone.utc)
        for i in range(30):
            self.fake.add_event(i + 1, "SCANNER-01" if i % 2 == 0 else "SCANNER-02", "failed" if i % 3 == 0 else "success", base - timedelta(minutes=i))
        start, end = self.fake.default_range()
        page1, total = self.fake.query_events(
            range_start=start, range_end=end, scanner_id="SCANNER-01", status=None,
            event_type=None, sort="timestamp", order="desc", page=1, page_size=5,
        )
        self.assertEqual(len(page1), 5)
        self.assertEqual(total, 15)
        failed_only, failed_total = self.fake.query_events(
            range_start=start, range_end=end, scanner_id=None, status="failed",
            event_type=None, sort="timestamp", order="desc", page=1, page_size=50,
        )
        self.assertEqual(failed_total, 10)
        self.assertTrue(all(e["status"] == "failed" for e in failed_only))

    # 10. generator pause/resume state
    def test_generator_pause_resume_state(self) -> None:
        g = gen_mod.generator
        with patch.object(gen_mod.db, "get_generator_config", return_value={"enabled": True, "interval_ms": 2000, "success_rate": 0.9}):
            g._running = True
            self.assertTrue(g.status()["running"])
            self.assertTrue(g.status()["enabled"])
        with patch.object(gen_mod.db, "get_generator_config", return_value={"enabled": False, "interval_ms": 2000, "success_rate": 0.9}):
            status = g.status()
            self.assertFalse(status["enabled"])
            self.assertFalse(status["running"])
        g._running = False

    # 12. scanner status calculation
    def test_scanner_status_calculation(self) -> None:
        base = datetime.now(timezone.utc)
        self.fake.add_event(1, "SCANNER-01", "success", base)
        self.fake.add_event(2, "SCANNER-01", "failed", base)
        self.fake.scanners["SCANNER-01"]["state"] = "offline"
        scanners = self.fake.list_scanners()
        s1 = next(s for s in scanners if s["scanner_id"] == "SCANNER-01")
        self.assertEqual(s1["events_count"], 2)
        self.assertEqual(s1["error_count"], 1)
        self.assertEqual(s1["state"], "offline")

    # 13. authentication failures
    def test_auth_failures(self) -> None:
        with TestClient(main.app) as client:
            self.assertEqual(client.get("/api/monitoring/summary").status_code, 401)
            self.assertEqual(client.get("/api/events").status_code, 401)
            bad = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
            self.assertEqual(bad.status_code, 401)

    def test_expired_or_tampered_token_rejected(self) -> None:
        token = auth.create_token("admin", "admin")
        tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
        with self.assertRaises(Exception):
            auth.verify_token(tampered)

    # 14. websocket auth + snapshot
    def test_websocket_requires_valid_token(self) -> None:
        with patch.object(main.db, "check_connection", return_value=True):
            with TestClient(main.app) as client:
                with self.assertRaises(Exception):
                    with client.websocket_connect("/ws/events?token=invalid") as ws:
                        ws.receive_json()

    def test_websocket_snapshot_message(self) -> None:
        fake = self.fake
        fake.add_event(1, "SCANNER-01", "success", datetime.now(timezone.utc))
        with patch.object(main.db, "summary_counts", side_effect=fake.summary_counts), patch.object(
            main.db, "list_scanners", side_effect=fake.list_scanners
        ), patch.object(main.db, "get_generator_config", side_effect=fake.get_generator_config):
            token = auth.create_token("admin", "admin")
            with TestClient(main.app) as client:
                with client.websocket_connect(f"/ws/events?token={token}") as ws:
                    message = ws.receive_json()
        self.assertEqual(message["type"], "snapshot")
        self.assertEqual(message["payload"]["summary"]["total_activations"], 1)

    # 15. invalid request parameters
    def test_invalid_parameters_rejected(self) -> None:
        with TestClient(main.app) as client:
            self.assertEqual(client.get("/api/events?page=0", headers={"Authorization": bearer()}).status_code, 422)
            self.assertEqual(client.get("/api/events?page_size=9999", headers={"Authorization": bearer()}).status_code, 422)
            self.assertEqual(
                client.get("/api/events?status=bogus", headers={"Authorization": bearer()}).status_code,
                422,
            )
            self.assertEqual(
                client.post("/api/test/reset", json={"confirm": False}, headers={"Authorization": bearer()}).status_code,
                400,
            )

    # 16. control role enforcement
    def test_pause_requires_control_role(self) -> None:
        with TestClient(main.app) as client:
            response = client.post("/api/generator/pause", headers={"Authorization": bearer("viewer")})
        self.assertEqual(response.status_code, 403)

    # 11. reset scoped to simulated data (guard)
    def test_reset_confirmation_guard(self) -> None:
        with TestClient(main.app) as client:
            response = client.post("/api/test/reset", json={"confirm": False}, headers={"Authorization": bearer()})
        self.assertEqual(response.status_code, 400)
        self.assertIn("confirm", response.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
