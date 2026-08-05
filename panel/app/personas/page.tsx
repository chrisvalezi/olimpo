"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Plus, User, Link2, Brain, Search } from "lucide-react";
import { api, mutate } from "@/lib/api";
import { PLATAFORMA, STATUS } from "@/lib/contas";

/**
 * Personas.
 *
 * A identidade, separada do login. A Luna Prado existe mesmo que nenhuma
 * conta exista — e o Instagram e o WhatsApp dela são a MESMA personagem,
 * com uma memória só.
 */
export default function Personas() {
  const [q, setQ] = useState("");
  const [nova, setNova] = useState(false);
  const { data, mutate: reload } = useSWR(
    `/v1/personas${q ? `?q=${encodeURIComponent(q)}` : ""}`, api,
  );
  const personas = data?.personas ?? [];

  return (
    <div className="space-y-5">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Personas</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            A identidade por trás das contas: voz, história, estética e memória.
            Uma persona pode ter vários logins — o Instagram e o WhatsApp da
            mesma personagem.
          </p>
        </div>
        <button onClick={() => setNova((v) => !v)} className="btn-primary">
          <Plus size={15} /> Nova persona
        </button>
      </header>

      {nova && <FormPersona onDone={() => { setNova(false); reload(); }} />}

      <div className="relative">
        <Search size={14} className="absolute left-2.5 top-2.5 text-slate-600" />
        <input value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="buscar persona…" className="input !mt-0 pl-8" />
      </div>

      {personas.length === 0 && !nova && (
        <div className="card text-center text-sm text-slate-500">
          Nenhuma persona. Crie uma e vincule os logins existentes a ela.
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        {personas.map((p: any) => <CardPersona key={p.id} persona={p} />)}
      </div>
    </div>
  );
}

function CardPersona({ persona }: { persona: any }) {
  const contas = persona.accounts ?? [];
  const card = persona.persona_card ?? {};
  const nicho = card?.niche?.primary;
  const semCard = Object.keys(card).length === 0;

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <User size={14} className="shrink-0 text-slate-500" />
            <span className="truncate font-medium text-slate-100">{persona.name}</span>
            <span className="font-mono text-xs text-slate-600">{persona.slug}</span>
          </div>
          <div className="mt-0.5 text-xs text-slate-500">
            {nicho && <span>{nicho} · </span>}
            {persona.memories} memórias
            {persona.arc && <span> · arco: {persona.arc}</span>}
          </div>
        </div>
        <Link href={`/personas/${persona.id}`} className="btn-ghost shrink-0">
          editar
        </Link>
      </div>

      {semCard && (
        <p className="rounded border border-warn/30 bg-warn/5 px-2 py-1 text-xs text-amber-200">
          Sem persona card. Sem ele a LLM não tem voz para escrever — a conta
          não deve sair de L0.
        </p>
      )}

      <div className="space-y-1 border-t border-surface-border pt-2">
        {contas.length === 0 ? (
          <span className="flex items-center gap-1 text-xs text-slate-600">
            <Link2 size={11} /> nenhum login vinculado
          </span>
        ) : (
          contas.map((c: any) => {
            const plat = PLATAFORMA[c.platform] ?? { cor: "", rotulo: c.platform };
            return (
              <Link key={c.id} href={`/contas/${c.id}`}
                className="flex items-center gap-1.5 text-xs hover:underline">
                <span className={`rounded border px-1 py-0.5 text-[10px] ${plat.cor}`}>
                  {plat.rotulo}
                </span>
                <span className="truncate text-slate-300">{c.handle}</span>
                <span className="text-slate-600">
                  {STATUS[c.status]?.rotulo ?? c.status}
                </span>
                <span className="ml-auto font-mono text-slate-600">{c.followers}</span>
              </Link>
            );
          })
        )}
      </div>
    </div>
  );
}

function FormPersona({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ name: "", description: "", persona_engine_id: "" });
  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);

  async function salvar() {
    setSalvando(true); setErro(null);
    try {
      await mutate("/v1/personas", "POST", { ...f, persona_card: {}, labels: [] });
      onDone();
    } catch (e) { setErro(String(e)); setSalvando(false); }
  }

  return (
    <div className="card space-y-3">
      <span className="label">Nova persona</span>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="label">Nome</span>
          <input className="input" placeholder="Luna Prado" value={f.name}
            onChange={(e) => setF({ ...f, name: e.target.value })} />
        </label>
        <label className="block">
          <span className="label">Descrição</span>
          <input className="input" placeholder="personal trainer, Floripa"
            value={f.description}
            onChange={(e) => setF({ ...f, description: e.target.value })} />
        </label>
        <label className="block">
          <span className="label">ID na máquina de influencers</span>
          <input className="input" placeholder="opcional"
            value={f.persona_engine_id}
            onChange={(e) => setF({ ...f, persona_engine_id: e.target.value })} />
        </label>
      </div>
      <p className="flex items-start gap-1.5 text-xs text-slate-600">
        <Brain size={12} className="mt-0.5 shrink-0" />
        A voz, a estética e os tabus ficam no persona card, editado depois de
        criar. Sem card a conta não sai de L0.
      </p>
      {erro && <p className="font-mono text-xs text-red-300">{erro}</p>}
      <div className="flex gap-2">
        <button onClick={salvar} disabled={salvando || !f.name} className="btn-primary">
          {salvando ? "salvando…" : "Criar"}
        </button>
        <button onClick={onDone} className="btn-ghost">Cancelar</button>
      </div>
    </div>
  );
}
