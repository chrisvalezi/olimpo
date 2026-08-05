"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  Server, RefreshCw, Plus, AlertTriangle, ExternalLink, ShieldAlert,
  Smartphone, ChevronRight, Trash2, Radio, Lock, Unlock,
} from "lucide-react";
import { api, mutate } from "@/lib/api";
import {
  SERVICE_KINDS, STATUS_TONE, STATUS_LABEL, KIND_DEFAULTS, tempoRelativo,
} from "@/lib/servers";

/**
 * Servidores.
 *
 * Cada host roda vários serviços — o orchestrator que executa, o
 * android-manager que gerencia a frota, o gateway que transmite as telas.
 * Nenhum deles conhece os outros, e nenhum conhece a frota inteira.
 * Esta tela é o índice que falta.
 */
export default function Servidores() {
  const { data, mutate: reload } = useSWR("/v1/servers", api, { refreshInterval: 20_000 });
  const [sondando, setSondando] = useState<string | null>(null);
  const [novoServidor, setNovoServidor] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const servers = data?.servers ?? [];

  async function sondar(id: string, refresh = false) {
    setSondando(id);
    setErro(null);
    try {
      await mutate(`/v1/servers/${id}/probe${refresh ? "?refresh=true" : ""}`, "POST");
      await reload();
    } catch (e) {
      setErro(String(e));
    } finally {
      setSondando(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Servidores</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Um servidor é um host. Nele podem rodar o orchestrator, o
            android-manager e o gateway de tela — cada um com endereço,
            credencial e TLS próprios.
          </p>
        </div>
        <button onClick={() => setNovoServidor((v) => !v)} className="btn-primary">
          <Plus size={15} /> Novo servidor
        </button>
      </header>

      {erro && (
        <div className="flex items-start gap-2 rounded-lg border border-block/40 bg-block/10 p-3 text-sm text-red-200">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span className="font-mono text-xs">{erro}</span>
        </div>
      )}

      {novoServidor && (
        <FormServidor onDone={() => { setNovoServidor(false); reload(); }} />
      )}

      {servers.length === 0 && !novoServidor && (
        <div className="card text-center text-sm text-slate-500">
          Nenhum servidor cadastrado.
        </div>
      )}

      <div className="space-y-4">
        {servers.map((s: any) => (
          <CardServidor
            key={s.id}
            servidor={s}
            defaults={data?.defaults}
            sondando={sondando === s.id}
            onSondar={(r?: boolean) => sondar(s.id, r)}
            onChange={reload}
          />
        ))}
      </div>
    </div>
  );
}

function CardServidor({
  servidor, sondando, onSondar, onChange,
}: {
  servidor: any; defaults: any; sondando: boolean;
  onSondar: (refresh?: boolean) => void; onChange: () => void;
}) {
  const [novoServico, setNovoServico] = useState(false);
  const services = servidor.services ?? [];
  const sync = servidor.last_sync;

  const enderecos = [
    servidor.hostname && { rotulo: "host", valor: servidor.hostname },
    servidor.tailscale_ip && { rotulo: "tailscale", valor: servidor.tailscale_ip },
    servidor.lan_ip && { rotulo: "lan", valor: servidor.lan_ip },
  ].filter(Boolean) as { rotulo: string; valor: string }[];

  return (
    <div className="card space-y-4">
      {/* ---------------------------------------------------------- cabeçalho */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Server size={17} className="shrink-0 text-slate-500" />
            <h2 className="truncate text-base font-medium text-slate-100">
              {servidor.name}
            </h2>
            <span className={`rounded border px-1.5 py-0.5 text-xs ${STATUS_TONE[servidor.status]}`}>
              {STATUS_LABEL[servidor.status] ?? servidor.status}
            </span>
            {servidor.labels?.map((l: string) => (
              <span key={l} className="rounded bg-surface-border/50 px-1.5 py-0.5 text-xs text-slate-400">
                {l}
              </span>
            ))}
          </div>
          {servidor.description && (
            <p className="mt-1 text-sm text-slate-500">{servidor.description}</p>
          )}
          {enderecos.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs text-slate-600">
              {enderecos.map((e) => (
                <span key={e.rotulo}>
                  <span className="text-slate-700">{e.rotulo}</span> {e.valor}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button onClick={() => onSondar(false)} disabled={sondando} className="btn-ghost">
            <RefreshCw size={12} className={sondando ? "animate-spin" : ""} />
            {sondando ? "sondando…" : "sondar"}
          </button>
          {/* Varredura completa: força o nó a revisitar cada device. Custa
              cerca de 90s, então nunca é o comportamento padrão. */}
          <button
            onClick={() => onSondar(true)}
            disabled={sondando}
            title="Força o servidor a revarrer todos os devices (~90s)"
            className="btn-ghost"
          >
            varredura completa
          </button>
        </div>
      </div>

      {/* ------------------------------------------------------------ métricas */}
      <div className="grid grid-cols-2 gap-2 border-t border-surface-border pt-3 sm:grid-cols-4">
        <Metrica rotulo="serviços" valor={`${services.filter((s: any) => s.status === "online").length}/${services.length}`} />
        <Metrica rotulo="devices" valor={`${servidor.devices_alive}/${servidor.devices_total}`} />
        <Metrica rotulo="contas online" valor={servidor.accounts_online} />
        <Metrica
          rotulo="última sincronia"
          valor={tempoRelativo(sync?.created_at ?? null)}
          detalhe={sync ? `${sync.accounts_found} contas · ${sync.duration_ms}ms` : undefined}
        />
      </div>

      {/* ------------------------------------------------------------ serviços */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="label">Serviços</span>
          <button onClick={() => setNovoServico((v) => !v)} className="btn-ghost">
            <Plus size={12} /> serviço
          </button>
        </div>

        {services.length === 0 && (
          <p className="text-xs text-slate-600">
            Nenhum serviço. Sem ao menos um orchestrator, este servidor não executa nada.
          </p>
        )}

        {services.map((sv: any) => (
          <LinhaServico key={sv.id} servico={sv} onChange={onChange} />
        ))}

        {novoServico && (
          <FormServico
            serverId={servidor.id}
            servidor={servidor}
            onDone={() => { setNovoServico(false); onChange(); }}
          />
        )}
      </div>

      {/* ------------------------------------------------- contas descobertas */}
      <Descobertas serverId={servidor.id} onImport={onChange} />

      {/* -------------------------------------------------------------- rodapé */}
      <div className="flex items-center justify-between border-t border-surface-border pt-3">
        <Link
          href={`/servidores/${servidor.id}`}
          className="flex items-center gap-1 text-xs text-info hover:underline"
        >
          <Smartphone size={12} /> ver os {servidor.devices_total} devices
          <ChevronRight size={12} />
        </Link>
        {sync?.error && (
          <span className="font-mono text-xs text-red-300">{sync.error}</span>
        )}
      </div>
    </div>
  );
}

function LinhaServico({ servico, onChange }: { servico: any; onChange: () => void }) {
  const meta = SERVICE_KINDS[servico.kind as keyof typeof SERVICE_KINDS] ?? SERVICE_KINDS.custom;
  const [removendo, setRemovendo] = useState(false);

  async function remover() {
    if (!confirm(`Remover o serviço ${meta.rotulo}?`)) return;
    setRemovendo(true);
    try {
      await mutate(`/v1/servers/services/${servico.id}`, "DELETE");
      onChange();
    } catch {
      setRemovendo(false);
    }
  }

  const httpsSemVerificacao = servico.base_url?.startsWith("https") && !servico.verify_tls;
  // Certificado autoassinado é a regra em rede interna e produz um erro
  // longo e assustador. Reconhecer e oferecer a correção vale mais do que
  // despejar o traceback e deixar o operador procurar.
  const certificadoRecusado = /CERTIFICATE_VERIFY_FAILED|self-signed|self signed/i
    .test(servico.last_error ?? "");

  async function confiarNoCertificado() {
    await mutate(`/v1/servers/services/${servico.id}`, "PATCH", { verify_tls: false });
    onChange();
  }

  return (
    <div className="space-y-1">
    <div className="flex items-center gap-2 rounded border border-surface-border bg-surface/50 px-2.5 py-2">
      <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${meta.cor}`}>
        {meta.rotulo}
      </span>
      {servico.label && <span className="text-xs text-slate-500">{servico.label}</span>}

      <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-500">
        {servico.base_url}
      </span>

      {/* TLS: em rede interna o certificado costuma ser autoassinado, e
          desligar a verificação é uma decisão consciente — que precisa
          ficar visível, não escondida no banco. */}
      {servico.base_url?.startsWith("https") && (
        <span
          title={httpsSemVerificacao ? "TLS sem verificação de certificado" : "TLS verificado"}
          className={httpsSemVerificacao ? "text-warn" : "text-active"}
        >
          {httpsSemVerificacao ? <Unlock size={12} /> : <Lock size={12} />}
        </span>
      )}

      {servico.latency_ms != null && servico.status === "online" && (
        <span className="shrink-0 font-mono text-[10px] text-slate-600">
          {servico.latency_ms}ms
        </span>
      )}

      <span
        className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${STATUS_TONE[servico.status]}`}
        title={servico.last_error ?? undefined}
      >
        {STATUS_LABEL[servico.status] ?? servico.status}
      </span>

      {/* Link clicável para a interface do serviço */}
      {servico.web_url && (
        <a
          href={servico.web_url}
          target="_blank"
          rel="noreferrer"
          title={`Abrir ${meta.rotulo}`}
          className="shrink-0 text-slate-500 hover:text-info"
        >
          <ExternalLink size={13} />
        </a>
      )}

      <button
        onClick={remover}
        disabled={removendo}
        className="shrink-0 text-slate-600 hover:text-block"
        title="Remover serviço"
      >
        <Trash2 size={13} />
      </button>
    </div>

    {certificadoRecusado && (
      <div className="flex items-center gap-2 rounded border border-warn/30 bg-warn/5 px-2.5 py-1.5 text-xs text-amber-200">
        <ShieldAlert size={12} className="shrink-0" />
        <span className="flex-1">
          Certificado autoassinado. Comum em rede interna — o tráfego continua
          cifrado, só não há terceiro atestando a identidade do servidor.
        </span>
        <button onClick={confiarNoCertificado} className="btn-ghost shrink-0">
          confiar neste serviço
        </button>
      </div>
    )}

    {servico.last_error && !certificadoRecusado && (
      <p className="px-2.5 font-mono text-[10px] text-red-300/80">{servico.last_error}</p>
    )}
    </div>
  );
}

function Descobertas({ serverId, onImport }: { serverId: string; onImport: () => void }) {
  const { data, mutate: reload } = useSWR(`/v1/servers/${serverId}/discovered`, api);
  const [importando, setImportando] = useState(false);
  const contas = data?.discovered ?? [];

  if (contas.length === 0) return null;

  async function importar(usernames?: string[]) {
    setImportando(true);
    try {
      await mutate(`/v1/servers/${serverId}/import`, "POST", { usernames: usernames ?? null });
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
        <button onClick={() => importar()} disabled={importando} className="btn-ghost">
          {importando ? "importando…" : "importar todas"}
        </button>
      </div>
      <ul className="grid gap-1 sm:grid-cols-2">
        {contas.map((c: any) => (
          <li key={c.handle} className="flex items-center justify-between text-xs">
            <span className="truncate text-slate-300">
              {c.handle}
              <span className="ml-1.5 text-slate-600">{c.platform}</span>
              {c.followers != null && (
                <span className="ml-1.5 text-slate-600">{c.followers} seg.</span>
              )}
              {c.containers.length > 1 && (
                <span className="ml-1.5 text-warn">{c.containers.length} devices</span>
              )}
            </span>
            <button
              onClick={() => importar([c.username])}
              disabled={importando}
              className="shrink-0 text-slate-500 hover:text-slate-200"
            >
              importar
            </button>
          </li>
        ))}
      </ul>
      <p className="text-xs text-slate-600">
        Importadas nascem em <code>draft</code>/L0: vinculadas ao device e com o
        inventário real, mas sem persona e sem operar.
      </p>
    </div>
  );
}

// =============================================================================
// Formulários
// =============================================================================

function FormServidor({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({
    name: "", description: "", hostname: "", tailscale_ip: "", lan_ip: "",
  });
  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);

  async function salvar() {
    setSalvando(true); setErro(null);
    try {
      await mutate("/v1/servers", "POST", {
        ...f,
        preferred_host: f.hostname || f.tailscale_ip || f.lan_ip || null,
      });
      onDone();
    } catch (e) {
      setErro(String(e)); setSalvando(false);
    }
  }

  return (
    <div className="card space-y-3">
      <span className="label">Novo servidor</span>
      <div className="grid gap-3 sm:grid-cols-2">
        <Campo rotulo="Nome" dica="como o host é conhecido">
          <input className="input" placeholder="media-dev"
            value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Campo>
        <Campo rotulo="Descrição" dica="opcional">
          <input className="input" placeholder="host de produção"
            value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        </Campo>
        <Campo rotulo="Hostname" dica="DNS ou nome Tailscale">
          <input className="input" placeholder="media-dev.taila41397.ts.net"
            value={f.hostname} onChange={(e) => setF({ ...f, hostname: e.target.value })} />
        </Campo>
        <Campo rotulo="IP Tailscale">
          <input className="input" placeholder="100.99.92.63"
            value={f.tailscale_ip} onChange={(e) => setF({ ...f, tailscale_ip: e.target.value })} />
        </Campo>
        <Campo rotulo="IP na LAN">
          <input className="input" placeholder="192.168.1.20"
            value={f.lan_ip} onChange={(e) => setF({ ...f, lan_ip: e.target.value })} />
        </Campo>
      </div>
      <p className="text-xs text-slate-600">
        Os serviços (orchestrator, android-manager, gateway de tela) são
        cadastrados depois, dentro do servidor.
      </p>
      {erro && <p className="font-mono text-xs text-red-300">{erro}</p>}
      <div className="flex gap-2">
        <button onClick={salvar} disabled={salvando || !f.name} className="btn-primary">
          {salvando ? "salvando…" : "Cadastrar"}
        </button>
        <button onClick={onDone} className="btn-ghost">Cancelar</button>
      </div>
    </div>
  );
}

function FormServico({
  serverId, servidor, onDone,
}: {
  serverId: string; servidor: any; onDone: () => void;
}) {
  const [kind, setKind] = useState("android_manager");
  const d = KIND_DEFAULTS[kind];
  const host = servidor.preferred_host || servidor.hostname
    || servidor.tailscale_ip || servidor.lan_ip || "";

  const [f, setF] = useState({
    base_url: "", web_url: "", auth_user: "", auth_secret: "", verify_tls: true,
  });
  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);

  // Preenche o endereço a partir do que já sabemos do servidor.
  function aplicarSugestao(novoKind: string) {
    setKind(novoKind);
    const s = KIND_DEFAULTS[novoKind];
    if (!host) return;
    const porta = (s.esquema === "https" && s.porta === 443) ? "" : `:${s.porta}`;
    const url = `${s.esquema}://${host}${porta}`;
    setF((prev) => ({
      ...prev,
      base_url: url,
      web_url: url,
      // Certificado autoassinado é a regra em rede interna.
      verify_tls: s.esquema !== "https",
    }));
  }

  async function salvar() {
    setSalvando(true); setErro(null);
    try {
      await mutate(`/v1/servers/${serverId}/services`, "POST", {
        kind,
        base_url: f.base_url,
        web_url: f.web_url || f.base_url,
        auth_scheme: d.auth,
        auth_user: f.auth_user || null,
        auth_secret: f.auth_secret || null,
        verify_tls: f.verify_tls,
      });
      onDone();
    } catch (e) {
      setErro(String(e)); setSalvando(false);
    }
  }

  return (
    <div className="space-y-3 rounded border border-info/30 bg-info/5 p-3">
      <span className="label">Novo serviço</span>

      <div className="flex flex-wrap gap-1.5">
        {Object.entries(SERVICE_KINDS).map(([k, meta]) => (
          <button
            key={k}
            onClick={() => aplicarSugestao(k)}
            title={meta.descricao}
            className={`rounded border px-2 py-1 text-xs ${
              kind === k ? meta.cor : "border-surface-border text-slate-500 hover:text-slate-300"
            }`}
          >
            {meta.rotulo}
          </button>
        ))}
      </div>

      {d.dica && <p className="text-xs text-amber-200/80">{d.dica}</p>}

      <div className="grid gap-3 sm:grid-cols-2">
        <Campo rotulo="URL da API" dica="onde o serviço responde">
          <input className="input" placeholder={`${d.esquema}://${host || "host"}:${d.porta}`}
            value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} />
        </Campo>
        <Campo rotulo="URL da interface" dica="para onde o link no painel leva">
          <input className="input" placeholder="mesma da API, se vazio"
            value={f.web_url} onChange={(e) => setF({ ...f, web_url: e.target.value })} />
        </Campo>

        {d.auth !== "none" && (
          <>
            <Campo rotulo={d.auth === "bearer" ? "—" : "Usuário"}
              dica={d.auth === "login" ? "não existe chave de API neste serviço" : "opcional"}>
              <input className="input" disabled={d.auth === "bearer"}
                value={f.auth_user} onChange={(e) => setF({ ...f, auth_user: e.target.value })} />
            </Campo>
            <Campo rotulo={d.auth === "bearer" ? "Token" : "Senha"}>
              <input className="input" type="password"
                value={f.auth_secret} onChange={(e) => setF({ ...f, auth_secret: e.target.value })} />
            </Campo>
          </>
        )}
      </div>

      {f.base_url.startsWith("https") && (
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={f.verify_tls}
            onChange={(e) => setF({ ...f, verify_tls: e.target.checked })} />
          Verificar certificado TLS
          <span className="text-slate-600">
            (desmarque se o certificado for autoassinado — vale só para este serviço)
          </span>
        </label>
      )}

      {erro && <p className="font-mono text-xs text-red-300">{erro}</p>}
      <div className="flex gap-2">
        <button onClick={salvar} disabled={salvando || f.base_url.length < 8} className="btn-primary">
          {salvando ? "salvando…" : "Adicionar"}
        </button>
        <button onClick={onDone} className="btn-ghost">Cancelar</button>
      </div>
    </div>
  );
}

// =============================================================================

function Metrica({ rotulo, valor, detalhe }: { rotulo: string; valor: any; detalhe?: string }) {
  return (
    <div>
      <div className="font-mono text-lg font-semibold tabular-nums text-slate-100">{valor}</div>
      <div className="text-xs text-slate-500">{rotulo}</div>
      {detalhe && <div className="text-[10px] text-slate-600">{detalhe}</div>}
    </div>
  );
}

function Campo({ rotulo, dica, children }: { rotulo: string; dica?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label">{rotulo}</span>
      {children}
      {dica && <span className="mt-0.5 block text-xs text-slate-600">{dica}</span>}
    </label>
  );
}
