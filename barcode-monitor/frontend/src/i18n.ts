// Lightweight i18n for the Barcode Monitor dashboard.
//
// Usage:  const { t, lang, setLang } = useI18n();
//         t("kpi.total")
//
// Translations are a flat key → string map. Both languages are typed so a
// missing key is caught at compile time.

import { createContext, useContext } from "react";

export type Lang = "en" | "id";

export interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string;
}

const en = {
  "app.title": "Barcode Monitor",
  "app.subtitle": "Production activation monitoring · local prototype",
  "app.loginSubtitle": "Local simulated production monitoring",
  "app.footer": "all events are synthetic (TEST-*, SCANNER-*) · no connection to production systems",

  "common.username": "Username",
  "common.password": "Password",
  "common.signIn": "Sign in",
  "common.signingIn": "Signing in…",
  "common.signOut": "Sign out",
  "common.devCreds": "Development credentials only. All data in this app is simulated.",
  "common.language": "Language",
  "common.theme": "Theme",
  "common.themeLight": "Light",
  "common.themeDark": "Dark",
  "common.updated": "Updated",
  "common.loading": "Loading…",
  "common.all": "All",
  "common.prev": "Prev",
  "common.next": "Next",
  "common.events": "events",
  "common.page": "page",

  "conn.connecting": "Connecting",
  "conn.checking": "Checking your session…",
  "conn.live": "Live",
  "conn.disconnected": "Disconnected",
  "conn.reconnecting": "Reconnecting (#{n})",
  "conn.warning": "Live connection unavailable — showing data refreshed every 10 seconds. Reconnecting…",

  "badge.simulated": "Simulated data",

  "kpi.total": "Total activations",
  "kpi.successful": "Successful",
  "kpi.failed": "Failed",
  "kpi.successRate": "Success rate",
  "kpi.activeScanners": "Active scanners",
  "kpi.lastEvent": "Last event",

  "range.updated": "Updated {time}",

  "chart.hourlyTitle": "Hourly activity",
  "chart.hourlySubtitle": "{count} hourly buckets · {range}h window",
  "chart.byScannerTitle": "Activity by scanner",
  "chart.byScannerSubtitle": "Total / failed per scanner",
  "chart.axisActivations": "Activations",
  "chart.success": "Success",
  "chart.failed": "Failed",

  "scanners.title": "Scanner status",
  "scanners.hint": "Simulated scanner health, not physical device telemetry",
  "scanners.events": "Events",
  "scanners.failures": "Failures",
  "scanners.lastEvent": "Last event",
  "scanners.empty": "No scanners registered yet.",
  "scanners.online": "Online",
  "scanners.offline": "Offline",

  "controls.title": "Simulation controls",
  "controls.hint": "Backend-driven generator",
  "controls.state": "State",
  "controls.running": "Running",
  "controls.paused": "Paused",
  "controls.pause": "Pause",
  "controls.resume": "Resume",
  "controls.interval": "Interval (ms)",
  "controls.successRate": "Success rate (0–1)",
  "controls.apply": "Apply config",
  "controls.saving": "Saving…",
  "controls.reset": "Reset & reseed dataset",
  "controls.resetting": "Resetting…",
  "controls.lastGenerated": "Last generated:",
  "controls.scannerCount": "Scanner count:",
  "controls.lastError": "Last error:",

  "events.title": "Recent events",
  "events.hint": "Same persisted dataset as the KPIs and charts",
  "events.filterScanner": "Scanner",
  "events.allScanners": "All scanners",
  "events.filterStatus": "Status",
  "events.allStatuses": "All statuses",
  "events.sort": "Sort",
  "events.order": "Order",
  "events.newestFirst": "Newest first",
  "events.oldestFirst": "Oldest first",
  "events.colTimestamp": "Timestamp",
  "events.colEventId": "Event ID",
  "events.colBarcode": "Barcode",
  "events.colScanner": "Scanner",
  "events.colType": "Type",
  "events.colStatus": "Status",
  "events.colError": "Error",
  "events.loading": "Loading events…",
  "events.empty": "No events in this range.",
  "events.waitingGenerator": "Waiting for the generator…",
  "events.generatorPaused": "Generator is paused.",
  "events.of": "of",

  "status.success": "Success",
  "status.failed": "Failed",

  "sort.timestamp": "Timestamp",
  "sort.eventId": "Event ID",
  "sort.scanner": "Scanner",
  "sort.status": "Status",

  "notice.paused": "Generator paused",
  "notice.resumed": "Generator resumed",
  "notice.configUpdated": "Generator configuration updated",
  "notice.resetConfirm":
    "Reset the simulated dataset and reseed 24 hours of history? This only clears barcode-monitor's simulated data.",
  "notice.controlFailed": "Control action failed",
  "notice.loginFailed": "Login failed",
  "notice.loadEventsFailed": "Failed to load events",
  "notice.loadDataFailed": "Failed to load dashboard data",
} as const;

