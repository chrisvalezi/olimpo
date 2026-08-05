import type { Config } from "tailwindcss";

// Paleta com significado semântico — cor comunica estado, não decora.
// Ver docs/10-painel.md (design system).
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0b0d10", // fundo da sala de controle
          raised: "#14181d",
          border: "#232a32",
        },
        // status de conta
        active: "#22c55e",
        warming: "#f59e0b",
        paused: "#64748b",
        suspended: "#ef4444",
        // severidade de alerta
        info: "#38bdf8",
        warn: "#f59e0b",
        block: "#ef4444",
        kill: "#a855f7",
        // níveis de autonomia (L0 → L3)
        l0: "#64748b",
        l1: "#38bdf8",
        l2: "#22c55e",
        l3: "#a855f7",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
