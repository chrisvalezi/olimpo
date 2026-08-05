"use client";

import useSWR from "swr";
import { AlertTriangle, CheckSquare, Send, Users } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Sala de controle.
 *
 * Prioridade da tela, de cima para baixo (docs/10-painel.md):
 *   1. o que precisa da minha atenção AGORA
 *   2. o que o sistema está fazendo neste momento
 *   3. por que ele decidiu isso
 */
export default function Overview() {
  const { data: health } = useSWR("/health", api, { refreshInterval: 10_000 });

  const dryRun = health && health.dispatch_enabled === false;
  const killed = health?.kill_switch === true;

  return (
    <div className="space-y-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Visão geral</h1>
        <span className="text-xs text-slate-500">
          {health ? `ambiente: ${health.env}` : "conectando…"}
        </span>
      </header>

      {/* Estados que mudam o significado de tudo na tela ficam no topo. */}
      {killed && (
        <Banner tone="kill" icon={AlertTriangle}>
          <strong>Kill switch global acionado.</strong> Nenhum agente está
          operando. Ver <code>docs/runbooks/kill-switch.md</code>.
        </Banner>
      )}
      {dryRun && !killed && (
        <Banner tone="warn" icon={AlertTriangle}>
          <strong>Modo dry-run.</strong> Nada é enviado ao orchestrator — as
          ações são gravadas em disco. Ligue <code>DISPATCH_ENABLED</code> para
          operar em contas reais.
        </Banner>
      )}

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Contas ativas" value="—" icon={Users} />
        <Stat label="Aguardando aprovação" value="—" icon={CheckSquare} />
        <Stat label="Tasks em voo" value="—" icon={Send} />
        <Stat label="Violações hoje" value="—" icon={AlertTriangle} />
      </section>

      <section className="card">
        <h2 className="label mb-3">Atividade ao vivo</h2>
        <p className="text-sm text-slate-500">
          O feed de eventos entra na Fase 1, via SSE. Ver{" "}
          <code>docs/fases/fase-1-primeira-influencer.md</code>.
        </p>
      </section>
    </div>
  );
}

function Stat({
  label, value, icon: Icon,
}: {
  label: string; value: string; icon: React.ElementType;
}) {
  return (
    <div className="card">
      <div className="mb-2 flex items-center justify-between">
        <span className="label">{label}</span>
        <Icon size={14} className="text-slate-600" strokeWidth={1.75} />
      </div>
      <div className="metric">{value}</div>
    </div>
  );
}

function Banner({
  tone, icon: Icon, children,
}: {
  tone: "warn" | "kill"; icon: React.ElementType; children: React.ReactNode;
}) {
  const tones = {
    warn: "border-warn/40 bg-warn/10 text-amber-200",
    kill: "border-kill/40 bg-kill/10 text-purple-200",
  };
  return (
    <div className={`flex items-start gap-2.5 rounded-lg border p-3 text-sm ${tones[tone]}`}>
      <Icon size={16} className="mt-0.5 shrink-0" strokeWidth={2} />
      <div>{children}</div>
    </div>
  );
}