const id: Record<TranslationKey, string> = {
  "app.title": "Monitor Barcode",
  "app.subtitle": "Pemantauan aktivasi produksi · prototipe lokal",
  "app.loginSubtitle": "Pemantauan produksi simulasi lokal",
  "app.footer": "semua event bersifat sintetis (TEST-*, SCANNER-*) · tidak terhubung ke sistem produksi",

  "common.username": "Nama pengguna",
  "common.password": "Kata sandi",
  "common.signIn": "Masuk",
  "common.signingIn": "Sedang masuk…",
  "common.signOut": "Keluar",
  "common.devCreds": "Hanya kredensial pengembangan. Semua data di aplikasi ini bersifat simulasi.",
  "common.language": "Bahasa",
  "common.theme": "Tema",
  "common.themeLight": "Terang",
  "common.themeDark": "Gelap",
  "common.updated": "Diperbarui",
  "common.loading": "Memuat…",
  "common.all": "Semua",
  "common.prev": "Sebelumnya",
  "common.next": "Berikutnya",
  "common.events": "event",
  "common.page": "halaman",

  "conn.connecting": "Menghubungkan",
  "conn.checking": "Memeriksa sesi Anda…",
  "conn.live": "Live",
  "conn.disconnected": "Terputus",
  "conn.reconnecting": "Menghubungkan ulang (#{n})",
  "conn.warning": "Koneksi live tidak tersedia — data ditampilkan dengan pembaruan tiap 10 detik. Menghubungkan ulang…",

  "badge.simulated": "Data simulasi",

  "kpi.total": "Total aktivasi",
  "kpi.successful": "Berhasil",
  "kpi.failed": "Gagal",
  "kpi.successRate": "Tingkat keberhasilan",
  "kpi.activeScanners": "Scanner aktif",
  "kpi.lastEvent": "Event terakhir",

  "range.updated": "Diperbarui {time}",

  "chart.hourlyTitle": "Aktivitas per jam",
  "chart.hourlySubtitle": "{count} bucket per jam · jendela {range} jam",
  "chart.byScannerTitle": "Aktivitas per scanner",
  "chart.byScannerSubtitle": "Total / gagal per scanner",
  "chart.axisActivations": "Aktivasi",
  "chart.success": "Berhasil",
  "chart.failed": "Gagal",

  "scanners.title": "Status scanner",
  "scanners.hint": "Status scanner simulasi, bukan telemetri perangkat fisik",
  "scanners.events": "Event",
  "scanners.failures": "Kegagalan",
  "scanners.lastEvent": "Event terakhir",
  "scanners.empty": "Belum ada scanner terdaftar.",
  "scanners.online": "Aktif",
  "scanners.offline": "Nonaktif",

  "controls.title": "Kontrol simulasi",
  "controls.hint": "Generator dijalankan backend",
  "controls.state": "Status",
  "controls.running": "Berjalan",
  "controls.paused": "Dijeda",
  "controls.pause": "Jeda",
  "controls.resume": "Lanjutkan",
  "controls.interval": "Interval (ms)",
  "controls.successRate": "Tingkat keberhasilan (0–1)",
  "controls.apply": "Terapkan konfigurasi",
  "controls.saving": "Menyimpan…",
  "controls.reset": "Reset & isi ulang data",
  "controls.resetting": "Mereset…",
  "controls.lastGenerated": "Terakhir dibuat:",
  "controls.scannerCount": "Jumlah scanner:",
  "controls.lastError": "Error terakhir:",

  "events.title": "Event terbaru",
  "events.hint": "Dataset tersimpan yang sama dengan KPI dan grafik",
  "events.filterScanner": "Scanner",
  "events.allScanners": "Semua scanner",
  "events.filterStatus": "Status",
  "events.allStatuses": "Semua status",
  "events.sort": "Urutkan",
  "events.order": "Urutan",
  "events.newestFirst": "Terbaru dulu",
  "events.oldestFirst": "Terlama dulu",
  "events.colTimestamp": "Waktu",
  "events.colEventId": "ID Event",
  "events.colBarcode": "Barcode",
  "events.colScanner": "Scanner",
  "events.colType": "Jenis",
  "events.colStatus": "Status",
  "events.colError": "Error",
  "events.loading": "Memuat event…",
  "events.empty": "Tidak ada event pada rentang ini.",
  "events.waitingGenerator": "Menunggu generator…",
  "events.generatorPaused": "Generator dijeda.",
  "events.of": "dari",

  "status.success": "Berhasil",
  "status.failed": "Gagal",

  "sort.timestamp": "Waktu",
  "sort.eventId": "ID Event",
  "sort.scanner": "Scanner",
  "sort.status": "Status",

  "notice.paused": "Generator dijeda",
  "notice.resumed": "Generator dilanjutkan",
  "notice.configUpdated": "Konfigurasi generator diperbarui",
  "notice.resetConfirm":
    "Reset dataset simulasi dan isi ulang 24 jam riwayat? Ini hanya menghapus data simulasi barcode-monitor.",
  "notice.controlFailed": "Aksi kontrol gagal",
  "notice.loginFailed": "Gagal masuk",
  "notice.loadEventsFailed": "Gagal memuat event",
  "notice.loadDataFailed": "Gagal memuat data dasbor",
};

export type TranslationKey = keyof typeof en;

export const TRANSLATIONS: Record<Lang, Record<TranslationKey, string>> = { en, id };

export const LANGUAGES: { code: Lang; label: string }[] = [
  { code: "en", label: "English" },
  { code: "id", label: "Bahasa Indonesia" },
];

export function translate(
  lang: Lang,
  key: TranslationKey,
  vars?: Record<string, string | number>,
): string {
  const table = TRANSLATIONS[lang] ?? TRANSLATIONS.en;
  let text = table[key] ?? TRANSLATIONS.en[key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replace(new RegExp(`\\{${name}\\}`, "g"), String(value));
    }
  }
  return text;
}

export const I18nContext = createContext<I18nContextValue>({
  lang: "en",
  setLang: () => undefined,
  t: (key, vars) => translate("en", key, vars),
});

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}
