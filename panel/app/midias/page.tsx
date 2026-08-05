"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  Images, ChevronLeft, ChevronRight, X, Download, Trash2, Copy,
  Film, ImageIcon, AlertTriangle,
} from "lucide-react";
import { api, mutate, urlDaMidia } from "@/lib/api";
import { tempoRelativo } from "@/lib/contas";

/** Os papéis, com o que cada um significa na operação. */
const PAPEL: Record<string, { rotulo: string; cor: string; ajuda: string }> = {
  face: { rotulo: "rosto", cor: "border-active/40 bg-active/10 text-active",
          ajuda: "A âncora nº 1. Toda geração a reenvia como referência." },
  character_sheet: { rotulo: "folha de personagem", cor: "border-l1/40 bg-l1/10 text-l1",
          ajuda: "Turnaround de 4 painéis. É de onde vem a roupa." },
  close_up_face: { rotulo: "close de rosto", cor: "border-l1/40 bg-l1/10 text-l1",
          ajuda: "Textura de pele e detalhe facial." },
  close_up_detail: { rotulo: "cartela de detalhe", cor: "border-l1/40 bg-l1/10 text-l1",
          ajuda: "Macro de olho, sobrancelha, lábio e mão." },
  wardrobe: { rotulo: "figurino", cor: "border-l3/40 bg-l3/10 text-l3",
          ajuda: "A mesma personagem com outra roupa." },
  brand_product: { rotulo: "produto", cor: "border-warn/40 bg-warn/10 text-warn",
          ajuda: "Folha de 6 ângulos de um produto de marca." },
  reference: { rotulo: "referência", cor: "border-slate-600 bg-slate-700/30 text-slate-400",
          ajuda: "Foto extra do mesmo rosto, ou candidata a âncora." },
  generated: { rotulo: "gerado", cor: "border-surface-border bg-surface text-slate-500",
          ajuda: "Conteúdo produzido." },
  inspiration: { rotulo: "inspiração", cor: "border-surface-border bg-surface text-slate-500",
          ajuda: "" },
};

export default function Midias() {
  const [filtros, setFiltros] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [aberta, setAberta] = useState<any>(null);

  const query = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), per_page: "60" });
    for (const [k, v] of Object.entries(filtros)) if (v) p.set(k, v);
    return p.toString();
  }, [filtros, page]);

  const { data, mutate: reload } = useSWR(`/v1/media?${query}`, api, {
    keepPreviousData: true, refreshInterval: 30_000,
  });
  const { data: personas } = useSWR("/v1/personas", api);

  const midias = data?.media ?? [];

  function filtrar(chave: string, valor: string) {
    setFiltros((f) => ({ ...f, [chave]: f[chave] === valor ? "" : valor }));
    setPage(1);
  }

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
            <Images size={18} className="text-slate-500" /> Mídias
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Tudo o que foi gerado ou enviado. Os arquivos vivem aqui, não no CDN
            do provider — as URLs deles expiram, e uma imagem de identidade
            perdida não se regenera igual.
          </p>
        </div>
        <span className="shrink-0 text-sm text-slate-500">{data?.total ?? 0} peças</span>
      </header>

      {/* ----------------------------------------------------------- filtros */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2 text-xs">
          <span className="w-16 shrink-0 text-slate-600">tipo</span>
          <div className="flex gap-1">
            {[["image", "imagens"], ["video", "vídeos"]].map(([v, l]) => (
              <Botao key={v} ativo={filtros.kind === v} onClick={() => filtrar("kind", v)}>
                {l}
              </Botao>
            ))}
          </div>
        </div>

        <div className="flex items-start gap-2 text-xs">
          <span className="w-16 shrink-0 pt-1 text-slate-600">papel</span>
          <div className="flex flex-wrap gap-1">
            {Object.entries(PAPEL).map(([v, meta]) => (
              <Botao key={v} ativo={filtros.role === v} titulo={meta.ajuda}
                onClick={() => filtrar("role", v)}>
                {meta.rotulo}
              </Botao>
            ))}
          </div>
        </div>

        <div className="flex items-start gap-2 text-xs">
          <span className="w-16 shrink-0 pt-1 text-slate-600">persona</span>
          <div className="flex flex-wrap gap-1">
            {(personas?.personas ?? []).map((p: any) => (
              <Botao key={p.id} ativo={filtros.persona_id === p.id}
                onClick={() => filtrar("persona_id", p.id)}>
                {p.name}
              </Botao>
            ))}
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------ grade */}
      {midias.length === 0 ? (
        <div className="card text-center text-sm text-slate-500">
          Nenhuma mídia com esses filtros.
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-7 xl:grid-cols-8">
          {midias.map((m: any) => (
            <Miniatura key={m.id} midia={m} onAbrir={() => setAberta(m)} />
          ))}
        </div>
      )}

      {(data?.pages ?? 1) > 1 && (
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>página {data.page} de {data.pages}</span>
          <div className="flex gap-1">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1} className="btn-ghost">
              <ChevronLeft size={12} /> anterior
            </button>
            <button onClick={() => setPage((p) => p + 1)}
              disabled={page >= data.pages} className="btn-ghost">
              próxima <ChevronRight size={12} />
            </button>
          </div>
        </div>
      )}

      {aberta && (
        <Detalhe midia={aberta} onFechar={() => setAberta(null)}
          onApagada={() => { setAberta(null); reload(); }} />
      )}
    </div>
  );
}

