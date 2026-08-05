"use client";

import { useState, use, useEffect } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ArrowLeft, Save, Trash2, Pin, Plus, Server, AlertTriangle, KeyRound,
  ShieldAlert, History,
} from "lucide-react";
import { api, mutate } from "@/lib/api";
import {
  STATUS, PRESENCA, AUTONOMIA, PLATAFORMA, ARQUETIPO, PAPEL,
  CAMPOS_PERFIL, tempoRelativo,
} from "@/lib/contas";

/** Ficha completa de uma conta: identidade, vínculo, dados operacionais,
 *  onde ela roda, observações datadas e histórico. */
export default function FichaDaConta({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, mutate: reload } = useSWR(`/v1/accounts/${id}`, api);
  const { data: personasData } = useSWR("/v1/personas", api);

  const [form, setForm] = useState<any>(null);
  const [perfil, setPerfil] = useState<Record<string, string>>({});
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const conta = data?.account;

  useEffect(() => {
    if (!conta) return;
    setForm({
      handle: conta.handle, archetype: conta.archetype, role: conta.role,
      status: conta.status, autonomy: conta.autonomy,
      persona_id: conta.persona_id ?? "", external_id: conta.external_id ?? "",
      jid: conta.jid ?? "", timezone: conta.timezone ?? "",
      secret_ref: conta.secret_ref ?? "", notes: conta.notes ?? "",
      labels: (conta.labels ?? []).join(", "),
    });
    setPerfil(conta.profile ?? {});
  }, [conta?.id, conta?.updated_at]);

  if (!conta || !form) {
    return <p className="text-sm text-slate-500">carregando…</p>;
  }

  const plat = PLATAFORMA[conta.platform] ?? { cor: "", rotulo: conta.platform };
  const pr = PRESENCA[conta.presence] ?? { rotulo: conta.presence, cor: "", ajuda: "" };

  async function salvar() {
    setSalvando(true); setErro(null);
    try {
      const { labels, persona_id, ...resto } = form;
      await mutate(`/v1/accounts/${id}`, "PATCH", {
        ...resto,
        labels: labels.split(",").map((x: string) => x.trim()).filter(Boolean),
        persona_id: persona_id || null,
        unbind_persona: !persona_id && !!conta.persona_id,
        profile: perfil,
      });
      await reload();
    } catch (e) { setErro(String(e)); } finally { setSalvando(false); }
  }

  return (
    <div className="space-y-5">
      <Link href="/contas" className="flex w-fit items-center gap-1 text-xs text-slate-500 hover:text-slate-300">
        <ArrowLeft size={12} /> contas
      </Link>

      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className={`rounded border px-1.5 py-0.5 text-xs font-medium ${plat.cor}`}>
              {plat.rotulo}
            </span>
            <h1 className="text-xl font-semibold text-slate-100">{conta.handle}</h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-slate-500">
            <span className={pr.cor} title={pr.ajuda}>● {pr.rotulo}</span>
            {conta.external_id && <span className="font-mono">id {conta.external_id}</span>}
            <span>criada {tempoRelativo(conta.created_at)}</span>
            {conta.last_synced_at && <span>sincronizada {tempoRelativo(conta.last_synced_at)}</span>}
          </div>
          {conta.previous_handles?.length > 0 && (
            <p className="mt-1 flex items-center gap-1 text-xs text-slate-600">
              <History size={11} /> já se chamou: {conta.previous_handles.join(", ")}
            </p>
          )}
        </div>
        <button onClick={salvar} disabled={salvando} className="btn-primary">
          <Save size={14} /> {salvando ? "salvando…" : "Salvar"}
        </button>
      </header>

      {erro && <p className="font-mono text-xs text-red-300">{erro}</p>}

      {conta.presence === "missing" && (
        <div className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-amber-200/90">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div>
            Esta conta não aparece no inventário há mais de um dia.{" "}
            <strong>Ela não foi perdida</strong> — o cadastro, a persona e o
            histórico continuam aqui. O que sumiu foi o login dentro do device.
            Se ela foi banida de verdade, marque a situação como{" "}
            <em>perdida</em> em vez de apagar.
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* ------------------------------------------------------ identidade */}
        <section className="card space-y-3 lg:col-span-2">
          <span className="label">Identidade e operação</span>
          <div className="grid gap-3 sm:grid-cols-2">
            <Campo rotulo="Handle">
              <input className="input" value={form.handle}
                onChange={(e) => setForm({ ...form, handle: e.target.value })} />
            </Campo>
            <Campo rotulo="Persona" dica="a identidade por trás deste login">
              <select className="input" value={form.persona_id}
                onChange={(e) => setForm({ ...form, persona_id: e.target.value })}>
                <option value="">— sem identidade vinculada —</option>
                {(personasData?.personas ?? []).map((p: any) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </Campo>
            <Campo rotulo="Situação" dica={STATUS[form.status]?.ajuda}>
              <select className="input" value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {Object.entries(STATUS).map(([k, v]) => (
                  <option key={k} value={k}>{v.rotulo}</option>
                ))}
              </select>
            </Campo>
            <Campo rotulo="Autonomia" dica={AUTONOMIA[form.autonomy]?.nome}>
              <select className="input" value={form.autonomy}
                onChange={(e) => setForm({ ...form, autonomy: e.target.value })}>
                {Object.keys(AUTONOMIA).map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </Campo>
            <Campo rotulo="Papel" dica={PAPEL[form.role]?.ajuda}>
              <select className="input" value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}>
                {Object.entries(PAPEL).map(([k, v]) => (
                  <option key={k} value={k}>{v.rotulo}</option>
                ))}
              </select>
            </Campo>
            <Campo rotulo="Arquétipo">
              <select className="input" value={form.archetype}
                onChange={(e) => setForm({ ...form, archetype: e.target.value })}>
                {Object.entries(ARQUETIPO).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </Campo>
            <Campo rotulo="ID na plataforma" dica="identificador estável — o handle muda, ele não">
              <input className="input font-mono" value={form.external_id}
                onChange={(e) => setForm({ ...form, external_id: e.target.value })} />
            </Campo>
            {conta.platform === "whatsapp" && (
              <Campo rotulo="Telefone / JID">
                <input className="input font-mono" value={form.jid}
                  onChange={(e) => setForm({ ...form, jid: e.target.value })} />
              </Campo>
            )}
            <Campo rotulo="Rótulos" dica="separados por vírgula — servem para filtrar em lote">
              <input className="input" value={form.labels}
                onChange={(e) => setForm({ ...form, labels: e.target.value })} />
            </Campo>
            <Campo rotulo="Fuso">
              <input className="input" value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
            </Campo>
          </div>
        </section>

        {/* --------------------------------------------------------- métricas */}
        <section className="card space-y-3">
          <span className="label">Inventário</span>
          <div className="grid grid-cols-3 gap-2 text-center">
            <Numero rotulo="seguidores" valor={conta.followers} />
            <Numero rotulo="seguindo" valor={conta.following} />
            <Numero rotulo="posts" valor={conta.media_count} />
          </div>
          <p className="text-xs text-slate-600">
            Vêm da varredura do orchestrator, não de digitação. Sincronizar um
            servidor atualiza estes números.
          </p>
        </section>
      </div>

      {/* ------------------------------------------------------------ perfil */}
      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <span className="label">Dados da conta</span>
          <span className="text-xs text-slate-600">
            campos livres — o que sua operação precisar
          </span>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {CAMPOS_PERFIL.map((c) => (
            <Campo key={c.chave} rotulo={c.rotulo} dica={c.dica}>
              <input className="input" type={c.tipo ?? "text"}
                value={perfil[c.chave] ?? ""}
                onChange={(e) => setPerfil({ ...perfil, [c.chave]: e.target.value })} />
            </Campo>
          ))}
        </div>

        <CamposExtras perfil={perfil} setPerfil={setPerfil} />

        {/* Guardar senha em coluna não cifrada é criar um alvo. O campo
            aponta para onde a credencial realmente vive. */}
        <div className="flex items-start gap-2 rounded border border-surface-border bg-surface/60 p-2.5">
          <KeyRound size={14} className="mt-0.5 shrink-0 text-slate-600" />
          <div className="flex-1 space-y-1">
            <Campo rotulo="Onde está a credencial"
              dica="referência ao cofre, não a senha em si — esta coluna não é cifrada">
              <input className="input" value={form.secret_ref}
                placeholder="bitwarden://item/xyz  ·  1password://…  ·  cofre físico gaveta 3"
                onChange={(e) => setForm({ ...form, secret_ref: e.target.value })} />
            </Campo>
          </div>
        </div>
      </section>

      {/* -------------------------------------------------------- placements */}
      <section className="card space-y-2">
        <span className="label">Onde esta conta roda</span>
        {data.placements.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nenhum device. A conta existe normalmente — só não tem onde operar
            até aparecer num inventário.
          </p>
        ) : (
          data.placements.map((p: any) => (
            <div key={p.id} className="flex flex-wrap items-center gap-2 rounded border border-surface-border bg-surface/50 px-2.5 py-2 text-xs">
              <Server size={12} className="text-slate-500" />
              <span className="text-slate-400">{p.server_name}</span>
              <span className="font-mono text-slate-500">{p.container}</span>
              {p.android_user != null && (
                <span className="font-mono text-slate-600">user {p.android_user}</span>
              )}
              <span className={p.logged_in ? "text-active" : "text-warn"}>
                {p.logged_in ? "logada" : "deslogada"}
              </span>
              {p.under_external_management && (
                <span className="flex items-center gap-1 text-warn">
                  <ShieldAlert size={11} /> sob watchdog
                </span>
              )}
              <span className="ml-auto text-slate-600">{tempoRelativo(p.last_seen_at)}</span>
            </div>
          ))
        )}
      </section>

      <Observacoes contaId={id} notas={data.notes} onChange={reload} />

      {/* ------------------------------------------------------- zona de risco */}
      <section className="card space-y-2 border-block/30">
        <span className="label text-block">Zona de risco</span>
        <p className="text-xs text-slate-500">
          Apagar remove memória, posts, tasks e aprovações em cascata. Conta
          banida deve virar <strong>perdida</strong> — o histórico é o que
          explica por que ela caiu.
        </p>
        <button
          onClick={async () => {
            if (prompt('Digite "apagar" para confirmar:') !== "apagar") return;
            await mutate(`/v1/accounts/${id}?confirm=apagar-de-verdade`, "DELETE");
            location.href = "/contas";
          }}
          className="btn-ghost w-fit !border-block/40 !text-block"
        >
          <Trash2 size={12} /> apagar conta e todo o histórico
        </button>
      </section>
    </div>
  );
}

function CamposExtras({ perfil, setPerfil }: {
  perfil: Record<string, string>; setPerfil: (p: Record<string, string>) => void;
}) {
  const conhecidos = new Set(CAMPOS_PERFIL.map((c) => c.chave));
  const extras = Object.keys(perfil).filter((k) => !conhecidos.has(k));

  return (
    <div className="space-y-2">
      {extras.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          {extras.map((k) => (
            <Campo key={k} rotulo={k}>
              <div className="flex gap-1">
                <input className="input" value={perfil[k] ?? ""}
                  onChange={(e) => setPerfil({ ...perfil, [k]: e.target.value })} />
                <button onClick={() => {
                  const p = { ...perfil }; delete p[k]; setPerfil(p);
                }} className="mt-1 text-slate-600 hover:text-block">
                  <Trash2 size={12} />
                </button>
              </div>
            </Campo>
          ))}
        </div>
      )}
      <button
        onClick={() => {
          const k = prompt("Nome do campo:");
          if (k) setPerfil({ ...perfil, [k.trim()]: "" });
        }}
        className="btn-ghost"
      >
        <Plus size={12} /> campo personalizado
      </button>
    </div>
  );
}

function Observacoes({ contaId, notas, onChange }: {
  contaId: string; notas: any[]; onChange: () => void;
}) {
  const [texto, setTexto] = useState("");
  const [fixar, setFixar] = useState(false);

  async function adicionar() {
    if (!texto.trim()) return;
    await mutate(`/v1/accounts/${contaId}/notes`, "POST", {
      body: texto, pinned: fixar, author: "painel",
    });
    setTexto(""); setFixar(false); onChange();
  }

  return (
    <section className="card space-y-3">
      <span className="label">Observações</span>
      <p className="text-xs text-slate-600">
        Datadas e preservadas. Diferente do campo de anotação livre acima, aqui
        nada é sobrescrito — é o histórico da conta.
      </p>

      <div className="flex gap-2">
        <input className="input !mt-0 flex-1" value={texto}
          placeholder="o que aconteceu com esta conta…"
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && adicionar()} />
        <label className="flex shrink-0 items-center gap-1 text-xs text-slate-500">
          <input type="checkbox" checked={fixar} onChange={(e) => setFixar(e.target.checked)} />
          fixar
        </label>
        <button onClick={adicionar} className="btn-primary shrink-0">
          <Plus size={13} /> anotar
        </button>
      </div>

      <div className="space-y-1.5">
        {notas.map((n: any) => (
          <div key={n.id} className={`flex items-start gap-2 rounded border px-2.5 py-1.5 text-xs ${
            n.pinned ? "border-warn/30 bg-warn/5" : "border-surface-border bg-surface/40"}`}>
            {n.pinned && <Pin size={11} className="mt-0.5 shrink-0 text-warn" />}
            <span className="flex-1 text-slate-300">{n.body}</span>
            <span className="shrink-0 text-slate-600">
              {n.author} · {tempoRelativo(n.created_at)}
            </span>
            <button onClick={async () => {
              await mutate(`/v1/notes/${n.id}`, "DELETE"); onChange();
            }} className="shrink-0 text-slate-700 hover:text-block">
              <Trash2 size={11} />
            </button>
          </div>
        ))}
        {notas.length === 0 && (
          <p className="text-xs text-slate-600">nenhuma observação ainda</p>
        )}
      </div>
    </section>
  );
}

function Campo({ rotulo, dica, children }: {
  rotulo: string; dica?: string; children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="label">{rotulo}</span>
      {children}
      {dica && <span className="mt-0.5 block text-xs text-slate-600">{dica}</span>}
    </label>
  );
}

function Numero({ rotulo, valor }: { rotulo: string; valor: number }) {
  return (
    <div>
      <div className="font-mono text-lg font-semibold tabular-nums text-slate-100">{valor}</div>
      <div className="text-xs text-slate-500">{rotulo}</div>
    </div>
  );
}
