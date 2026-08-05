"use client";

import Link from "next/link";
import { Link2 } from "lucide-react";
import { PLATAFORMA, STATUS } from "@/lib/contas";

/**
 * Os logins de plataforma vinculados a esta persona.
 *
 * A persona é a IDENTIDADE; a conta é o login numa plataforma. Uma persona
 * pode ter Instagram, WhatsApp e TikTok — e vincular não acontece aqui, e
 * sim na ficha da conta, porque é lá que se sabe qual login é qual.
 */
export function PersonaLogins({ contas }: { contas: any[] }) {
  if (contas.length === 0) {
    return (
      <section className="card">
        <p className="flex items-center gap-1.5 text-sm text-slate-500">
          <Link2 size={13} /> nenhum login vinculado. Vincule pela ficha da
          conta, ou em lote na lista de contas.
        </p>
      </section>
    );
  }

  return (
    <section className="card space-y-2">
      {contas.map((c) => {
        const plat = PLATAFORMA[c.platform] ?? { cor: "", rotulo: c.platform };
        return (
          <Link key={c.id} href={`/contas/${c.id}`}
            className="flex items-center gap-2 rounded border border-surface-border bg-surface/50 px-2.5 py-1.5 text-xs hover:bg-surface-border/30">
            <span className={`rounded border px-1 py-0.5 text-[10px] ${plat.cor}`}>
              {plat.rotulo}
            </span>
            <span className="text-slate-300">{c.handle}</span>
            <span className="text-slate-600">{STATUS[c.status]?.rotulo}</span>
            <span className="text-slate-600">{c.autonomy}</span>
            <span className="ml-auto font-mono text-slate-500">{c.followers} seg.</span>
          </Link>
        );
      })}
    </section>
  );
}