function Botao({ ativo, titulo, onClick, children }: {
  ativo: boolean; titulo?: string; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button title={titulo} onClick={onClick}
      className={`rounded border px-2 py-0.5 ${
        ativo ? "border-info/50 bg-info/15 text-info"
              : "border-surface-border text-slate-400 hover:text-slate-200"
      }`}>
      {children}
    </button>
  );
}

function Miniatura({ midia, onAbrir }: { midia: any; onAbrir: () => void }) {
  const papel = PAPEL[midia.role] ?? PAPEL.generated;
  return (
    <button onClick={onAbrir}
      className="group relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface">
      {midia.kind === "video" ? (
        <>
          <video src={urlDaMidia(midia.id)} className="h-full w-full object-cover" muted />
          <Film size={12} className="absolute right-1 top-1 text-white/80" />
        </>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={urlDaMidia(midia.id)} alt="" loading="lazy"
          className="h-full w-full object-cover transition-transform group-hover:scale-105" />
      )}
      <span className={`absolute left-1 top-1 rounded border px-1 py-0.5 text-[9px] ${papel.cor}`}>
        {papel.rotulo}
      </span>
      {midia.persona_name && (
        <span className="absolute bottom-0 left-0 right-0 truncate bg-black/70 px-1 py-0.5 text-[9px] text-slate-300">
          {midia.persona_name}
        </span>
      )}
    </button>
  );
}

function Detalhe({ midia, onFechar, onApagada }: {
  midia: any; onFechar: () => void; onApagada: () => void;
}) {
  const [erro, setErro] = useState<string | null>(null);
  const papel = PAPEL[midia.role] ?? PAPEL.generated;

  async function apagar() {
    if (!confirm("Apagar este registro? O arquivo em disco permanece.")) return;
    try {
      await mutate(`/v1/media/${midia.id}`, "DELETE");
      onApagada();
    } catch (e) {
      setErro(String(e).replace("Error: ", ""));
    }
  }

  return (
    <div onClick={onFechar}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4">
      <div onClick={(e) => e.stopPropagation()}
        className="flex max-h-full w-full max-w-6xl gap-4 overflow-hidden rounded-lg border border-surface-border bg-surface-raised p-4">

        <div className="flex min-w-0 flex-1 items-center justify-center">
          {midia.kind === "video" ? (
            <video src={urlDaMidia(midia.id)} controls
              className="max-h-[80vh] max-w-full rounded" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={urlDaMidia(midia.id)} alt=""
              className="max-h-[80vh] max-w-full rounded object-contain" />
          )}
        </div>

        <aside className="w-80 shrink-0 space-y-3 overflow-y-auto">
          <div className="flex items-start justify-between">
            <span className={`rounded border px-1.5 py-0.5 text-xs ${papel.cor}`}>
              {papel.rotulo}
            </span>
            <button onClick={onFechar} className="text-slate-500 hover:text-slate-200">
              <X size={16} />
            </button>
          </div>

          {papel.ajuda && <p className="text-xs text-slate-500">{papel.ajuda}</p>}

          <dl className="space-y-1.5 border-t border-surface-border pt-3 text-xs">
            {midia.persona_name && (
              <Linha rotulo="persona">{midia.persona_name}</Linha>
            )}
            {midia.model && <Linha rotulo="modelo">{midia.model}</Linha>}
            {midia.provider && <Linha rotulo="provider">{midia.provider}</Linha>}
            {midia.aspect_ratio && <Linha rotulo="proporção">{midia.aspect_ratio}</Linha>}
            {midia.bytes && (
              <Linha rotulo="tamanho">{Math.round(midia.bytes / 1024)} KB</Linha>
            )}
            <Linha rotulo="criada">{tempoRelativo(midia.created_at)}</Linha>
            {midia.source && <Linha rotulo="origem">{midia.source}</Linha>}
          </dl>

          {midia.prompt && (
            <div className="space-y-1 border-t border-surface-border pt-3">
              <div className="flex items-center justify-between">
                <span className="label">Prompt</span>
                <button onClick={() => navigator.clipboard?.writeText(midia.prompt)}
                  title="copiar" className="text-slate-600 hover:text-slate-300">
                  <Copy size={12} />
                </button>
              </div>
              {/* O prompt exato é o que permite refazer ou entender por que a
                  imagem ficou assim. Guardá-lo é barato; perdê-lo não tem volta. */}
              <p className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded border border-surface-border bg-surface p-2 font-mono text-[10px] leading-relaxed text-slate-400">
                {midia.prompt}
              </p>
            </div>
          )}

          {erro && (
            <p className="flex items-start gap-1 text-xs text-red-300">
              <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {erro}
            </p>
          )}

          <div className="flex gap-2 border-t border-surface-border pt-3">
            <a href={urlDaMidia(midia.id)} download className="btn-ghost">
              <Download size={12} /> baixar
            </a>
            <button onClick={apagar} className="btn-ghost !border-block/40 !text-block">
              <Trash2 size={12} /> apagar
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Linha({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-slate-600">{rotulo}</dt>
      <dd className="truncate text-slate-300">{children}</dd>
    </div>
  );
}
