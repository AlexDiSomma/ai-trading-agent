/**
 * Imposta nome utente e password della dashboard: `npm run imposta-password`.
 *
 * La password si digita senza che compaia a schermo e non viene mai salvata: in
 * `.env.local` finisce solo la sua impronta (scrypt). Il segreto delle sessioni viene
 * rigenerato a ogni esecuzione, quindi cambiare password chiude tutte le sessioni.
 */

import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { stdin, stdout } from "node:process";
import { fileURLToPath } from "node:url";

import { impronta } from "./password.mjs";

const FILE = join(dirname(fileURLToPath(import.meta.url)), "..", ".env.local");

function chiedi(domanda, nascosta = false) {
  return new Promise((risolvi) => {
    stdout.write(domanda);
    let valore = "";
    if (!nascosta) {
      stdin.resume();
      stdin.setEncoding("utf8");
      stdin.once("data", (d) => {
        stdin.pause();
        risolvi(String(d).trim());
      });
      return;
    }
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding("utf8");
    const leggi = (c) => {
      if (c === "\r" || c === "\n" || c === "\u0004") {
        stdin.setRawMode(false);
        stdin.pause();
        stdin.removeListener("data", leggi);
        stdout.write("\n");
        risolvi(valore);
      } else if (c === "\u0003") {
        stdout.write("\nAnnullato.\n");
        process.exit(1);
      } else if (c === "\u007f" || c === "\b") {
        if (valore.length) {
          valore = valore.slice(0, -1);
          stdout.write("\b \b");
        }
      } else {
        valore += c;
        stdout.write("•".repeat(c.length));
      }
    };
    stdin.on("data", leggi);
  });
}

function scrivi(voci) {
  let righe = existsSync(FILE) ? readFileSync(FILE, "utf8").split(/\r?\n/) : [];
  for (const [chiave, valore] of Object.entries(voci)) {
    const riga = `${chiave}=${valore}`;
    const i = righe.findIndex((r) => r.startsWith(`${chiave}=`));
    if (i >= 0) righe[i] = riga;
    else righe.push(riga);
  }
  righe = righe.filter((r, i, tutte) => r !== "" || i < tutte.length - 1);
  writeFileSync(FILE, righe.join("\n") + "\n", { encoding: "utf8" });
}

async function main() {
  console.log("\nAccesso alla dashboard AI Trading Agent\n");
  const utente = await chiedi("Nome utente: ");
  if (!/^[A-Za-z0-9._@-]{3,40}$/.test(utente)) {
    console.error("Nome utente non valido: da 3 a 40 caratteri fra lettere, numeri e . _ @ -");
    process.exit(1);
  }
  const password = await chiedi("Password (almeno 10 caratteri): ", true);
  if (password.length < 10) {
    console.error("Password troppo corta: servono almeno 10 caratteri.");
    process.exit(1);
  }
  const conferma = await chiedi("Ripeti la password: ", true);
  if (conferma !== password) {
    console.error("Le due password non coincidono. Nulla è stato cambiato.");
    process.exit(1);
  }
  scrivi({
    DASHBOARD_USER: utente,
    DASHBOARD_PASSWORD_HASH: impronta(password),
    SESSION_SECRET: randomBytes(32).toString("hex"),
  });
  console.log("\nFatto. Credenziali salvate (solo l'impronta della password) in frontend/.env.local.");
  console.log("Riavvia la dashboard perché le legga. Le sessioni aperte sono state chiuse.\n");
}

main();
