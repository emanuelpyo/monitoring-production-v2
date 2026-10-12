/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0f1720",
        panel: "#f4f7f9",
        line: "#d6dde4",
        steel: "#516171",
        signal: "#0e7c86",
        ok: "#1b8a5a",
        warning: "#d9822b",
        fault: "#c0392b",
        // Dark-mode surfaces
        "ink-dark": "#e6edf3",
        "panel-dark": "#0d1117",
        "surface-dark": "#161b22",
        "line-dark": "#30363d",
        "steel-dark": "#8b949e",
      },
    },
  },
  plugins: [],
};
