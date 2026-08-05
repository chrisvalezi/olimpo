"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import {
  AlertTriangle, ImageOff, Info, Sparkles, Loader2, Check, Shirt, Plus, X,
  Maximize2, Upload as UploadIcon, RefreshCw, Download,
} from "lucide-react";
import { api, mutate, urlDaMidia } from "@/lib/api";
import { Upload_ } from "@/components/upload";
import { GerarIdentidade } from "@/components/gerar-identidade";
import { GerarFigurino } from "@/components/gerar-figurino";
import { GerarDerivada } from "@/components/gerar-derivada";
import { FotosDeReferencia } from "@/components/fotos-referencia";

/**
 * Identidade visual: as quatro âncoras.
 *
 * Não há semente fixa nem modelo treinado por trás da consistência. A
 * personagem só se mantém a mesma porque estas imagens são reenviadas como
 * referência a cada geração — e porque a ORDEM delas é o que o prompt
 * referencia. A primeira é o rosto, a segunda é a roupa.
 *
 * As três derivadas nascem DO ROSTO. Por isso ele vem primeiro e as outras
 * ficam bloqueadas até ele existir.
 */
export function IdentidadeVisual({ personaId, persona }: {
  personaId: string; persona?: any;
}) {
  const { data, mutate: reload } = useSWR(
    `/v1/media/personas/${personaId}/identidade`, api,
    { refreshInterval: 6_000 },
  );
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const [erro, setErro] = useState<string | null>(null);
  const [candidatas, setCandidatas] = useState<string[] | null>(null);
  const [abrirGerador, setAbrirGerador] = useState(false);
  const [ampliada, setAmpliada] = useState<string | null>(null);
  //: Qual âncora derivada está com o popup aberto. O popup existe porque
  //: gerar custa: sem ele, cada clique gastava uma geração no modelo que
  //: estivesse por padrão, sem o operador ver qual era.
  const [derivada, setDerivada] = useState<string | null>(null);

  if (!data) return null;
  const { ancoras, completas, pode_gerar_com_identidade } = data;

  async function aposentar(ancora: string) {
    const ehRosto = ancora === "face";
    const texto = ehRosto
      ? "Tirar o rosto derruba a identidade inteira — as outras três derivam dele. A imagem continua na biblioteca. Continuar?"
      : "Desligar esta âncora? A imagem continua na biblioteca e pode ser reescolhida.";
    if (!confirm(texto)) return;
    await mutate(`/v1/media/personas/${personaId}/ancoras/${ancora}`, "DELETE");
    reload();
  }

  return (
    <section className="card space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="label">Identidade visual</span>
          <p className="mt-0.5 text-xs text-slate-500">
            As quatro imagens que fazem a personagem ser sempre a mesma pessoa.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className={`text-xs ${completas === 4 ? "text-active" : "text-warn"}`}>
            {completas}/4
          </span>
          <button onClick={() => setAbrirGerador(true)} className="btn-primary">
            <Sparkles size={13} /> gerar rosto
          </button>
        </div>
      </div>

      {!pode_gerar_com_identidade && (
        <div className="flex items-start gap-2 rounded border border-block/30 bg-block/5 p-2.5 text-xs text-red-200">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>
            <strong>Comece pelo rosto.</strong> Sem ele não há identidade a
            preservar — cada geração produziria uma pessoa diferente, e as
            outras três derivam dele. Você pode enviar fotos de referência ou
            gerar a partir da descrição física.
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
            onGerar={() => a.papel === "face" ? setAbrirGerador(true) : setDerivada(a.papel)}
            onAposentar={() => aposentar(a.papel)}
            onAmpliar={() => a.asset_id && setAmpliada(a.asset_id)}
            onMudou={() => { reload(); setJobs((j) => ({ ...j, [a.papel]: "" })); }}
            onCandidatas={setCandidatas}
          />
        ))}
      </div>

      <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
        <Info size={11} className="mt-0.5 shrink-0" />
        A numeração é o que o prompt referencia. A ordem é fixa de propósito —
        inverter as duas primeiras faz o modelo copiar a roupa da referência de
        rosto.
      </p>

      {abrirGerador && (
        <GerarIdentidade
          personaId={personaId}
          personaNome={persona?.name ?? "persona"}
          temDescricao={!!(persona?.physical_desc ?? "").trim()}
          onFechar={() => setAbrirGerador(false)}
          onEnfileirado={(jobId) => {
            setAbrirGerador(false);
            setJobs((j) => ({ ...j, face: jobId }));
          }}
        />
      )}

      {derivada && (
        <GerarDerivada
          personaId={personaId}
          ancora={derivada as any}
          rostoAssetId={ancoras.find((a: any) => a.papel === "face")?.asset_id ?? null}
          onFechar={() => setDerivada(null)}
          onEnfileirado={(jobId) => {
            setJobs((j) => ({ ...j, [derivada]: jobId }));
            setDerivada(null);
          }}
        />
      )}

      {candidatas && (
        <EscolherRosto
          personaId={personaId} assets={candidatas}
          onFechar={() => setCandidatas(null)}
          onEscolhido={() => { setCandidatas(null); reload(); }}
        />
      )}

      {ampliada && <Lightbox assetId={ampliada} onFechar={() => setAmpliada(null)} />}
    </section>
  );
}

