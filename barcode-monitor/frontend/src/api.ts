// Centralised API client. All backend URLs and auth handling live here.

import type {
  EventFilters,
  EventListResponse,
  GeneratorStatus,
  HourlyResponse,
  LoginResponse,
  MonitoringStatus,
  MonitoringSummary,
  ScannerActivity,
  ScannerStatus,
} from "./types";

export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const WS_URL: string = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws/events";

const TOKEN_KEY = "barcode_monitor_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const authToken = token ?? getToken();
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);

  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore parse errors */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  login(username: string, password: string): Promise<LoginResponse> {
    return request<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  },

  health(): Promise<{ status: string; database: string }> {
    return request("/health");
  },

  status(): Promise<MonitoringStatus> {
    return request<MonitoringStatus>("/api/monitoring/status");
  },

  summary(rangeHours: number): Promise<MonitoringSummary> {
    return request<MonitoringSummary>(`/api/monitoring/summary?range_hours=${rangeHours}`);
  },

  hourly(rangeHours: number): Promise<HourlyResponse> {
    return request<HourlyResponse>(`/api/monitoring/hourly?range_hours=${rangeHours}`);
  },

  scannerActivity(rangeHours: number): Promise<ScannerActivity[]> {
    return request<ScannerActivity[]>(`/api/monitoring/scanner-activity?range_hours=${rangeHours}`);
  },

  events(filters: EventFilters): Promise<EventListResponse> {
    const params = new URLSearchParams({
      page: String(filters.page),
      page_size: String(filters.page_size),
      range_hours: String(filters.range_hours),
      sort: filters.sort,
      order: filters.order,
    });
    if (filters.scanner_id) params.set("scanner_id", filters.scanner_id);
    if (filters.status) params.set("status", filters.status);
    return request<EventListResponse>(`/api/events?${params.toString()}`);
  },

  scanners(): Promise<ScannerStatus[]> {
    return request<ScannerStatus[]>("/api/scanners");
  },

  pause(): Promise<GeneratorStatus> {
    return request<GeneratorStatus>("/api/generator/pause", { method: "POST" });
  },

  resume(): Promise<GeneratorStatus> {
    return request<GeneratorStatus>("/api/generator/resume", { method: "POST" });
  },

  configure(payload: { interval_ms?: number; success_rate?: number }): Promise<GeneratorStatus> {
    return request<GeneratorStatus>("/api/generator/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  reset(): Promise<{ ok: boolean; detail: string }> {
    return request("/api/test/reset", { method: "POST", body: JSON.stringify({ confirm: true }) });
  },
};

export function buildWsUrl(token: string): string {
  const separator = WS_URL.includes("?") ? "&" : "?";
  return `${WS_URL}${separator}token=${encodeURIComponent(token)}`;
}
