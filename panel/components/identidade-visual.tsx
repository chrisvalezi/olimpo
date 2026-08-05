"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle, ImageOff, Info, Sparkles, Loader2, Check, Shirt, Plus, X,
} from "lucide-react";
import { api, mutate, urlDaMidia } from "@/lib/api";
import { Upload_ } from "@/components/upload";

/**
 * Identidade visual: as quatro âncoras.
 *
 * Não há semente fixa nem modelo treinado por trás da consistência. A
 * personagem só se mantém a mesma porque estas imagens são reenviadas como
 * referência a cada geração — e porque a ORDEM delas é o que o prompt
 * referencia. A primeira é o rosto, a segunda é a roupa.
 *
 * As três derivadas nascem DO ROSTO, usando-o como referência. É essa cadeia
 * que faz todas serem da mesma pessoa; por isso o rosto vem primeiro e as
 * outras ficam bloqueadas até ele existir.
 */
export function IdentidadeVisual({ personaId }: { personaId: string }) {
  const { data, mutate: reload } = useSWR(
    `/v1/media/personas/${personaId}/identidade`, api,
    { refreshInterval: 8_000 },
  );
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const [erro, setErro] = useState<string | null>(null);
  const [candidatas, setCandidatas] = useState<string[] | null>(null);

  if (!data) return null;
  const { ancoras, completas, pode_gerar_com_identidade } = data;

  async function gerar(ancora: string) {
    setErro(null);
    try {
      const r = await mutate(
        `/v1/media/personas/${personaId}/ancoras/${ancora}`, "POST",
      );
      setJobs((j) => ({ ...j, [ancora]: r.job_id }));
    } catch (e) {
      setErro(String(e).replace("Error: ", ""));
    }
  }

  return (
    <section className="card space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <span className="label">Identidade visual</span>
          <p className="mt-0.5 text-xs text-slate-500">
            As quatro imagens que fazem a personagem ser sempre a mesma pessoa.
          </p>
        </div>
        <span className={`text-xs ${completas === 4 ? "text-active" : "text-warn"}`}>
          {completas}/4
        </span>
      </div>

      {!pode_gerar_com_identidade && (
        <div className="flex items-start gap-2 rounded border border-block/30 bg-block/5 p-2.5 text-xs text-red-200">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>
            <strong>Comece pelo rosto.</strong> Sem ele não há identidade a
            preservar — cada geração produziria uma pessoa diferente, e as
            outras três derivam dele.
          </span>
        </div>
      )}

      {erro && (
        <p className="flex items-start gap-1 text-xs text-red-300">
          <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {erro}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ancoras.map((a: any) => (
          <Ancora
            key={a.papel}
            ancora={a}
            personaId={personaId}
            temRosto={pode_gerar_com_identidade}
            jobId={jobs[a.papel]}
            onGerar={() => gerar(a.papel)}
            onMudou={() => { reload(); setJobs((j) => ({ ...j, [a.papel]: "" })); }}
            onCandidatas={setCandidatas}
          />
        ))}
      </div>

      {candidatas && (
        <EscolherRosto
          personaId={personaId} assets={candidatas}
          onFechar={() => setCandidatas(null)}
          onEscolhido={() => { setCandidatas(null); reload(); }}
        />
      )}

      <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
        <Info size={11} className="mt-0.5 shrink-0" />
        A numeração é o que o prompt referencia. A ordem é fixa de propósito —
        inverter as duas primeiras faz o modelo copiar a roupa da referência de
        rosto.
      </p>
    </section>
  );
}

function Ancora({
  ancora, personaId, temRosto, jobId, onGerar, onMudou, onCandidatas,
}: {
  ancora: any; personaId: string; temRosto: boolean; jobId?: string;
  onGerar: () => void; onMudou: () => void;
  onCandidatas: (ids: string[]) => void;
}) {
  const ehRosto = ancora.papel === "face";
  const bloqueada = !ehRosto && !temRosto;

  // Acompanha o job enquanto ele existir.
  const { data: job } = useSWR(
    jobId ? `/v1/media/jobs/${jobId}` : null, api, { refreshInterval: 3_000 },
  );
  const rodando = job && ["queued", "running"].includes(job.status);

  if (job?.status === "done" && job.result_asset_ids?.length) {
    // O rosto vem em três variações para escolher; as derivadas já entram.
    if (ehRosto && job.result_asset_ids.length > 1) {
      onCandidatas(job.result_asset_ids);
    } else {
      onMudou();
    }
  }

  return (
    <figure className="space-y-1.5">
      <div className="relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface">
        {ancora.asset_id ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={urlDaMidia(ancora.asset_id)} alt={ancora.rotulo}
            loading="lazy" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-slate-700">
            {rodando ? (
              <>
                <Loader2 size={18} className="animate-spin text-info" />
                <span className="text-[10px] text-info">gerando…</span>
              </>
            ) : (
              <ImageOff size={18} />
            )}
          </div>
        )}
        <span className="absolute left-1 top-1 rounded bg-black/70 px-1 py-0.5 font-mono text-[10px] text-slate-300">
          @image{ancora.posicao}
        </span>
        {job?.status === "error" && (
          <span className="absolute inset-x-1 bottom-1 rounded bg-block/80 px-1 py-0.5 text-[9px] text-white">
            falhou
          </span>
        )}
      </div>

      <figcaption className="text-[11px] leading-tight text-slate-400">
        {ancora.rotulo}
        {ancora.detalhe && (
          <span className="block text-[10px] text-slate-600">
            {Math.round(ancora.detalhe.bytes / 1024)} KB
          </span>
        )}
      </figcaption>

      {job?.status === "error" && (
        <p className="text-[10px] leading-tight text-red-300">{job.error}</p>
      )}

      <div className="space-y-1">
        <button
          onClick={onGerar}
          disabled={bloqueada || rodando}
          title={
            bloqueada ? "Defina o rosto primeiro — esta imagem deriva dele"
              : ehRosto ? "Gera três variações a partir da descrição física"
                : "Gera usando o rosto como referência"
          }
          className="btn-ghost w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Sparkles size={11} />
          {ancora.asset_id ? "gerar de novo" : "gerar"}
        </button>

        <Upload_
          personaId={personaId} papel={ancora.papel} compacto
          aoEnviar={onMudou}
        >
          enviar foto
        </Upload_>
      </div>
    </figure>
  );
}

