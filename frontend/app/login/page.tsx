/** Pagina di accesso: copre la navigazione, che senza sessione non serve. */

import { ModuloLogin } from "@/components/ModuloLogin";
import { configurata } from "@/lib/sessione";

export const dynamic = "force-dynamic";

export default async function Login({ searchParams }: { searchParams: Promise<{ da?: string }> }) {
  const { da } = await searchParams;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center overflow-y-auto bg-zinc-50 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon.svg" alt="" className="h-12 w-12" />
          <h1 className="mt-3 text-lg font-semibold text-zinc-900">AI Trading Agent</h1>
          <p className="text-sm text-zinc-500">Accedi per vedere e guidare il sistema</p>
        </div>
        <div className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">
          <ModuloLogin destinazione={da ?? "/"} configurata={configurata()} />
        </div>
        <p className="mt-4 text-center text-xs text-zinc-400">Modalità PAPER · nessun ordine reale</p>
      </div>
    </div>
  );
}
