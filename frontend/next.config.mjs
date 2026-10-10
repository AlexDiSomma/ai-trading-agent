import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Radice esplicita: in BroCloud c'è un altro package-lock.json e Next, deducendo
  // la radice, sceglieva quella cartella (avviso a ogni build e avvio).
  outputFileTracingRoot: dirname(fileURLToPath(import.meta.url)),
  // Il token dell'API e l'indirizzo del backend restano variabili d'ambiente.
  // Le credenziali MT5 non arrivano MAI qui: il frontend non parla con il
  // broker, parla soltanto con il backend.
  env: {},
};

export default nextConfig;
