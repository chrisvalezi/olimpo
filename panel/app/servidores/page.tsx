"use client";

import { useState } from "react";
import useSWR from "swr";
import { Server, RefreshCw, Plus, AlertTriangle, Cpu } from "lucide-react";
import { api, mutate } from "@/lib/api";

/**
 * Servidores android-orchestrator.
 *
 * Cada host com containers redroid roda a própria pilha e só conhece a si
 * mesmo — não existe índice de frota do lado deles. Esta tela É o índice.
 */
export default function Servidores() {
  const { data, mutate: reload } = useSWR("/v1/nodes", api, { refreshInterval: 15_000 });
  const [probing, setProbing] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const nodes = data?.nodes ?? [];

  async function probe(id: string) {
    setProbing(id);
    setErro(null);
    try {
      await mutate(`/v1/nodes/${id}/probe`, "POST");
      await reload();
    } catch (e) {
      setErro(String(e));
    } finally {
      setProbing(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Servidores</h1>
          <p className="mt-1 text-sm text-slate-500">
            Cada host com redroid roda seu próprio orchestrator e só enxerga os
            containers dele. A visão de frota é montada aqui.
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-1.5 rounded-md border border-surface-border bg-surface-raised px-3 py-1.5 text-sm text-slate-200 hover:bg-surface-border/50"
        >
          <Plus size={15} /> Novo servidor
        </button>
      </header>

      {erro && (
        <div className="flex items-start gap-2 rounded-lg border border-block/40 bg-block/10 p-3 text-sm text-red-200">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>{erro}</span>
        </div>
      )}

      {showForm && <NovoServidor onDone={() => { setShowForm(false); reload(); }} />}

      {nodes.length === 0 && !showForm && (
        <div className="card text-center text-sm text-slate-500">
          Nenhum servidor cadastrado. Adicione o primeiro apontando para a porta
          do <strong className="text-slate-300">worker (8078)</strong>.
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        {nodes.map((n: any) => (
          <NodeCard key={n.id} node={n} probing={probing === n.id} onProbe={() => probe(n.id)} />
        ))}
      </div>
    </div>
  );
}

/**
 * Contas que o nó tem logadas e o Olimpo ainda não conhece.
 *
 * Elas não são criadas na sincronização de propósito: uma conta no Olimpo
 * precisa de persona, arquétipo e nível de autonomia — decisões de humano.
 * Mas ficar invisível também é errado, então elas aparecem aqui.
 */
function Descobertas({ nodeId, onImport }: { nodeId: string; onImport: () => void }) {
  const { data, mutate: reload } = useSWR(`/v1/nodes/${nodeId}/discovered`, api);
  const [importando, setImportando] = useState(false);
  const contas = data?.discovered ?? [];

  if (contas.length === 0) return null;

  async function importar(usernames?: string[]) {
    setImportando(true);
    try {
      await mutate(`/v1/nodes/${nodeId}/import`, "POST", { usernames: usernames ?? null });
      await reload();
      onImport();
    } finally {
      setImportando(false);
    }
  }

  return (
    <div className="space-y-2 rounded border border-warn/30 bg-warn/5 p-2.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-amber-200">
          {contas.length} conta{contas.length > 1 ? "s" : ""} logada
          {contas.length > 1 ? "s" : ""} que o Olimpo não conhece
        </span>
        <button
          onClick={() => importar()}
          disabled={importando}
          className="rounded border border-warn/40 px-2 py-0.5 text-xs text-amber-200 hover:bg-warn/10 disabled:opacity-50"
        >
          {importando ? "importando…" : "importar todas"}
        </button>
      </div>
      <ul className="space-y-1">
        {contas.map((c: any) => (
          <li key={c.handle} className="flex items-center justify-between text-xs">
            <span className="text-slate-300">
              {c.handle}
              <span className="ml-1.5 text-slate-600">{c.platform}</span>
              {c.containers.length > 1 && (
                <span className="ml-1.5 text-warn">{c.containers.length} devices</span>
              )}
            </span>
            <button
              onClick={() => importar([c.username])}
              disabled={importando}
              className="text-slate-500 hover:text-slate-200 disabled:opacity-50"
            >
              importar
            </button>
          </li>
        ))}
      </ul>
      <p className="text-xs text-slate-600">
        Importadas nascem em <code>draft</code>/L0: vinculadas ao device, mas sem
        persona e sem operar até alguém ativá-las.
      </p>
    </div>
  );
}

function NodeCard({ node, probing, onProbe }: { node: any; probing: boolean; onProbe: () => void }) {
  const tones: Record<string, string> = {
    active: "text-active border-active/40 bg-active/10",
    degraded: "text-warn border-warn/40 bg-warn/10",
    unreachable: "text-block border-block/40 bg-block/10",
    disabled: "text-paused border-paused/40 bg-paused/10",
  };
  const actionCount = node.actions_cache ? Object.keys(node.actions_cache).length : 0;

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <Server size={16} className="text-slate-500" />
          <div>
            <div className="font-medium text-slate-100">{node.name}</div>
            <div className="font-mono text-xs text-slate-500">{node.base_url}</div>
          </div>
        </div>
        <span className={`rounded border px-2 py-0.5 text-xs ${tones[node.status] ?? tones.disabled}`}>
          {node.status}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 border-t border-surface-border pt-3 text-center">
        <Metric label="devices" value={node.device_count ?? 0} />
        <Metric label="contas on" value={node.accounts_online ?? 0} />
        <Metric label="ações" value={actionCount} />
      </div>

      {node.last_error && (
        <div className="rounded border border-block/30 bg-block/5 p-2 font-mono text-xs text-red-300">
          {node.last_error}
        </div>
      )}

      {node.inventory_refreshed_at && <Descobertas nodeId={node.id} onImport={onProbe} />}

      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>
          {node.inventory_refreshed_at
            ? `inventário: ${new Date(node.inventory_refreshed_at).toLocaleString("pt-BR")}`
            : "nunca sincronizado"}
        </span>
        <button
          onClick={onProbe}
          disabled={probing}
          className="flex items-center gap-1.5 rounded border border-surface-border px-2 py-1 text-slate-300 hover:bg-surface-border/50 disabled:opacity-50"
        >
          <RefreshCw size={12} className={probing ? "animate-spin" : ""} />
          {probing ? "sincronizando…" : "sincronizar"}
        </button>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="font-mono text-lg font-semibold tabular-nums text-slate-100">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}

function NovoServidor({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({
    name: "", base_url: "http://", basic_user: "", basic_password: "", priority: 100,
  });
  const [erro, setErro] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function salvar() {
    setSaving(true);
    setErro(null);
    try {
      await mutate("/v1/nodes", "POST", {
        ...form,
        basic_user: form.basic_user || null,
        basic_password: form.basic_password || null,
      });
      onDone();
    } catch (e) {
      setErro(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-2 text-sm text-slate-300">
        <Cpu size={15} /> Novo servidor
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Campo label="Nome" hint="como o host é conhecido">
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="i9-14900k"
            className="input"
          />
        </Campo>
        <Campo label="URL do worker" hint="porta 8078 — nunca a 8077">
          <input
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="http://192.168.1.20:8078"
            className="input"
          />
        </Campo>
        <Campo label="Usuário (Basic)" hint="opcional — auth é opt-in no serviço">
          <input
            value={form.basic_user}
            onChange={(e) => setForm({ ...form, basic_user: e.target.value })}
            className="input"
          />
        </Campo>
        <Campo label="Senha (Basic)" hint="opcional">
          <input
            type="password"
            value={form.basic_password}
            onChange={(e) => setForm({ ...form, basic_password: e.target.value })}
            className="input"
          />
        </Campo>
      </div>

      {/* A porta 8077 monta o docker.sock: quem fala com ela tem root no host. */}
      <p className="text-xs text-slate-500">
        A porta <code className="text-slate-400">8077</code> é do executor, que monta o
        docker.sock e escuta só em loopback. Sempre cadastre o worker.
      </p>

      {erro && <div className="text-xs text-red-300">{erro}</div>}

      <div className="flex gap-2">
        <button
          onClick={salvar}
          disabled={saving || !form.name || form.base_url.length < 10}
          className="rounded-md bg-info/20 px-3 py-1.5 text-sm text-info hover:bg-info/30 disabled:opacity-40"
        >
          {saving ? "salvando…" : "Cadastrar"}
        </button>
        <button onClick={onDone} className="rounded-md px-3 py-1.5 text-sm text-slate-400 hover:text-slate-200">
          Cancelar
        </button>
      </div>
    </div>
  );
}

function Campo({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-xs text-slate-600">{hint}</span>}
    </label>
  );
}