function Ancora({
  ancora, personaId, temRosto, jobId, onGerar, onAposentar, onAmpliar,
  onMudou, onCandidatas,
}: {
  ancora: any; personaId: string; temRosto: boolean; jobId?: string;
  onGerar: () => void; onAposentar: () => void; onAmpliar: () => void;
  onMudou: () => void; onCandidatas: (ids: string[]) => void;
}) {
  const ehRosto = ancora.papel === "face";
  const bloqueada = !ehRosto && !temRosto;

  const { data: job } = useSWR(
    jobId ? `/v1/media/jobs/${jobId}` : null, api, { refreshInterval: 3_000 },
  );
  const rodando = job && ["queued", "running"].includes(job.status);

  // Avisar o pai é efeito colateral, e efeito colateral não acontece durante
  // o render — o React interrompe com "cannot update a component while
  // rendering a different one", e com razão: a atualização do pai
  // reinvalidaria este componente no meio da própria renderização.
  useEffect(() => {
    if (job?.status !== "done" || !job.result_asset_ids?.length) return;
    if (ehRosto && job.result_asset_ids.length > 1) onCandidatas(job.result_asset_ids);
    else onMudou();
    // Só o desfecho do job importa aqui; as funções mudam a cada render do
    // pai e entrariam em laço se fossem dependências.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status, job?.result_asset_ids?.length]);

  return (
    <figure className="group space-y-1.5">
      <div className="relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface">
        {ancora.asset_id ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={urlDaMidia(ancora.asset_id)} alt={ancora.rotulo}
              loading="lazy" className="h-full w-full object-cover" />

            <button onClick={onAmpliar} title="ampliar"
              className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all hover:bg-black/40 group-hover:opacity-100">
              <Maximize2 size={20} className="text-white" />
            </button>

            {/* Testar outra geração sem perder a atual: o X desliga a âncora,
                a imagem continua na biblioteca. */}
            <button onClick={onAposentar} title="desligar esta âncora"
              className="absolute right-1 top-1 rounded-full bg-black/70 p-1 text-slate-300 opacity-0 transition-opacity hover:bg-block hover:text-white group-hover:opacity-100">
              <X size={11} />
            </button>
          </>
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
      </div>

      <figcaption className="text-[11px] leading-tight text-slate-400">
        {ancora.rotulo}
        {ancora.detalhe && (
          <span className="block font-mono text-[10px] text-slate-600">
            {ancora.detalhe.width
              ? `${ancora.detalhe.width}×${ancora.detalhe.height} · `
              : ""}
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
              : ehRosto ? "Abre o gerador: fotos de referência, variações e modelo"
                : "Gera usando o rosto como referência"
          }
          className="btn-ghost w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
        >
          {ancora.asset_id ? <RefreshCw size={11} /> : <Sparkles size={11} />}
          {ancora.asset_id ? "gerar de novo" : "gerar"}
        </button>

        <Upload_ personaId={personaId} papel={ancora.papel} compacto aoEnviar={onMudou}>
          <UploadIcon size={11} /> enviar foto
        </Upload_>
      </div>
    </figure>
  );
}

function Lightbox({ assetId, onFechar }: { assetId: string; onFechar: () => void }) {
  return (
    <div onClick={onFechar}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/90 p-4">
      <button onClick={onFechar}
        className="absolute right-4 top-4 rounded-full bg-black/60 p-2 text-slate-300 hover:text-white">
        <X size={18} />
      </button>
      <a href={urlDaMidia(assetId)} download onClick={(e) => e.stopPropagation()}
        className="absolute right-16 top-4 rounded-full bg-black/60 p-2 text-slate-300 hover:text-white">
        <Download size={18} />
      </a>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={urlDaMidia(assetId)} alt="" onClick={(e) => e.stopPropagation()}
        className="max-h-full max-w-full rounded object-contain" />
    </div>
  );
}

/** As variações de rosto. A escolha é humana porque nenhuma métrica captura
 *  "esta é a personagem que eu imaginei". */
function EscolherRosto({ personaId, assets, onFechar, onEscolhido }: {
  personaId: string; assets: string[];
  onFechar: () => void; onEscolhido: () => void;
}) {
  const [salvando, setSalvando] = useState<string | null>(null);
  const [ampliada, setAmpliada] = useState<string | null>(null);

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
              As outras ficam na biblioteca.
            </p>
          </div>
          <button onClick={onFechar} className="text-slate-500 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        <div className={`grid gap-3 ${assets.length > 2 ? "grid-cols-3" : "grid-cols-2"}`}>
          {assets.map((id) => (
            <div key={id} className="group relative aspect-[3/4] overflow-hidden rounded border-2 border-transparent bg-surface hover:border-info">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={urlDaMidia(id)} alt="" className="h-full w-full object-cover" />

              <div className="absolute inset-0 flex items-center justify-center gap-2 bg-black/0 opacity-0 transition-all group-hover:bg-black/50 group-hover:opacity-100">
                <button onClick={() => setAmpliada(id)} title="ampliar"
                  className="rounded-full bg-black/70 p-2 text-white hover:bg-black">
                  <Maximize2 size={16} />
                </button>
                <button onClick={() => escolher(id)} disabled={!!salvando}
                  title="usar esta"
                  className="rounded-full bg-active/80 p-2 text-white hover:bg-active">
                  {salvando === id
                    ? <Loader2 size={16} className="animate-spin" />
                    : <Check size={16} />}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {ampliada && <Lightbox assetId={ampliada} onFechar={() => setAmpliada(null)} />}
    </div>
  );
}

/** Figurinos: a mesma personagem com outra roupa. */
export function Figurinos({ personaId, persona }: {
  personaId: string; persona?: any;
}) {
  const { data, mutate: reload } = useSWR(
    `/v1/media/personas/${personaId}/figurinos`, api, { refreshInterval: 8_000 },
  );
  const [abrir, setAbrir] = useState(false);
  const [job, setJob] = useState<string | null>(null);
  const [ampliada, setAmpliada] = useState<string | null>(null);

  const { data: estado } = useSWR(job ? `/v1/media/jobs/${job}` : null, api,
    { refreshInterval: 3_000 });

  // Mesmo motivo do bloco em `Ancora`: limpar o job é efeito, não render.
  useEffect(() => {
    if (!job || !estado || !["done", "error"].includes(estado.status)) return;
    setJob(null);
    if (estado.status === "done") reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estado?.status]);

  const figurinos = data?.figurinos ?? [];

  return (
    <section className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="label">Guarda-roupa</span>
          <p className="mt-0.5 text-xs text-slate-500">
            Cada figurino é uma folha de 4 painéis do mesmo rosto com outra
            roupa — escolhível no estúdio a cada foto.
          </p>
        </div>
        <button onClick={() => setAbrir(true)} className="btn-primary shrink-0">
          <Plus size={13} /> figurino
        </button>
      </div>

      {job && (
        <p className="flex items-center gap-1.5 text-xs text-info">
          <Loader2 size={12} className="animate-spin" /> gerando figurino…
        </p>
      )}
      {estado?.status === "error" && (
        <p className="flex items-start gap-1 text-xs text-red-300">
          <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {estado.error}
        </p>
      )}

      {figurinos.length === 0 ? (
        <p className="text-xs text-slate-600">
          nenhum figurino ainda — o estúdio usa a folha de personagem como
          roupa padrão
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {figurinos.map((w: any) => (
            <figure key={w.id} className="group space-y-1">
              <div className="relative aspect-video overflow-hidden rounded border border-surface-border bg-surface">
                {w.asset_id ? (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={urlDaMidia(w.asset_id)} alt={w.name} loading="lazy"
                      className="h-full w-full object-cover" />
                    <button onClick={() => setAmpliada(w.asset_id)}
                      className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all hover:bg-black/40 group-hover:opacity-100">
                      <Maximize2 size={16} className="text-white" />
                    </button>
                  </>
                ) : (
                  <div className="flex h-full items-center justify-center text-slate-700">
                    <Shirt size={16} />
                  </div>
                )}
              </div>
              <figcaption className="truncate text-[11px] text-slate-400" title={w.description}>
                {w.name}
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      {abrir && (
        <GerarFigurino
          personaId={personaId} genero={persona?.gender}
          onFechar={() => setAbrir(false)}
          onEnfileirado={(jobId) => { setAbrir(false); setJob(jobId); }}
        />
      )}
      {ampliada && <Lightbox assetId={ampliada} onFechar={() => setAmpliada(null)} />}
    </section>
  );
}

/** Galeria do que a persona já produziu. */
export function GaleriaDaPersona({ personaId }: { personaId: string }) {
  const { data } = useSWR(
    `/v1/media?persona_id=${personaId}&role=generated&per_page=24`, api,
    { refreshInterval: 15_000 },
  );
  const [ampliada, setAmpliada] = useState<string | null>(null);
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
          <button key={m.id} onClick={() => setAmpliada(m.id)} title={m.prompt ?? ""}
            className="group relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface">
            {m.kind === "video" ? (
              <video src={urlDaMidia(m.id)} className="h-full w-full object-cover" muted />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={urlDaMidia(m.id)} alt="" loading="lazy"
                className="h-full w-full object-cover transition-transform group-hover:scale-105" />
            )}
          </button>
        ))}
      </div>
      {ampliada && <Lightbox assetId={ampliada} onFechar={() => setAmpliada(null)} />}
    </section>
  );
}
