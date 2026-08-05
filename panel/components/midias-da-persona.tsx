"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  X, Download, ChevronLeft, ChevronRight, Info, Trash2, AlertTriangle,
} from "lucide-react";
import { api, mutate, urlDaMidia } from "@/lib/api";

/**
 * Tudo o que existe de mídia desta persona.
 *
 * A versão anterior mostrava só `role=generated`, e isso escondia a maior
 * parte do acervo: as quatro âncoras, os figurinos e as fotos de referência
 * ficavam invisíveis aqui. Quem procurava "aquela foto que eu gerei semana
 * passada" não tinha onde procurar.
 *
 * Os papéis não são categorias decorativas — cada um tem consequência sobre
 * o que a geração faz com a imagem. Por isso viram filtro, com a contagem
 * ao lado: ver "identidade 4" é ver que a persona está completa.
 */

const PAPEL: Record<string, { rotulo: string; cor: string }> = {
  face:             { rotulo: "rosto",        cor: "text-active" },
  character_sheet:  { rotulo: "folha",        cor: "text-active" },
  close_up_face:    { rotulo: "close",        cor: "text-active" },
  close_up_detail:  { rotulo: "detalhe",      cor: "text-active" },
  wardrobe:         { rotulo: "figurino",     cor: "text-l3" },
  reference:        { rotulo: "referência",   cor: "text-l1" },
  generated:        { rotulo: "produzido",    cor: "text-info" },
  product:          { rotulo: "produto",      cor: "text-warn" },
};

//: Agrupamentos que valem como filtro. "identidade" junta as quatro âncoras
//: porque é assim que se pensa nelas — não como quatro papéis soltos.
const GRUPOS: { chave: string; rotulo: string; papeis: string[] | null }[] = [
  { chave: "todas",      rotulo: "todas",      papeis: null },
  { chave: "identidade", rotulo: "identidade", papeis: ["face", "character_sheet", "close_up_face", "close_up_detail"] },
  { chave: "wardrobe",   rotulo: "figurinos",  papeis: ["wardrobe"] },
  { chave: "generated",  rotulo: "produzido",  papeis: ["generated"] },
  { chave: "reference",  rotulo: "referências", papeis: ["reference"] },
];

const POR_PAGINA = 48;

