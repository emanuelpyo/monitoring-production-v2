// Shared TypeScript types mirroring the backend Pydantic contracts.

export type EventStatus = "success" | "failed";
export type ScannerState = "online" | "offline";

export interface BarcodeEvent {
  id: number;
  event_id: string;
  barcode: string;
  scanner_id: string;
  event_type: string;
  status: EventStatus;
  timestamp: string;
  error_message: string | null;
  is_simulated: boolean;
}

export interface EventListResponse {
  items: BarcodeEvent[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ScannerStatus {
  scanner_id: string;
  state: ScannerState;
  last_event_at: string | null;
  events_count: number;
  error_count: number;
  is_simulated: boolean;
}

export interface ScannerActivity {
  scanner_id: string;
  total: number;
  successful: number;
  failed: number;
}

export interface MonitoringSummary {
  total_activations: number;
  successful_activations: number;
  failed_activations: number;
  success_rate: number;
  active_scanners: number;
  total_scanners: number;
  latest_event_at: string | null;
  range_start: string;
  range_end: string;
  range_hours: number;
  is_simulated: boolean;
}

export interface HourlyBucket {
  bucket: string;
  total: number;
  successful: number;
  failed: number;
}

export interface HourlyResponse {
  range_start: string;
  range_end: string;
  buckets: HourlyBucket[];
  timezone: string;
}

export interface GeneratorStatus {
  running: boolean;
  enabled: boolean;
  interval_ms: number;
  success_rate: number;
  scanner_count: number;
  last_generated_at: string | null;
  last_error: string | null;
  is_simulated: boolean;
}

export interface MonitoringStatus {
  status: "ok" | "degraded" | "down";
  database: "ok" | "down";
  generator: GeneratorStatus;
  message: string | null;
}

export interface UserInfo {
  username: string;
  display_name: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserInfo;
}

export interface EventFilters {
  page: number;
  page_size: number;
  scanner_id: string;
  status: "" | EventStatus;
  range_hours: number;
  sort: "timestamp" | "event_id" | "scanner_id" | "status";
  order: "asc" | "desc";
}

export interface WsSnapshotPayload {
  summary: {
    total_activations: number;
    successful_activations: number;
    failed_activations: number;
    success_rate: number;
    active_scanners: number;
    total_scanners: number;
    latest_event_at: string | null;
  };
  scanners: ScannerStatus[];
  generator: GeneratorStatus;
}

export type WsMessage =
  | { type: "snapshot"; payload: WsSnapshotPayload }
  | { type: "event.created"; payload: BarcodeEvent }
  | { type: "metrics.updated"; payload: Partial<MonitoringSummary> }
  | { type: "scanner.updated"; payload: { scanner_id: string; state: ScannerState } }
  | { type: "generator.status"; payload: GeneratorStatus }
  | { type: "system.error"; payload: { message: string } };
