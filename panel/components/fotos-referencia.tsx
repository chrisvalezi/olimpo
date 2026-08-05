"use client";

import { useState } from "react";
import useSWR from "swr";
import { X, Images, Info, Loader2 } from "lucide-react";
import { api, mutate, urlDaMidia } from "@/lib/api";
import { Upload_ } from "@/components/upload";

/**
 * Acervo de fotos de referência da persona.
 *
 * Diferente das quatro âncoras: estas não têm posição fixa no prompt nem
 * papel semântico. São ângulos extras e corpo inteiro que entram como
 * reforço em TODA geração — quanto mais a personagem aparece, menos sobra
 * para o modelo inventar. Corpo inteiro é o que mais falta: as âncoras são
 * rosto e closes, então altura e proporção ficam por conta do modelo até
 * existir uma foto de corpo aqui.
 *
 * Ficam abaixo das âncoras porque é essa a hierarquia: sem rosto elas não
 * salvam nada, e com rosto elas melhoram tudo.
 */
export function FotosDeReferencia({
  personaId, onAmpliar,
}: {
  personaId: string;
  onAmpliar?: (assetId: string) => void;
}) {
  const { data, mutate: reload } = useSWR(
    `/v1/media/personas/${personaId}/identidade`, api,
  );
  const [removendo, setRemovendo] = useState<string | null>(null);

  const extras: string[] = data?.extras ?? [];

  async function anexar(assetId: string) {
    await mutate(`/v1/media/personas/${personaId}/referencias/${assetId}`, "POST");
    reload();
  }

  async function remover(assetId: string) {
    setRemovendo(assetId);
    try {
      await mutate(
        `/v1/media/personas/${personaId}/referencias/${assetId}`, "DELETE",
      );
      reload();
    } finally {
      setRemovendo(null);
    }
  }

  return (
    <section className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="label flex items-center gap-1.5">
            <Images size={12} className="text-slate-500" /> Fotos de referência
          </span>
          <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-slate-500">
            Mais fotos da mesma pessoa — de preferência <strong>corpo
            inteiro</strong> e outros ângulos. Entram como referência extra em
            toda geração, junto das âncoras.
          </p>
        </div>
        {extras.length > 0 && (
          <span className="shrink-0 text-xs text-slate-500">
            {extras.length}
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2.5 sm:grid-cols-5 lg:grid-cols-8">
        {extras.map((id) => (
          <div key={id}
            className="group relative aspect-[3/4] overflow-hidden rounded border border-surface-border">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={urlDaMidia(id)} alt=""
              onClick={() => onAmpliar?.(id)}
              className="h-full w-full cursor-zoom-in object-cover" />
            <button
              onClick={() => remover(id)}
              disabled={removendo === id}
              title="tirar do acervo — a imagem continua na biblioteca"
              className="absolute right-1 top-1 rounded-full bg-black/70 p-1 text-slate-300 opacity-0 transition hover:text-white group-hover:opacity-100"
            >
              {removendo === id
                ? <Loader2 size={11} className="animate-spin" />
                : <X size={11} />}
            </button>
          </div>
        ))}

        <div className="aspect-[3/4]">
          <Upload_ personaId={personaId} papel="reference" multiplo compacto
            aoEnviar={(r) => anexar(r.asset_id)}>
            <span className="flex flex-col items-center gap-1 py-6 text-center">
              <span className="text-lg leading-none">+</span>
              <span className="text-[10px] leading-tight">fotos</span>
            </span>
          </Upload_>
        </div>
      </div>

      <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
        <Info size={11} className="mt-0.5 shrink-0" />
        O X tira a foto do acervo — a imagem continua na biblioteca de mídias
        e pode ser reanexada. Nada é apagado aqui.
      </p>
    </section>
  );
}