/** As três variações de rosto. A escolha é humana porque nenhuma métrica
 *  captura "esta é a personagem que eu imaginei". */
function EscolherRosto({ personaId, assets, onFechar, onEscolhido }: {
  personaId: string; assets: string[];
  onFechar: () => void; onEscolhido: () => void;
}) {
  const [salvando, setSalvando] = useState<string | null>(null);

  async function escolher(assetId: string) {
    setSalvando(assetId);
    try {
      await mutate(
        `/v1/media/personas/${personaId}/ancoras/face/definir?asset_id=${assetId}`,
        "POST",
      );
      onEscolhido();
    } finally {
      setSalvando(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4">
      <div className="w-full max-w-4xl space-y-3 rounded-lg border border-surface-border bg-surface-raised p-4">
        <div className="flex items-center justify-between">
          <div>
            <span className="label">Escolha o rosto</span>
            <p className="text-xs text-slate-500">
              A escolhida vira a âncora nº 1 e passa a ancorar tudo o mais.
            </p>
          </div>
          <button onClick={onFechar} className="text-slate-500 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        <div className="grid grid-cols-3 gap-3">
          {assets.map((id) => (
            <button key={id} onClick={() => escolher(id)} disabled={!!salvando}
              className="group relative aspect-[3/4] overflow-hidden rounded border-2 border-transparent bg-surface transition-colors hover:border-info disabled:opacity-50">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={urlDaMidia(id)} alt="" className="h-full w-full object-cover" />
              <span className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all group-hover:bg-black/40 group-hover:opacity-100">
                {salvando === id
                  ? <Loader2 size={20} className="animate-spin text-white" />
                  : <Check size={22} className="text-white" />}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Figurinos: a mesma personagem com outra roupa. */
export function Figurinos({ personaId }: { personaId: string }) {
  const { data, mutate: reload } = useSWR(
    `/v1/media/personas/${personaId}/figurinos`, api, { refreshInterval: 10_000 },
  );
  const [criando, setCriando] = useState(false);
  const [f, setF] = useState({ nome: "", roupa: "", cabelo: "", texto_livre: "" });
  const [job, setJob] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  const { data: estado } = useSWR(job ? `/v1/media/jobs/${job}` : null, api,
    { refreshInterval: 3_000 });
  if (estado?.status === "done") { setJob(null); reload(); }

  const figurinos = data?.figurinos ?? [];

  async function criar() {
    setErro(null);
    try {
      const p = new URLSearchParams({
        nome: f.nome, roupa: f.roupa, cabelo: f.cabelo, texto_livre: f.texto_livre,
      });
      const r = await mutate(
        `/v1/media/personas/${personaId}/figurinos?${p}`, "POST",
      );
      setJob(r.job_id);
      setCriando(false);
      setF({ nome: "", roupa: "", cabelo: "", texto_livre: "" });
    } catch (e) {
      setErro(String(e).replace("Error: ", ""));
    }
  }

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <span className="label">Guarda-roupa</span>
          <p className="mt-0.5 text-xs text-slate-500">
            Cada figurino é uma folha de 4 painéis do mesmo rosto com outra
            roupa — reutilizável em qualquer foto.
          </p>
        </div>
        <button onClick={() => setCriando((v) => !v)} className="btn-ghost">
          <Plus size={12} /> figurino
        </button>
      </div>

      {criando && (
        <div className="space-y-2 rounded border border-info/30 bg-info/5 p-2.5">
          <div className="grid gap-2 sm:grid-cols-4">
            <input className="input" placeholder="nome (ex.: treino)"
              value={f.nome} onChange={(e) => setF({ ...f, nome: e.target.value })} />
            <input className="input" placeholder="roupa"
              value={f.roupa} onChange={(e) => setF({ ...f, roupa: e.target.value })} />
            <input className="input" placeholder="cabelo"
              value={f.cabelo} onChange={(e) => setF({ ...f, cabelo: e.target.value })} />
            <input className="input" placeholder="observação livre"
              value={f.texto_livre}
              onChange={(e) => setF({ ...f, texto_livre: e.target.value })} />
          </div>
          <div className="flex gap-2">
            <button onClick={criar} disabled={!f.nome} className="btn-primary">
              <Sparkles size={13} /> gerar
            </button>
            <button onClick={() => setCriando(false)} className="btn-ghost">
              cancelar
            </button>
          </div>
        </div>
      )}

      {erro && <p className="text-xs text-red-300">{erro}</p>}

      {job && (
        <p className="flex items-center gap-1.5 text-xs text-info">
          <Loader2 size={12} className="animate-spin" /> gerando figurino…
        </p>
      )}

      {figurinos.length === 0 && !criando ? (
        <p className="text-xs text-slate-600">nenhum figurino ainda</p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
          {figurinos.map((w: any) => (
            <figure key={w.id} className="space-y-1">
              <div className="aspect-video overflow-hidden rounded border border-surface-border bg-surface">
                {w.asset_id ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={urlDaMidia(w.asset_id)} alt={w.name} loading="lazy"
                    className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center text-slate-700">
                    <Shirt size={16} />
                  </div>
                )}
              </div>
              <figcaption className="truncate text-[11px] text-slate-400">
                {w.name}
              </figcaption>
            </figure>
          ))}
        </div>
      )}
    </section>
  );
}

/** Galeria do que a persona já produziu. */
export function GaleriaDaPersona({ personaId }: { personaId: string }) {
  const { data } = useSWR(
    `/v1/media?persona_id=${personaId}&role=generated&per_page=24`, api,
    { refreshInterval: 15_000 },
  );
  const midias = data?.media ?? [];
  if (midias.length === 0) return null;

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between">
        <span className="label">Conteúdo produzido</span>
        <span className="text-xs text-slate-500">{data.total} peças</span>
      </div>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {midias.map((m: any) => (
          <a key={m.id} href={urlDaMidia(m.id)} target="_blank" rel="noreferrer"
            title={m.prompt ?? ""}
            className="group relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface">
            {m.kind === "video" ? (
              <video src={urlDaMidia(m.id)} className="h-full w-full object-cover" muted />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={urlDaMidia(m.id)} alt="" loading="lazy"
                className="h-full w-full object-cover transition-transform group-hover:scale-105" />
            )}
            {m.model && (
              <span className="absolute bottom-0 left-0 right-0 truncate bg-black/70 px-1 py-0.5 text-[9px] text-slate-400">
                {m.model}
              </span>
            )}
          </a>
        ))}
      </div>
    </section>
  );
}
