import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import Plot from "react-plotly.js";
import type { Layout } from "plotly.js";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  CircleSlash,
  Clock,
  Database,
  Languages,
  Moon,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ScanLine,
  ShieldCheck,
  Sun,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import { api, ApiError, buildWsUrl, clearToken, getToken, setToken, WS_URL } from "./api";
import type {
  BarcodeEvent,
  EventFilters,
  EventListResponse,
  GeneratorStatus,
  HourlyBucket,
  MonitoringSummary,
  ScannerActivity,
  ScannerStatus,
  UserInfo,
  WsMessage,
} from "./types";
import { I18nContext, LANGUAGES, translate, useI18n } from "./i18n";
import type { I18nContextValue, Lang, TranslationKey } from "./i18n";
import "./styles.css";

const RANGE_OPTIONS = [
  { label: "1h", hours: 1 },
  { label: "8h", hours: 8 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
];

const DEFAULT_FILTERS: EventFilters = {
  page: 1,
  page_size: 15,
  scanner_id: "",
  status: "",
  range_hours: 24,
  sort: "timestamp",
  order: "desc",
};

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

type Theme = "light" | "dark";

const THEME_KEY = "barcode_monitor_theme";
const LANG_KEY = "barcode_monitor_lang";

function getInitialTheme(): Theme {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getInitialLang(): Lang {
  const stored = localStorage.getItem(LANG_KEY);
  if (stored === "en" || stored === "id") return stored;
  return navigator.language?.toLowerCase().startsWith("id") ? "id" : "en";
}

function App() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [lang, setLang] = useState<Lang>(getInitialLang);

  const t = useCallback<I18nContextValue["t"]>(
    (key: TranslationKey, vars?: Record<string, string | number>) => translate(lang, key, vars),
    [lang],
  );

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem(LANG_KEY, lang);
    document.documentElement.lang = lang;
  }, [lang]);

  const [user, setUser] = useState<UserInfo | null>(null);
  const [loginUser, setLoginUser] = useState("admin");
  const [loginPass, setLoginPass] = useState("admin123");
  const [authError, setAuthError] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);

  const [summary, setSummary] = useState<MonitoringSummary | null>(null);
  const [hourly, setHourly] = useState<HourlyBucket[]>([]);
  const [scannerActivity, setScannerActivity] = useState<ScannerActivity[]>([]);
  const [scanners, setScanners] = useState<ScannerStatus[]>([]);
  const [generator, setGenerator] = useState<GeneratorStatus | null>(null);
  const [events, setEvents] = useState<EventListResponse | null>(null);
  const [filters, setFilters] = useState<EventFilters>(DEFAULT_FILTERS);
  const [eventsLoading, setEventsLoading] = useState(false);

  const [wsConnected, setWsConnected] = useState(false);
  const [wsAttempts, setWsAttempts] = useState(0);
  const [dataError, setDataError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  const [intervalInput, setIntervalInput] = useState("2500");
  const [successInput, setSuccessInput] = useState("0.92");

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<number | null>(null);
  const seenEvents = useRef<Set<string>>(new Set());

  const token = getToken();

  // ---- auth bootstrap ------------------------------------------------------
  useEffect(() => {
    if (!token) {
      setBooting(false);
      return;
    }
    fetch(`${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error("session expired");
        return r.json();
      })
      .then((u: UserInfo) => setUser(u))
      .catch(() => {
        clearToken();
        setUser(null);
      })
      .finally(() => setBooting(false));
  }, [token]);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setAuthError(null);
    setPending("login");
    try {
      const res = await api.login(loginUser, loginPass);
      setToken(res.access_token);
      setUser(res.user);
    } catch (err) {
      setAuthError(err instanceof ApiError ? err.message : t("notice.loginFailed"));
    } finally {
      setPending(null);
    }
  }

  function handleLogout() {
    clearToken();
    setUser(null);
    setSummary(null);
    setEvents(null);
    setWsConnected(false);
    socketRef.current?.close();
  }

  // ---- REST loaders --------------------------------------------------------
  const loadEvents = useCallback(async (f: EventFilters) => {
    setEventsLoading(true);
    try {
      const res = await api.events(f);
      setEvents(res);
      res.items.forEach((e) => seenEvents.current.add(e.event_id));
    } catch (err) {
      setDataError(err instanceof ApiError ? err.message : t("notice.loadEventsFailed"));
    } finally {
      setEventsLoading(false);
    }
  }, [t]);

  const loadAll = useCallback(
    async (rangeHours: number) => {
      try {
        const [sum, hrs, act, scn, gen] = await Promise.all([
          api.summary(rangeHours),
          api.hourly(rangeHours),
          api.scannerActivity(rangeHours),
          api.scanners(),
          api.status().then((s) => s.generator),
        ]);
        setSummary(sum);
        setHourly(hrs.buckets);
        setScannerActivity(act);
        setScanners(scn);
        setGenerator(gen);
        setLastUpdate(new Date().toISOString());
        setDataError(null);
      } catch (err) {
        setDataError(err instanceof ApiError ? err.message : t("notice.loadDataFailed"));
      }
    },
    [t],
  );

  useEffect(() => {
    if (!user) return;
    loadAll(filters.range_hours);
  }, [user, filters.range_hours, loadAll]);

  useEffect(() => {
    if (!user) return;
    loadEvents(filters);
  }, [user, filters, loadEvents]);

  useEffect(() => {
    if (!generator) return;
    setIntervalInput(String(generator.interval_ms));
    setSuccessInput(String(generator.success_rate));
  }, [generator?.interval_ms, generator?.success_rate]);

  // ---- WebSocket -----------------------------------------------------------
  const connectWs = useCallback(() => {
    const t = getToken();
    if (!t) return;
    const socket = new WebSocket(buildWsUrl(t));
    socketRef.current = socket;

    socket.onopen = () => {
      setWsConnected(true);
      setWsAttempts(0);
      // Refresh REST data to recover anything missed while disconnected.
      loadAll(filters.range_hours);
      loadEvents(filters);
    };
    socket.onclose = () => {
      setWsConnected(false);
      const attempt = wsAttempts + 1;
      setWsAttempts(attempt);
      const backoff = Math.min(15000, 1000 * 2 ** Math.min(attempt, 4));
      reconnectRef.current = window.setTimeout(connectWs, backoff);
    };
    socket.onerror = () => socket.close();
    socket.onmessage = (evt) => {
      let message: WsMessage;
      try {
        message = JSON.parse(evt.data);
      } catch {
        return;
      }
      if (message.type === "snapshot") {
        const p = message.payload;
        setSummary((prev) => buildSummaryFromSnapshot(p, prev, filters.range_hours));
        setScanners(p.scanners);
        setGenerator(p.generator);
      } else if (message.type === "event.created") {
        const event = message.payload;
        if (seenEvents.current.has(event.event_id)) return;
        seenEvents.current.add(event.event_id);
        setEvents((prev) =>
          prev
            ? {
                ...prev,
                total: prev.total + 1,
                items: [event, ...prev.items].slice(0, prev.page_size),
              }
            : prev,
        );
        setLastUpdate(new Date().toISOString());
      } else if (message.type === "metrics.updated") {
        setSummary((prev) => (prev ? { ...prev, ...message.payload } : prev));
        setLastUpdate(new Date().toISOString());
      } else if (message.type === "scanner.updated") {
        const { scanner_id, state } = message.payload;
        setScanners((prev) =>
          prev.map((s) => (s.scanner_id === scanner_id ? { ...s, state } : s)),
        );
      } else if (message.type === "generator.status") {
        setGenerator(message.payload);
      } else if (message.type === "system.error") {
        setDataError(message.payload.message);
      }
    };
  }, [filters, loadAll, loadEvents, wsAttempts]);

  useEffect(() => {
    if (!user) return;
    connectWs();
    return () => {
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      socketRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  // Periodic REST refresh keeps charts/scanners honest even if WS is down.
  useEffect(() => {
    if (!user) return;
    const timer = window.setInterval(() => loadAll(filters.range_hours), 10000);
    return () => window.clearInterval(timer);
  }, [user, filters.range_hours, loadAll]);

  // ---- control actions -----------------------------------------------------
  async function runControl(kind: "pause" | "resume" | "config" | "reset") {
    setPending(kind);
    setNotice(null);
    try {
      if (kind === "pause") {
        setGenerator(await api.pause());
        setNotice({ tone: "ok", text: t("notice.paused") });
      } else if (kind === "resume") {
        setGenerator(await api.resume());
        setNotice({ tone: "ok", text: t("notice.resumed") });
      } else if (kind === "config") {
        const gen = await api.configure({
          interval_ms: Number(intervalInput),
          success_rate: Number(successInput),
        });
        setGenerator(gen);
        setNotice({ tone: "ok", text: t("notice.configUpdated") });
      } else if (kind === "reset") {
        if (!window.confirm(t("notice.resetConfirm"))) {
          setPending(null);
          return;
        }
        const res = await api.reset();
        setNotice({ tone: "ok", text: res.detail });
        seenEvents.current.clear();
        await loadAll(filters.range_hours);
        await loadEvents({ ...filters, page: 1 });
      }
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : t("notice.controlFailed") });
    } finally {
      setPending(null);
    }
  }

  // ---- derived -------------------------------------------------------------
  const scannerOptions = useMemo(() => scanners.map((s) => s.scanner_id), [scanners]);

  if (booting) {
    return <FullScreenMessage title={t("conn.connecting")} detail={t("conn.checking")} />;
  }

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-panel px-4 dark:bg-panel-dark">
        <div className="w-full max-w-md">
          <div className="mb-3 flex items-center justify-end gap-2">
            <LangSwitch lang={lang} setLang={setLang} />
            <ThemeSwitch theme={theme} setTheme={setTheme} />
          </div>
          <form onSubmit={handleLogin} className="w-full rounded-lg border border-line bg-white p-8 shadow-sm dark:border-line-dark dark:bg-surface-dark">
            <div className="mb-6 flex items-center gap-3">
              <span className="inline-flex rounded bg-signal/10 p-2 text-signal"><ScanLine size={22} /></span>
              <div>
                <h1 className="text-xl font-semibold dark:text-ink-dark">{t("app.title")}</h1>
                <p className="text-sm text-steel dark:text-steel-dark">{t("app.loginSubtitle")}</p>
              </div>
            </div>
            <label className="mb-3 grid gap-1 text-sm font-medium text-steel dark:text-steel-dark">
              {t("common.username")}
              <select
                className="h-10 rounded-md border border-line bg-white px-3 text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                value={loginUser}
                onChange={(e) => {
                  setLoginUser(e.target.value);
                  setLoginPass(`${e.target.value}123`);
                }}
              >
                <option value="admin">admin</option>
                <option value="operator">operator</option>
                <option value="viewer">viewer</option>
              </select>
            </label>
            <label className="mb-4 grid gap-1 text-sm font-medium text-steel dark:text-steel-dark">
              {t("common.password")}
              <input
                type="password"
                className="h-10 rounded-md border border-line bg-white px-3 text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                value={loginPass}
                onChange={(e) => setLoginPass(e.target.value)}
              />
            </label>
            {authError && (
              <div className="mb-4 rounded-md border border-fault bg-fault/10 px-3 py-2 text-sm text-fault">{authError}</div>
            )}
            <button
              type="submit"
              disabled={pending === "login"}
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-signal text-sm font-semibold text-white disabled:opacity-60"
            >
              <ShieldCheck size={16} /> {pending === "login" ? t("common.signingIn") : t("common.signIn")}
            </button>
            <p className="mt-4 text-center text-xs text-steel dark:text-steel-dark">
              {t("common.devCreds")}
            </p>
          </form>
        </div>
      </div>
    );
  }

  const kpis = [
    { label: t("kpi.total"), value: summary?.total_activations ?? 0, icon: <Activity size={18} /> },
    { label: t("kpi.successful"), value: summary?.successful_activations ?? 0, icon: <CheckCircle2 size={18} />, tone: "ok" as const },
    { label: t("kpi.failed"), value: summary?.failed_activations ?? 0, icon: <XCircle size={18} />, tone: "fault" as const },
    { label: t("kpi.successRate"), value: `${(summary?.success_rate ?? 0).toFixed(1)}%`, icon: <BarChart3 size={18} /> },
    { label: t("kpi.activeScanners"), value: `${summary?.active_scanners ?? 0}/${summary?.total_scanners ?? 0}`, icon: <ScanLine size={18} /> },
    { label: t("kpi.lastEvent"), value: fmtClock(summary?.latest_event_at), icon: <Clock size={18} /> },
  ];

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
    <div className="min-h-screen bg-panel text-ink dark:bg-panel-dark dark:text-ink-dark">
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-line bg-white dark:border-line-dark dark:bg-surface-dark">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-5 py-3">
          <div className="flex items-center gap-3">
            <span className="inline-flex rounded bg-signal/10 p-2 text-signal"><ScanLine size={20} /></span>
            <div>
              <h1 className="text-lg font-semibold leading-tight">{t("app.title")}</h1>
              <p className="text-xs text-steel dark:text-steel-dark">{t("app.subtitle")}</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <SimulatedBadge />
            <ConnBadge connected={wsConnected} attempts={wsAttempts} />
            <LangSwitch lang={lang} setLang={setLang} />
            <ThemeSwitch theme={theme} setTheme={setTheme} />
            <span className="rounded-md border border-line px-2 py-1 text-steel dark:border-line-dark dark:text-steel-dark">{user.display_name}</span>
            <button onClick={handleLogout} className="rounded-md border border-line px-3 py-1 font-medium hover:border-fault hover:text-fault dark:border-line-dark">
              {t("common.signOut")}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-5 py-5">
        {/* Range + status bar */}
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 rounded-md border border-line bg-white p-1 shadow-sm dark:border-line-dark dark:bg-surface-dark">
            {RANGE_OPTIONS.map((opt) => (
              <button
                key={opt.label}
                onClick={() => setFilters((f) => ({ ...f, range_hours: opt.hours, page: 1 }))}
                className={`h-8 rounded px-3 text-sm font-semibold ${
                  filters.range_hours === opt.hours ? "bg-signal text-white" : "text-steel hover:bg-panel dark:text-steel-dark dark:hover:bg-panel-dark"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 text-xs text-steel dark:text-steel-dark">
            <span className="inline-flex items-center gap-1"><Database size={14} /> PostgreSQL · production_monitoring</span>
            <span>{t("range.updated", { time: lastUpdate ? fmtClock(lastUpdate) : "—" })}</span>
          </div>
        </div>

        {dataError && (
          <div className="mb-4 rounded-md border border-fault bg-fault/10 px-4 py-3 text-sm text-fault">
            {dataError}
          </div>
        )}
        {!wsConnected && (
          <div className="mb-4 rounded-md border border-warning bg-warning/10 px-4 py-3 text-sm text-warning">
            {t("conn.warning")}
          </div>
        )}

        {/* KPI cards */}
        <section className="mb-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
          {kpis.map((kpi) => (
            <div key={kpi.label} className="rounded-md border border-line bg-white p-4 shadow-sm dark:border-line-dark dark:bg-surface-dark">
              <div className={`mb-2 inline-flex rounded p-1.5 ${toneBg(kpi.tone)}`}>{kpi.icon}</div>
              <p className="text-xs font-medium text-steel dark:text-steel-dark">{kpi.label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{typeof kpi.value === "number" ? kpi.value.toLocaleString() : kpi.value}</p>
            </div>
          ))}
        </section>

        {/* Charts */}
        <section className="mb-5 grid gap-4 xl:grid-cols-2">
          <ChartPanel title={t("chart.hourlyTitle")} subtitle={t("chart.hourlySubtitle", { count: hourly.length, range: filters.range_hours })}>
            <Plot
              data={[
                { x: hourly.map((b) => b.bucket), y: hourly.map((b) => b.successful), type: "bar", name: t("chart.success"), marker: { color: "#1b8a5a" } },
                { x: hourly.map((b) => b.bucket), y: hourly.map((b) => b.failed), type: "bar", name: t("chart.failed"), marker: { color: "#c0392b" } },
              ]}
              layout={{ ...plotLayout(t("chart.axisActivations"), theme), barmode: "stack" }}
              config={{ displayModeBar: false, responsive: true }}
              className="h-full w-full"
              style={{ width: "100%", height: "100%" }}
            />
          </ChartPanel>

          <ChartPanel title={t("chart.byScannerTitle")} subtitle={t("chart.byScannerSubtitle")}>
            <Plot
              data={[
                { x: scannerActivity.map((s) => s.scanner_id), y: scannerActivity.map((s) => s.successful), type: "bar", name: t("chart.success"), marker: { color: "#1b8a5a" } },
                { x: scannerActivity.map((s) => s.scanner_id), y: scannerActivity.map((s) => s.failed), type: "bar", name: t("chart.failed"), marker: { color: "#c0392b" } },
              ]}
              layout={{ ...plotLayout(t("chart.axisActivations"), theme), barmode: "group" }}
              config={{ displayModeBar: false, responsive: true }}
              className="h-full w-full"
              style={{ width: "100%", height: "100%" }}
            />
          </ChartPanel>
        </section>

        {/* Scanner cards */}
        <section className="mb-5">
          <SectionTitle icon={<ScanLine size={16} />} title={t("scanners.title")} hint={t("scanners.hint")} />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {scanners.map((s) => (
              <div key={s.scanner_id} className={`rounded-md border bg-white p-4 shadow-sm dark:bg-surface-dark ${s.state === "offline" ? "border-fault" : "border-line dark:border-line-dark"}`}>
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-semibold">{s.scanner_id}</span>
                  <ScannerStateBadge state={s.state} />
                </div>
                <dl className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <dt className="text-xs text-steel dark:text-steel-dark">{t("scanners.events")}</dt>
                    <dd className="font-medium tabular-nums">{s.events_count.toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-steel dark:text-steel-dark">{t("scanners.failures")}</dt>
                    <dd className="font-medium tabular-nums">{s.error_count.toLocaleString()}</dd>
                  </div>
                  <div className="col-span-2">
                    <dt className="text-xs text-steel dark:text-steel-dark">{t("scanners.lastEvent")}</dt>
                    <dd className="font-medium">{fmtTime(s.last_event_at)}</dd>
                  </div>
                </dl>
              </div>
            ))}
            {scanners.length === 0 && <EmptyRow text={t("scanners.empty")} />}
          </div>
        </section>

        {/* Generator controls + events table */}
        <section className="grid gap-4 xl:grid-cols-[340px_1fr]">
          <div className="space-y-4">
            <div className="rounded-md border border-line bg-white p-4 shadow-sm dark:border-line-dark dark:bg-surface-dark">
              <SectionTitle icon={<Activity size={16} />} title={t("controls.title")} hint={t("controls.hint")} />
              <div className="mb-3 flex items-center justify-between rounded border border-line px-3 py-2 text-sm dark:border-line-dark">
                <span className="text-steel dark:text-steel-dark">{t("controls.state")}</span>
                <span className={`font-semibold ${generator?.enabled ? "text-ok" : "text-warning"}`}>
                  {generator?.enabled ? t("controls.running") : t("controls.paused")}
                </span>
              </div>
              <div className="mb-3 flex gap-2">
                <ControlButton
                  icon={<Pause size={15} />}
                  label={t("controls.pause")}
                  onClick={() => runControl("pause")}
                  disabled={!generator?.enabled || pending !== null}
                />
                <ControlButton
                  icon={<Play size={15} />}
                  label={t("controls.resume")}
                  onClick={() => runControl("resume")}
                  disabled={!!generator?.enabled || pending !== null}
                />
              </div>
              <label className="mb-2 grid gap-1 text-xs font-medium text-steel dark:text-steel-dark">
                {t("controls.interval")}
                <input
                  type="number"
                  min={200}
                  max={60000}
                  value={intervalInput}
                  onChange={(e) => setIntervalInput(e.target.value)}
                  className="h-9 rounded-md border border-line px-2 text-sm text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                />
              </label>
              <label className="mb-3 grid gap-1 text-xs font-medium text-steel dark:text-steel-dark">
                {t("controls.successRate")}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={successInput}
                  onChange={(e) => setSuccessInput(e.target.value)}
                  className="h-9 rounded-md border border-line px-2 text-sm text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                />
              </label>
              <ControlButton
                icon={<RefreshCw size={15} />}
                label={pending === "config" ? t("controls.saving") : t("controls.apply")}
                onClick={() => runControl("config")}
                disabled={pending !== null}
                block
              />
              <div className="mt-4 border-t border-line pt-3 text-xs text-steel dark:border-line-dark dark:text-steel-dark">
                <p>{t("controls.lastGenerated")} <span className="font-medium text-ink dark:text-ink-dark">{fmtTime(generator?.last_generated_at)}</span></p>
                <p>{t("controls.scannerCount")} <span className="font-medium text-ink dark:text-ink-dark">{generator?.scanner_count ?? 0}</span></p>
                {generator?.last_error && <p className="mt-1 text-fault">{t("controls.lastError")} {generator.last_error}</p>}
              </div>
              <div className="mt-4">
                <ControlButton
                  icon={<RotateCcw size={15} />}
                  label={pending === "reset" ? t("controls.resetting") : t("controls.reset")}
                  onClick={() => runControl("reset")}
                  disabled={pending !== null}
                  danger
                  block
                />
              </div>
              {notice && (
                <div
                  className={`mt-3 rounded-md border px-3 py-2 text-xs ${
                    notice.tone === "ok" ? "border-ok bg-ok/10 text-ok" : "border-fault bg-fault/10 text-fault"
                  }`}
                >
                  {notice.text}
                </div>
              )}
            </div>
          </div>

          {/* Events table */}
          <div className="rounded-md border border-line bg-white p-4 shadow-sm dark:border-line-dark dark:bg-surface-dark">
            <SectionTitle icon={<BarChart3 size={16} />} title={t("events.title")} hint={t("events.hint")} />

            <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <label className="grid gap-1 text-xs font-medium text-steel dark:text-steel-dark">
                {t("events.filterScanner")}
                <select
                  value={filters.scanner_id}
                  onChange={(e) => setFilters((f) => ({ ...f, scanner_id: e.target.value, page: 1 }))}
                  className="h-9 rounded-md border border-line px-2 text-sm text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                >
                  <option value="">{t("events.allScanners")}</option>
                  {scannerOptions.map((id) => (
                    <option key={id} value={id}>{id}</option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-xs font-medium text-steel dark:text-steel-dark">
                {t("events.filterStatus")}
                <select
                  value={filters.status}
                  onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value as EventFilters["status"], page: 1 }))}
                  className="h-9 rounded-md border border-line px-2 text-sm text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                >
                  <option value="">{t("events.allStatuses")}</option>
                  <option value="success">{t("status.success")}</option>
                  <option value="failed">{t("status.failed")}</option>
                </select>
              </label>
              <label className="grid gap-1 text-xs font-medium text-steel dark:text-steel-dark">
                {t("events.sort")}
                <select
                  value={filters.sort}
                  onChange={(e) => setFilters((f) => ({ ...f, sort: e.target.value as EventFilters["sort"] }))}
                  className="h-9 rounded-md border border-line px-2 text-sm text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                >
                  <option value="timestamp">{t("sort.timestamp")}</option>
                  <option value="event_id">{t("sort.eventId")}</option>
                  <option value="scanner_id">{t("sort.scanner")}</option>
                  <option value="status">{t("sort.status")}</option>
                </select>
              </label>
              <label className="grid gap-1 text-xs font-medium text-steel dark:text-steel-dark">
                {t("events.order")}
                <select
                  value={filters.order}
                  onChange={(e) => setFilters((f) => ({ ...f, order: e.target.value as EventFilters["order"] }))}
                  className="h-9 rounded-md border border-line px-2 text-sm text-ink outline-none focus:border-signal dark:border-line-dark dark:bg-panel-dark dark:text-ink-dark"
                >
                  <option value="desc">{t("events.newestFirst")}</option>
                  <option value="asc">{t("events.oldestFirst")}</option>
                </select>
              </label>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs uppercase text-steel dark:border-line-dark dark:text-steel-dark">
                    <th className="py-2 pr-3 font-semibold">{t("events.colTimestamp")}</th>
                    <th className="py-2 pr-3 font-semibold">{t("events.colEventId")}</th>
                    <th className="py-2 pr-3 font-semibold">{t("events.colBarcode")}</th>
                    <th className="py-2 pr-3 font-semibold">{t("events.colScanner")}</th>
                    <th className="py-2 pr-3 font-semibold">{t("events.colType")}</th>
                    <th className="py-2 pr-3 font-semibold">{t("events.colStatus")}</th>
                    <th className="py-2 pr-3 font-semibold">{t("events.colError")}</th>
                  </tr>
                </thead>
                <tbody>
                  {eventsLoading && (
                    <tr>
                      <td colSpan={7} className="py-6 text-center text-steel dark:text-steel-dark">{t("events.loading")}</td>
                    </tr>
                  )}
                  {!eventsLoading && events?.items.length === 0 && (
                    <tr>
                      <td colSpan={7} className="py-6 text-center text-steel dark:text-steel-dark">
                        {t("events.empty")} {generator?.enabled ? t("events.waitingGenerator") : t("events.generatorPaused")}
                      </td>
                    </tr>
                  )}
                  {!eventsLoading &&
                    events?.items.map((event) => (
                      <tr key={event.event_id} className="border-b border-line/60 hover:bg-panel dark:border-line-dark/60 dark:hover:bg-panel-dark">
                        <td className="py-2 pr-3 tabular-nums text-steel dark:text-steel-dark">{fmtTime(event.timestamp)}</td>
                        <td className="py-2 pr-3 font-mono text-xs">{event.event_id}</td>
                        <td className="py-2 pr-3 font-mono text-xs">{event.barcode}</td>
                        <td className="py-2 pr-3">{event.scanner_id}</td>
                        <td className="py-2 pr-3 text-steel dark:text-steel-dark">{event.event_type}</td>
                        <td className="py-2 pr-3"><StatusBadge status={event.status} /></td>
                        <td className="py-2 pr-3 text-xs text-fault">{event.error_message ?? "—"}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>

            {events && (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-steel dark:text-steel-dark">
                <span>
                  {events.total.toLocaleString()} {t("common.events")} · {t("common.page")} {events.page} {t("events.of")} {events.total_pages}
                </span>
                <div className="flex items-center gap-2">
                  <ControlButton
                    icon={<Clock size={14} />}
                    label={t("common.prev")}
                    onClick={() => setFilters((f) => ({ ...f, page: Math.max(1, f.page - 1) }))}
                    disabled={events.page <= 1}
                  />
                  <ControlButton
                    icon={<Clock size={14} />}
                    label={t("common.next")}
                    onClick={() => setFilters((f) => ({ ...f, page: Math.min(events.total_pages, f.page + 1) }))}
                    disabled={events.page >= events.total_pages}
                  />
                </div>
              </div>
            )}
          </div>
        </section>

        <footer className="mt-6 pb-4 text-center text-xs text-steel dark:text-steel-dark">
          {t("app.title")} · {t("app.footer")}
          <br />
          WebSocket: {WS_URL}
        </footer>
      </main>
    </div>
    </I18nContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------
function toneBg(tone?: "ok" | "fault"): string {
  if (tone === "ok") return "bg-ok/10 text-ok";
  if (tone === "fault") return "bg-fault/10 text-fault";
  return "bg-signal/10 text-signal";
}

function buildSummaryFromSnapshot(
  p: { summary: MonitoringSummary | any; scanners: ScannerStatus[]; generator: GeneratorStatus },
  prev: MonitoringSummary | null,
  rangeHours: number,
): MonitoringSummary {
  return {
    total_activations: p.summary.total_activations,
    successful_activations: p.summary.successful_activations,
    failed_activations: p.summary.failed_activations,
    success_rate: p.summary.success_rate,
    active_scanners: p.summary.active_scanners,
    total_scanners: p.summary.total_scanners,
    latest_event_at: p.summary.latest_event_at,
    range_start: prev?.range_start ?? new Date().toISOString(),
    range_end: prev?.range_end ?? new Date().toISOString(),
    range_hours: rangeHours,
    is_simulated: true,
  };
}

function SimulatedBadge() {
  const { t } = useI18n();
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-warning bg-warning/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-warning">
      <AlertTriangle size={13} /> {t("badge.simulated")}
    </span>
  );
}

function ConnBadge({ connected, attempts }: { connected: boolean; attempts: number }) {
  const { t } = useI18n();
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold ${
        connected ? "border-ok bg-ok/10 text-ok" : "border-fault bg-fault/10 text-fault"
      }`}
    >
      {connected ? <Wifi size={13} /> : <WifiOff size={13} />}
      {connected ? t("conn.live") : attempts > 0 ? t("conn.reconnecting", { n: attempts }) : t("conn.disconnected")}
    </span>
  );
}

function ScannerStateBadge({ state }: { state: ScannerStatus["state"] }) {
  const { t } = useI18n();
  const online = state === "online";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold ${
        online ? "bg-ok/10 text-ok" : "bg-fault/10 text-fault"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${online ? "bg-ok" : "bg-fault"}`} />
      {online ? t("scanners.online") : t("scanners.offline")}
    </span>
  );
}

function StatusBadge({ status }: { status: BarcodeEvent["status"] }) {
  const { t } = useI18n();
  const ok = status === "success";
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold ${ok ? "bg-ok/10 text-ok" : "bg-fault/10 text-fault"}`}>
      {ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
      {ok ? t("status.success") : t("status.failed")}
    </span>
  );
}

function ThemeSwitch({ theme, setTheme }: { theme: Theme; setTheme: (t: Theme) => void }) {
  const { t } = useI18n();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      onClick={() => setTheme(next)}
      title={`${t("common.theme")}: ${theme === "dark" ? t("common.themeDark") : t("common.themeLight")}`}
      aria-label={t("common.theme")}
      className="inline-flex h-8 items-center gap-1 rounded-md border border-line bg-white px-2 text-xs font-semibold text-steel hover:border-signal dark:border-line-dark dark:bg-surface-dark dark:text-steel-dark"
    >
      {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
      {theme === "dark" ? t("common.themeLight") : t("common.themeDark")}
    </button>
  );
}

function LangSwitch({ lang, setLang }: { lang: Lang; setLang: (l: Lang) => void }) {
  return (
    <span className="inline-flex h-8 items-center gap-1 rounded-md border border-line bg-white px-1 text-xs font-semibold text-steel dark:border-line-dark dark:bg-surface-dark dark:text-steel-dark">
      <Languages size={14} className="ml-1" />
      {LANGUAGES.map((l) => (
        <button
          key={l.code}
          onClick={() => setLang(l.code)}
          className={`h-6 rounded px-2 uppercase ${
            lang === l.code ? "bg-signal text-white" : "hover:bg-panel dark:hover:bg-panel-dark"
          }`}
        >
          {l.code}
        </button>
      ))}
    </span>
  );
}

function SectionTitle({ icon, title, hint }: { icon: React.ReactNode; title: string; hint?: string }) {
  return (
    <div className="mb-3 flex items-start gap-2">
      <span className="mt-0.5 text-signal">{icon}</span>
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {hint && <p className="text-xs text-steel dark:text-steel-dark">{hint}</p>}
      </div>
    </div>
  );
}

function ChartPanel({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="h-[340px] rounded-md border border-line bg-white p-4 shadow-sm dark:border-line-dark dark:bg-surface-dark">
      <div className="mb-2">
        <h2 className="text-base font-semibold">{title}</h2>
        {subtitle && <p className="text-xs text-steel dark:text-steel-dark">{subtitle}</p>}
      </div>
      <div className="h-[260px]">{children}</div>
    </section>
  );
}

function ControlButton({
  icon,
  label,
  onClick,
  disabled = false,
  block = false,
  danger = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  block?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-9 items-center justify-center gap-2 rounded-md border px-3 text-sm font-semibold disabled:opacity-50 ${
        block ? "w-full" : ""
      } ${
        danger
          ? "border-fault text-fault hover:bg-fault/10"
          : "border-line bg-white hover:border-signal dark:border-line-dark dark:bg-surface-dark"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function EmptyRow({ text }: { text: string }) {
  return <p className="col-span-full rounded border border-dashed border-line px-3 py-6 text-center text-sm text-steel dark:border-line-dark dark:text-steel-dark">{text}</p>;
}

function FullScreenMessage({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-panel dark:bg-panel-dark">
      <div className="text-center">
        <CircleSlash className="mx-auto mb-3 text-steel dark:text-steel-dark" />
        <p className="font-semibold dark:text-ink-dark">{title}</p>
        <p className="text-sm text-steel dark:text-steel-dark">{detail}</p>
      </div>
    </div>
  );
}

function plotLayout(yTitle: string, theme: Theme): Partial<Layout> {
  const dark = theme === "dark";
  const textColor = dark ? "#e6edf3" : "#0f1720";
  const bg = dark ? "#161b22" : "white";
  const grid = dark ? "#30363d" : "#edf1f4";
  return {
    autosize: true,
    margin: { t: 10, r: 15, b: 35, l: 45 },
    paper_bgcolor: bg,
    plot_bgcolor: bg,
    font: { family: "Inter, system-ui, sans-serif", color: textColor, size: 11 },
    yaxis: { title: { text: yTitle }, gridcolor: grid },
    xaxis: { gridcolor: grid },
    legend: { orientation: "h", y: -0.2, font: { color: textColor } },
  };
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
