"""Smoke test for the running Barcode Monitor stack.

Exercises: login, auth enforcement, summary, hourly, events (filters/pagination),
scanners, generator pause/resume, WebSocket snapshot, and reset confirmation.

Usage:  python3 scripts/smoke_test.py   (with the backend running on :8000)
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws/events"


def request_json(path: str, token: str | None = None, method: str = "GET", body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if raw else {}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def login(username: str, password: str) -> str:
    status, payload = request_json("/api/auth/login", method="POST", body={"username": username, "password": password})
    require(status == 200, f"login failed for {username}: {payload}")
    return payload["access_token"]  # type: ignore[index]


def main() -> int:
    admin = login("admin", "admin123")

    status, _ = request_json("/health")
    require(status == 200, "health endpoint failed")

    status, _ = request_json("/api/monitoring/summary")
    require(status == 401, "summary should require auth")

    status, summary = request_json("/api/monitoring/summary?range_hours=24", admin)
    require(status == 200, "summary failed")
    require(summary["total_activations"] > 0, "no seeded activations present")  # type: ignore[index]

    status, hourly = request_json("/api/monitoring/hourly?range_hours=24", admin)
    require(status == 200 and len(hourly["buckets"]) > 0, "hourly aggregates empty")  # type: ignore[index]

    status, events = request_json("/api/events?page=1&page_size=5", admin)
    require(status == 200 and len(events["items"]) <= 5, "events pagination failed")  # type: ignore[index]

    status, filtered = request_json("/api/events?status=failed&page_size=5", admin)
    require(status == 200, "events status filter failed")
    require(all(item["status"] == "failed" for item in filtered["items"]), "status filter returned wrong rows")  # type: ignore[index]

    status, scanners = request_json("/api/scanners", admin)
    require(status == 200 and len(scanners) >= 1, "scanners endpoint empty")  # type: ignore[index]

    status, paused = request_json("/api/generator/pause", admin, method="POST")
    require(status == 200 and paused["enabled"] is False, "pause failed")  # type: ignore[index]
    status, resumed = request_json("/api/generator/resume", admin, method="POST")
    require(status == 200 and resumed["enabled"] is True, "resume failed")  # type: ignore[index]

    status, reset_guard = request_json("/api/test/reset", admin, method="POST", body={"confirm": False})
    require(status == 400, "reset without confirm should be rejected")

    # WebSocket snapshot check
    try:
        import websockets  # type: ignore
    except ImportError:
        print("(websockets not installed; skipping live WS assertion)")
    else:
        import asyncio

        async def check_ws() -> None:
            async with websockets.connect(f"{WS_URL}?token={admin}") as ws:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                require(message["type"] == "snapshot", "ws did not send snapshot first")

        asyncio.run(check_ws())

    print("Smoke test passed")
    print(f"Total activations (24h): {summary['total_activations']}")  # type: ignore[index]
    print(f"Success rate: {summary['success_rate']}%")  # type: ignore[index]
    print(f"Scanners: {len(scanners)}")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
