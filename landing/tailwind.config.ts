import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#050F1C",
          900: "#040E1A",
          800: "#081D33",
          700: "#0A2540",
          600: "#1B355A",
          400: "#3F5570",
          300: "#6D7F95",
        },
        gold: {
          300: "#E3BC54",
          400: "#DBAF34",
          500: "#D9A21B",
          600: "#B7791F",
        },
        teal: { 500: "#14B8A6", 300: "#4FD3BF" },
        mist: "#EAF2FF",
      },
      fontFamily: {
        display: ["var(--font-montserrat)", "system-ui", "sans-serif"],
        serif: ["var(--font-fraunces)", "Georgia", "serif"],
        mono: ["var(--font-jetbrains)", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
