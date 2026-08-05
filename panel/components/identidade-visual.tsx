"use client";

import useSWR from "swr";
import { AlertTriangle, ImageOff, Info } from "lucide-react";
import { api, urlDaMidia } from "@/lib/api";

/**
 * As quatro âncoras de identidade de uma persona.
 *
 * Não há semente fixa nem modelo treinado por trás: a personagem só se
 * mantém a mesma porque estas imagens são reenviadas como referência a cada
 * geração — e porque a ORDEM delas é o que o prompt referencia. A primeira é
 * o rosto, a segunda é a roupa. Trocar a ordem faz o modelo copiar a roupa
 * da referência facial.
 *
 * Por isso a posição aparece na tela: ela é contrato, não enfeite.
 */
export function IdentidadeVisual({ personaId }: { personaId: string }) {
  const { data } = useSWR(`/v1/media/personas/${personaId}/identidade`, api);
  if (!data) return null;

  const { ancoras, completas, pode_gerar_com_identidade } = data;

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between">
        <span className="label">Identidade visual</span>
        <span className={`text-xs ${completas === 4 ? "text-active" : "text-warn"}`}>
          {completas}/4 âncoras
        </span>
      </div>

      {!pode_gerar_com_identidade && (
        <div className="flex items-start gap-2 rounded border border-block/30 bg-block/5 p-2.5 text-xs text-red-200">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>
            Sem a referência de rosto não há identidade a preservar: cada
            geração produz uma pessoa diferente. Gere o rosto antes de
            qualquer outra coisa.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {ancoras.map((a: any) => (
          <figure key={a.papel} className="space-y-1">
            <div className="relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface">
              {a.asset_id ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={urlDaMidia(a.asset_id)}
                  alt={a.rotulo}
                  loading="lazy"
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="flex h-full items-center justify-center text-slate-700">
                  <ImageOff size={18} />
                </div>
              )}
              <span className="absolute left-1 top-1 rounded bg-black/70 px-1 py-0.5 font-mono text-[10px] text-slate-300">
                @image{a.posicao}
              </span>
            </div>
            <figcaption className="text-[10px] leading-tight text-slate-500">
              {a.rotulo}
              {a.detalhe && (
                <span className="block text-slate-700">
                  {Math.round(a.detalhe.bytes / 1024)} KB
                </span>
              )}
            </figcaption>
          </figure>
        ))}
      </div>

      <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
        <Info size={11} className="mt-0.5 shrink-0" />
        A numeração é o que o prompt referencia. A ordem é fixa de propósito —
        o rosto sempre em primeiro, a roupa em segundo.
      </p>
    </section>
  );
}

/** Galeria do que a persona já produziu. */
export function GaleriaDaPersona({ personaId }: { personaId: string }) {
  const { data } = useSWR(
    `/v1/media?persona_id=${personaId}&role=generated&per_page=24`, api,
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
          <a
            key={m.id}
            href={urlDaMidia(m.id)}
            target="_blank"
            rel="noreferrer"
            title={m.prompt ?? m.label ?? ""}
            className="group relative aspect-[3/4] overflow-hidden rounded border border-surface-border bg-surface"
          >
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
