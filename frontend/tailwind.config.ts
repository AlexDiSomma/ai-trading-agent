import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Colori tramite variabili CSS: il tema scuro li inverte in globals.css.
        white: "rgb(var(--white) / <alpha-value>)",
        zinc: { 50: "rgb(var(--zinc-50) / <alpha-value>)", 100: "rgb(var(--zinc-100) / <alpha-value>)", 200: "rgb(var(--zinc-200) / <alpha-value>)", 300: "rgb(var(--zinc-300) / <alpha-value>)", 400: "rgb(var(--zinc-400) / <alpha-value>)", 500: "rgb(var(--zinc-500) / <alpha-value>)", 600: "rgb(var(--zinc-600) / <alpha-value>)", 700: "rgb(var(--zinc-700) / <alpha-value>)", 800: "rgb(var(--zinc-800) / <alpha-value>)", 900: "rgb(var(--zinc-900) / <alpha-value>)", 950: "rgb(var(--zinc-950) / <alpha-value>)" },
        emerald: { 50: "rgb(var(--emerald-50) / <alpha-value>)", 100: "rgb(var(--emerald-100) / <alpha-value>)", 200: "rgb(var(--emerald-200) / <alpha-value>)", 300: "rgb(var(--emerald-300) / <alpha-value>)", 400: "rgb(var(--emerald-400) / <alpha-value>)", 500: "rgb(var(--emerald-500) / <alpha-value>)", 600: "rgb(var(--emerald-600) / <alpha-value>)", 700: "rgb(var(--emerald-700) / <alpha-value>)", 800: "rgb(var(--emerald-800) / <alpha-value>)", 900: "rgb(var(--emerald-900) / <alpha-value>)", 950: "rgb(var(--emerald-950) / <alpha-value>)" },
        rose: { 50: "rgb(var(--rose-50) / <alpha-value>)", 100: "rgb(var(--rose-100) / <alpha-value>)", 200: "rgb(var(--rose-200) / <alpha-value>)", 300: "rgb(var(--rose-300) / <alpha-value>)", 400: "rgb(var(--rose-400) / <alpha-value>)", 500: "rgb(var(--rose-500) / <alpha-value>)", 600: "rgb(var(--rose-600) / <alpha-value>)", 700: "rgb(var(--rose-700) / <alpha-value>)", 800: "rgb(var(--rose-800) / <alpha-value>)", 900: "rgb(var(--rose-900) / <alpha-value>)", 950: "rgb(var(--rose-950) / <alpha-value>)" },
        amber: { 50: "rgb(var(--amber-50) / <alpha-value>)", 100: "rgb(var(--amber-100) / <alpha-value>)", 200: "rgb(var(--amber-200) / <alpha-value>)", 300: "rgb(var(--amber-300) / <alpha-value>)", 400: "rgb(var(--amber-400) / <alpha-value>)", 500: "rgb(var(--amber-500) / <alpha-value>)", 600: "rgb(var(--amber-600) / <alpha-value>)", 700: "rgb(var(--amber-700) / <alpha-value>)", 800: "rgb(var(--amber-800) / <alpha-value>)", 900: "rgb(var(--amber-900) / <alpha-value>)", 950: "rgb(var(--amber-950) / <alpha-value>)" },
        sky: { 50: "rgb(var(--sky-50) / <alpha-value>)", 100: "rgb(var(--sky-100) / <alpha-value>)", 200: "rgb(var(--sky-200) / <alpha-value>)", 300: "rgb(var(--sky-300) / <alpha-value>)", 400: "rgb(var(--sky-400) / <alpha-value>)", 500: "rgb(var(--sky-500) / <alpha-value>)", 600: "rgb(var(--sky-600) / <alpha-value>)", 700: "rgb(var(--sky-700) / <alpha-value>)", 800: "rgb(var(--sky-800) / <alpha-value>)", 900: "rgb(var(--sky-900) / <alpha-value>)", 950: "rgb(var(--sky-950) / <alpha-value>)" },
        violet: { 50: "rgb(var(--violet-50) / <alpha-value>)", 100: "rgb(var(--violet-100) / <alpha-value>)", 200: "rgb(var(--violet-200) / <alpha-value>)", 300: "rgb(var(--violet-300) / <alpha-value>)", 400: "rgb(var(--violet-400) / <alpha-value>)", 500: "rgb(var(--violet-500) / <alpha-value>)", 600: "rgb(var(--violet-600) / <alpha-value>)", 700: "rgb(var(--violet-700) / <alpha-value>)", 800: "rgb(var(--violet-800) / <alpha-value>)", 900: "rgb(var(--violet-900) / <alpha-value>)", 950: "rgb(var(--violet-950) / <alpha-value>)" },
        green: { 50: "rgb(var(--green-50) / <alpha-value>)", 100: "rgb(var(--green-100) / <alpha-value>)", 200: "rgb(var(--green-200) / <alpha-value>)", 300: "rgb(var(--green-300) / <alpha-value>)", 400: "rgb(var(--green-400) / <alpha-value>)", 500: "rgb(var(--green-500) / <alpha-value>)", 600: "rgb(var(--green-600) / <alpha-value>)", 700: "rgb(var(--green-700) / <alpha-value>)", 800: "rgb(var(--green-800) / <alpha-value>)", 900: "rgb(var(--green-900) / <alpha-value>)", 950: "rgb(var(--green-950) / <alpha-value>)" },
        red: { 50: "rgb(var(--red-50) / <alpha-value>)", 100: "rgb(var(--red-100) / <alpha-value>)", 200: "rgb(var(--red-200) / <alpha-value>)", 300: "rgb(var(--red-300) / <alpha-value>)", 400: "rgb(var(--red-400) / <alpha-value>)", 500: "rgb(var(--red-500) / <alpha-value>)", 600: "rgb(var(--red-600) / <alpha-value>)", 700: "rgb(var(--red-700) / <alpha-value>)", 800: "rgb(var(--red-800) / <alpha-value>)", 900: "rgb(var(--red-900) / <alpha-value>)", 950: "rgb(var(--red-950) / <alpha-value>)" },
        blue: { 50: "rgb(var(--blue-50) / <alpha-value>)", 100: "rgb(var(--blue-100) / <alpha-value>)", 200: "rgb(var(--blue-200) / <alpha-value>)", 300: "rgb(var(--blue-300) / <alpha-value>)", 400: "rgb(var(--blue-400) / <alpha-value>)", 500: "rgb(var(--blue-500) / <alpha-value>)", 600: "rgb(var(--blue-600) / <alpha-value>)", 700: "rgb(var(--blue-700) / <alpha-value>)", 800: "rgb(var(--blue-800) / <alpha-value>)", 900: "rgb(var(--blue-900) / <alpha-value>)", 950: "rgb(var(--blue-950) / <alpha-value>)" },
        // Semantica degli stati della pagina System Health.
        sano: "#15803d",
        degradato: "#b45309",
        guasto: "#b91c1c",
        ignoto: "#52525b",
      },
    },
  },
  plugins: [],
};

export default config;
