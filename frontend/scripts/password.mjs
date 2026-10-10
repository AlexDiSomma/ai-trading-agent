/**
 * Impronta della password (scrypt). Formato: scrypt:N:r:p:sale:impronta (base64).
 *
 * Separatore `:` e non `$`: Next.js, leggendo `.env.local`, interpreta `$` come
 * riferimento a variabile e altererebbe l'impronta (trovato dal test del login).
 * Usato dallo script di impostazione e dalla route di login: una sola definizione.
 */

import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

const N = 16384;
const R = 8;
const P = 1;
const LUNGHEZZA = 64;

export function impronta(password) {
  const sale = randomBytes(16);
  const h = scryptSync(password, sale, LUNGHEZZA, { N, r: R, p: P });
  return ["scrypt", N, R, P, sale.toString("base64"), h.toString("base64")].join(":");
}

export function confronta(password, memorizzata) {
  const parti = String(memorizzata || "").split(":");
  if (parti.length !== 6 || parti[0] !== "scrypt") return false;
  const [, n, r, p, sale, attesa] = parti;
  const atteso = Buffer.from(attesa, "base64");
  const h = scryptSync(password, Buffer.from(sale, "base64"), atteso.length, {
    N: Number(n),
    r: Number(r),
    p: Number(p),
  });
  return h.length === atteso.length && timingSafeEqual(h, atteso);
}
