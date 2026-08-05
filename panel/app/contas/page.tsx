"use client";

import { useState } from "react";
import useSWR from "swr";
import { Users, Server, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";

const AUTONOMY: Record<string, { cor: string; nome: string }> = {
  L0: { cor: "text-l0 border-l0/40 bg-l0/10", nome: "estagiário" },
  L1: { cor: "text-l1 border-l1/40 bg-l1/10", nome: "júnior" },
  L2: { cor: "text-l2 border-l2/40 bg-l2/10", nome: "pleno" },
  L3: { cor: "text-l3 border-l3/40 bg-l3/10", nome: "sênior" },
};

// A plataforma decide o que a conta PODE fazer. WhatsApp não tem feed nem
// seguidor; deixar isso implícito na tela é como o erro de classe começa.
const PLATAFORMA: Record<string, { cor: string; rotulo: string }> = {
  instagram: { cor: "border-pink-500/40 bg-pink-500/10 text-pink-300", rotulo: "IG" },
  whatsapp: { cor: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", rotulo: "WA" },
  tiktok: { cor: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300", rotulo: "TT" },
};

const ARQUETIPO: Record<string, string> = {
  influencer: "influencer", gossip: "fofoca", clipper: "clipagem",
  messaging: "mensageria", collector: "coletora",
};

const STATUS: Record<string, string> = {
  active: "text-active", warming: "text-warming", paused: "text-paused",
  suspended: "text-suspended", draft: "text-slate-500", lost: "text-suspended",
};

export default function Contas() {
  const [filtro, setFiltro] = useState<string>("");
  const { data } = useSWR(
    `/v1/accounts${filtro ? `?platform=${filtro}` : ""}`, api,
    { refreshInterval: 20_000 },
  );

  const contas = data?.accounts ?? [];

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Contas</h1>
        <div className="flex gap-1">
          {[["", "todas"], ["instagram", "Instagram"], ["whatsapp", "WhatsApp"], ["tiktok", "TikTok"]].map(
            ([v, label]) => (
              <button
                key={v}
                onClick={() => setFiltro(v)}
                className={`rounded-md px-2.5 py-1 text-xs ${
                  filtro === v
                    ? "bg-surface-border/60 text-slate-100"
                    : "text-slate-500 hover:text-slate-300"
                }`}
              >
                {label}
              </button>
            ),
          )}
        </div>
      </header>

      {contas.length === 0 && (
        <div className="card text-center text-sm text-slate-500">
          Nenhuma conta. Rode <code className="text-slate-400">make seed</code> ou
          sincronize um servidor em <strong className="text-slate-300">Servidores</strong>.
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {contas.map((c: any) => (
          <ContaCard key={c.id} conta={c} />
        ))}
      </div>
    </div>
  );
}

function ContaCard({ conta }: { conta: any }) {
  const aut = AUTONOMY[conta.autonomy] ?? AUTONOMY.L0;
  const plat = PLATAFORMA[conta.platform] ?? {
    cor: "border-slate-600 bg-slate-700/30 text-slate-400", rotulo: conta.platform,
  };
  // Mensageria e coletora não rodam o loop de conteúdo — não têm arco.
  const rodaLoop = !["messaging", "collector"].includes(conta.archetype);
  const placements = conta.placements ?? [];
  // Sem placement, o Olimpo não sabe em qual device agir — a conta não opera.
  const semDevice = placements.length === 0;
  // A mesma conta logada em nós diferentes roda em paralelo: o nó serializa
  // por device, serializar por conta é obrigação nossa.
  const multiNo = new Set(placements.map((p: any) => p.node)).size > 1;

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className={`rounded border px-1 py-0.5 text-[10px] font-medium ${plat.cor}`}>
              {plat.rotulo}
            </span>
            <span className="truncate font-medium text-slate-100">{conta.handle}</span>
          </div>
          <div className="mt-0.5 text-xs text-slate-500">
            {ARQUETIPO[conta.archetype] ?? conta.archetype}
            {conta.persona_name && ` · ${conta.persona_name}`}
            {conta.niche && ` · ${conta.niche}`}
          </div>
        </div>
        <span className={`rounded border px-1.5 py-0.5 text-xs ${aut.cor}`} title={aut.nome}>
          {conta.autonomy}
        </span>
      </div>

      {rodaLoop && conta.arc && (
        <div className="rounded border border-surface-border bg-surface/60 p-2 text-xs text-slate-400">
          <span className="text-slate-600">arco atual · </span>
          {conta.arc}
        </div>
      )}

      <div className="flex items-center gap-3 text-xs">
        <span className={STATUS[conta.status] ?? "text-slate-500"}>● {conta.status}</span>
        {rodaLoop && <span className="text-slate-500">{conta.followers} seguidores</span>}
        {conta.tasks_open > 0 && (
          <span className="text-info">{conta.tasks_open} em voo</span>
        )}
      </div>

      <div className="space-y-1 border-t border-surface-border pt-2">
        {semDevice ? (
          <div className="flex items-center gap-1.5 text-xs text-warn">
            <AlertTriangle size={12} /> sem device — não opera
          </div>
        ) : (
          placements.map((p: any, i: number) => (
            <div key={i} className="flex items-center gap-1.5 text-xs text-slate-500">
              <Server size={11} />
              <span className="text-slate-400">{p.node}</span>
              <span className="font-mono">{p.container}</span>
              {!p.logged_in && <span className="text-warn">deslogada</span>}
            </div>
          ))
        )}
        {multiNo && (
          <div className="flex items-center gap-1.5 text-xs text-warn">
            <AlertTriangle size={12} /> em mais de um servidor — exige serialização
          </div>
        )}
      </div>
    </div>
  );
}
