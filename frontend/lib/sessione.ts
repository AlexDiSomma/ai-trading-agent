/**
 * Sessione della dashboard: cookie firmato con HMAC-SHA256 (Web Crypto, quindi
 * utilizzabile sia nel middleware sia nelle route).
 *
 * Il cookie contiene solo utente e scadenza, firmati con `SESSION_SECRET`. Cambiare
 * la password rigenera il segreto: tutte le sessioni aperte decadono.
 */

export const COOKIE = "ata_sessione";
export const DURATA_ORE = 12;
export const DURATA_RICORDAMI_GIORNI = 30;

const codifica = new TextEncoder();

function b64url(dati: ArrayBuffer | Uint8Array): string {
  const byte = dati instanceof Uint8Array ? dati : new Uint8Array(dati);
  let s = "";
  for (const b of byte) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function daB64url(s: string): Uint8Array<ArrayBuffer> {
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  const byte = new Uint8Array(new ArrayBuffer(b.length));
  for (let i = 0; i < b.length; i++) byte[i] = b.charCodeAt(i);
  return byte;
}

async function chiave(segreto: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", codifica.encode(segreto), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export function configurata(): boolean {
  return Boolean(process.env.SESSION_SECRET && process.env.DASHBOARD_USER && process.env.DASHBOARD_PASSWORD_HASH);
}

export async function firma(utente: string, durataSecondi: number): Promise<string> {
  const segreto = process.env.SESSION_SECRET;
  if (!segreto) throw new Error("SESSION_SECRET mancante");
  const corpo = b64url(codifica.encode(JSON.stringify({ u: utente, exp: Math.floor(Date.now() / 1000) + durataSecondi })));
  const sig = await crypto.subtle.sign("HMAC", await chiave(segreto), codifica.encode(corpo));
  return `${corpo}.${b64url(sig)}`;
}

/** Utente della sessione se il cookie è valido e non scaduto, altrimenti null. */
export async function verifica(valore: string | undefined): Promise<string | null> {
  const segreto = process.env.SESSION_SECRET;
  if (!valore || !segreto) return null;
  const [corpo, sig] = valore.split(".");
  if (!corpo || !sig) return null;
  try {
    const ok = await crypto.subtle.verify("HMAC", await chiave(segreto), daB64url(sig), codifica.encode(corpo));
    if (!ok) return null;
    const dati = JSON.parse(new TextDecoder().decode(daB64url(corpo))) as { u?: string; exp?: number };
    if (!dati.u || !dati.exp || dati.exp < Date.now() / 1000) return null;
    if (dati.u !== process.env.DASHBOARD_USER) return null;
    return dati.u;
  } catch {
    return null;
  }
}

/** Le richieste che modificano qualcosa devono venire dalla dashboard stessa. */
export function stessaOrigine(request: Request): boolean {
  const origine = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origine || !host) return false;
  try {
    return new URL(origine).host === host;
  } catch {
    return false;
  }
}