export function MidiasDaPersona({ personaId }: { personaId: string }) {
  const [grupo, setGrupo] = useState("todas");
  const [pagina, setPagina] = useState(1);
  const [aberta, setAberta] = useState<number | null>(null);

  const alvo = GRUPOS.find((g) => g.chave === grupo);
  // Um papel por requisição é o que a API oferece; para "identidade" o
  // filtro é feito aqui, sobre a página já carregada.
  const papelUnico = alvo?.papeis?.length === 1 ? alvo.papeis[0] : null;

  const { data, mutate: reload } = useSWR(
    `/v1/media?persona_id=${personaId}&per_page=${POR_PAGINA}&page=${pagina}`
      + (papelUnico ? `&role=${papelUnico}` : ""),
    api,
    { refreshInterval: 20_000 },
  );

  const todas = data?.media ?? [];
  const midias = alvo?.papeis && !papelUnico
    ? todas.filter((m: any) => alvo.papeis!.includes(m.role))
    : todas;

  // Contagem por papel da página corrente. Dito como "nesta página" em vez
  // de fingir um total: o servidor não devolve agregado por papel, e um
  // número que parece total mas não é vale menos que nenhum.
  const contagem: Record<string, number> = {};
  for (const m of todas) contagem[m.role] = (contagem[m.role] ?? 0) + 1;

  async function apagar(id: string) {
    if (!confirm("Apagar esta mídia? Isso não tem volta.")) return;
    try {
      await mutate(`/v1/media/${id}`, "DELETE");
      setAberta(null);
      reload();
    } catch (e) {
      alert(String(e).replace("Error: ", ""));
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {GRUPOS.map((g) => {
            const n = g.papeis
              ? g.papeis.reduce((s, p) => s + (contagem[p] ?? 0), 0)
              : todas.length;
            return (
              <button
                key={g.chave}
                onClick={() => { setGrupo(g.chave); setPagina(1); }}
                className={`rounded border px-2.5 py-1 text-xs transition ${
                  grupo === g.chave
                    ? "border-info/50 bg-info/10 text-info"
                    : "border-surface-border text-slate-500 hover:text-slate-300"
                }`}
              >
                {g.rotulo}
                {n > 0 && <span className="ml-1.5 opacity-60">{n}</span>}
              </button>
            );
          })}
        </div>
        {data && (
          <span className="text-xs text-slate-500">
            {data.total} no total · página {data.page}/{data.pages}
          </span>
        )}
      </div>

      {!data ? (
        <p className="text-sm text-slate-600">carregando…</p>
      ) : midias.length === 0 ? (
        <p className="rounded border border-dashed border-surface-border p-6 text-center text-sm text-slate-600">
          nada aqui ainda
        </p>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-8">
          {midias.map((m: any, i: number) => {
            const papel = PAPEL[m.role] ?? { rotulo: m.role, cor: "text-slate-400" };
            return (
              <button
                key={m.id}
                onClick={() => setAberta(i)}
                title={m.label ?? m.prompt ?? ""}
                className="group relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface"
              >
                {m.kind === "video" ? (
                  <video src={urlDaMidia(m.id)} muted
                    className="h-full w-full object-cover" />
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={urlDaMidia(m.id)} alt="" loading="lazy"
                    className="h-full w-full object-cover transition-transform group-hover:scale-105" />
                )}
                <span className={`absolute bottom-0 left-0 right-0 truncate bg-black/70 px-1 py-0.5 text-[9px] ${papel.cor}`}>
                  {papel.rotulo}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {data && data.pages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button onClick={() => setPagina((p) => Math.max(1, p - 1))}
            disabled={pagina <= 1} className="btn-ghost">
            <ChevronLeft size={12} /> anterior
          </button>
          <button onClick={() => setPagina((p) => Math.min(data.pages, p + 1))}
            disabled={pagina >= data.pages} className="btn-ghost">
            próxima <ChevronRight size={12} />
          </button>
        </div>
      )}

      {aberta !== null && midias[aberta] && (
        <Detalhe
          midia={midias[aberta]}
          temAnterior={aberta > 0}
          temProxima={aberta < midias.length - 1}
          onAnterior={() => setAberta(aberta - 1)}
          onProxima={() => setAberta(aberta + 1)}
          onFechar={() => setAberta(null)}
          onApagar={() => apagar(midias[aberta].id)}
        />
      )}
    </section>
  );
}

/** Visor com o que a imagem É, não só a imagem.
 *
 *  O prompt e o modelo importam mais do que parece: é a partir deles que se
 *  repete um resultado bom, e sem eles uma geração que deu certo é
 *  irrepetível. */
function Detalhe({
  midia, temAnterior, temProxima, onAnterior, onProxima, onFechar, onApagar,
}: {
  midia: any;
  temAnterior: boolean; temProxima: boolean;
  onAnterior: () => void; onProxima: () => void;
  onFechar: () => void; onApagar: () => void;
}) {
  const [verPrompt, setVerPrompt] = useState(false);
  const papel = PAPEL[midia.role] ?? { rotulo: midia.role, cor: "text-slate-400" };
  const ehAncora = ["face", "character_sheet", "close_up_face", "close_up_detail"]
    .includes(midia.role);

  return (
    <div onClick={onFechar}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/90 p-4">
      <div onClick={(e) => e.stopPropagation()}
        className="flex max-h-full w-full max-w-6xl gap-4 overflow-hidden">

        <div className="relative flex min-w-0 flex-1 items-center justify-center">
          {temAnterior && (
            <button onClick={onAnterior}
              className="absolute left-0 z-10 rounded-full bg-black/60 p-2 text-slate-300 hover:text-white">
              <ChevronLeft size={20} />
            </button>
          )}
          {midia.kind === "video" ? (
            <video src={urlDaMidia(midia.id)} controls
              className="max-h-[85vh] max-w-full rounded object-contain" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={urlDaMidia(midia.id)} alt=""
              className="max-h-[85vh] max-w-full rounded object-contain" />
          )}
          {temProxima && (
            <button onClick={onProxima}
              className="absolute right-0 z-10 rounded-full bg-black/60 p-2 text-slate-300 hover:text-white">
              <ChevronRight size={20} />
            </button>
          )}
        </div>

        <aside className="w-72 shrink-0 space-y-3 overflow-y-auto rounded-lg border border-surface-border bg-surface-raised p-4">
          <div className="flex items-start justify-between">
            <div>
              <span className={`text-xs ${papel.cor}`}>{papel.rotulo}</span>
              {midia.label && (
                <p className="text-sm text-slate-200">{midia.label}</p>
              )}
            </div>
            <button onClick={onFechar} className="text-slate-500 hover:text-slate-200">
              <X size={16} />
            </button>
          </div>

          <dl className="space-y-1 text-xs">
            {[
              ["modelo", midia.model],
              ["provedor", midia.provider],
              ["formato", midia.aspect_ratio],
              ["dimensões", midia.width ? `${midia.width}×${midia.height}` : null],
              ["tamanho", midia.bytes ? `${Math.round(midia.bytes / 1024)} KB` : null],
              ["origem", midia.source],
            ].filter(([, v]) => v).map(([k, v]) => (
              <div key={k as string} className="flex justify-between gap-2">
                <dt className="text-slate-600">{k}</dt>
                <dd className="truncate font-mono text-slate-400">{v as string}</dd>
              </div>
            ))}
          </dl>

          {midia.prompt && (
            <div>
              <button onClick={() => setVerPrompt((v) => !v)}
                className="btn-ghost w-full justify-center">
                <Info size={11} /> {verPrompt ? "esconder" : "ver"} prompt
              </button>
              {verPrompt && (
                <p className="mt-2 max-h-60 overflow-y-auto rounded border border-surface-border bg-surface p-2 font-mono text-[10px] leading-relaxed text-slate-500">
                  {midia.prompt}
                </p>
              )}
            </div>
          )}

          <div className="flex gap-2 border-t border-surface-border pt-3">
            <a href={urlDaMidia(midia.id)} download className="btn-ghost">
              <Download size={12} /> baixar
            </a>
            <button onClick={onApagar} className="btn-ghost hover:text-red-300">
              <Trash2 size={12} /> apagar
            </button>
          </div>

          {ehAncora && (
            <p className="flex items-start gap-1 text-[10px] leading-relaxed text-amber-200/70">
              <AlertTriangle size={11} className="mt-0.5 shrink-0" />
              Esta é uma âncora de identidade. Apagar exige desligá-la da
              persona antes — o X na seção de identidade faz isso.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
