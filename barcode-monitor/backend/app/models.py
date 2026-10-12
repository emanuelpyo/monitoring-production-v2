"""Pydantic request/response contracts for the Barcode Monitor API.

These models are the single source of truth for the JSON shape exchanged
between the backend and the React dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

EventType = Literal["barcode_activation"]
EventStatus = Literal["success", "failed"]
ScannerState = Literal["online", "offline"]


class BarcodeEvent(BaseModel):
    id: int
    event_id: str
    barcode: str
    scanner_id: str
    event_type: str
    status: str
    timestamp: datetime
    error_message: Optional[str] = None
    is_simulated: bool = True


class EventListResponse(BaseModel):
    items: list[BarcodeEvent]
    page: int
    page_size: int
    total: int
    total_pages: int


class ScannerStatus(BaseModel):
    scanner_id: str
    state: ScannerState
    last_event_at: Optional[datetime] = None
    events_count: int = 0
    error_count: int = 0
    is_simulated: bool = True


class MonitoringSummary(BaseModel):
    total_activations: int
    successful_activations: int
    failed_activations: int
    success_rate: float
    active_scanners: int
    total_scanners: int
    latest_event_at: Optional[datetime] = None
    range_start: datetime
    range_end: datetime
    range_hours: float
    is_simulated: bool = True


class HourlyBucket(BaseModel):
    bucket: datetime
    total: int
    successful: int
    failed: int


class HourlyResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    buckets: list[HourlyBucket]
    timezone: str


class ScannerActivity(BaseModel):
    scanner_id: str
    total: int
    successful: int
    failed: int


class GeneratorStatus(BaseModel):
    running: bool
    enabled: bool
    interval_ms: int
    success_rate: float
    scanner_count: int
    last_generated_at: Optional[datetime] = None
    last_error: Optional[str] = None
    is_simulated: bool = True


class MonitoringStatus(BaseModel):
    status: Literal["ok", "degraded", "down"]
    database: Literal["ok", "down"]
    generator: GeneratorStatus
    message: Optional[str] = None


class GeneratorConfigRequest(BaseModel):
    interval_ms: Optional[int] = Field(default=None, ge=200, le=60000)
    success_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ResetRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description="Must be true to execute the destructive reset+reseed operation.",
    )


class MessageResponse(BaseModel):
    ok: bool
    detail: Optional[str] = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserInfo(BaseModel):
    username: str
    display_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo
