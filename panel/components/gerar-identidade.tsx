"use client";

import { useState } from "react";
import {
  X, Sparkles, Loader2, AlertTriangle, Plus, Trash2, User, Palette, Layers,
} from "lucide-react";
import { mutate, urlDaMidia } from "@/lib/api";
import { Upload_ } from "@/components/upload";
import { MotorPicker, Motor, motorSalvo, salvarMotor } from "@/components/motor";

/**
 * Geração do rosto — o ponto de partida da identidade.
 *
 * Aceita quantas fotos de referência o operador tiver, cada uma com um papel
 * declarado. O papel não é etiqueta: ele vira uma instrução em texto que diz
 * ao modelo o que fazer com aquela imagem. Sem isso as fotos colapsam num
 * borrão — a roupa vem do rosto, o rosto vem da referência de estilo.
 */

const PAPEL = {
  face: {
    rotulo: "rosto", icone: User,
    cor: "border-active/50 bg-active/10 text-active",
    ajuda: "Recria este rosto. Se houver descrição física, ela manda no tom de pele e cabelo, e a foto entra como geometria facial.",
    exemploNota: "ex.: use o formato dos olhos desta",
  },
  style: {
    rotulo: "estilo", icone: Palette,
    cor: "border-l3/50 bg-l3/10 text-l3",
    ajuda: "Copia pose, roupa, cor, cenário e clima. NUNCA o rosto — o modelo é instruído explicitamente a ignorar a identidade de quem aparece nela.",
    exemploNota: "ex.: use só a iluminação desta",
  },
  extra: {
    rotulo: "outro ângulo", icone: Layers,
    cor: "border-l1/50 bg-l1/10 text-l1",
    ajuda: "A MESMA pessoa em outro ângulo ou luz. Reforça a identidade; o modelo é avisado de que não é outra pessoa.",
    exemploNota: "",
  },
} as const;

//: O rosto vira referência de todo o resto — vertical é o enquadramento
//: que preserva mais pixels de face por imagem.
const ROSTO_ASPECTOS = ["9:16", "3:4", "4:5", "1:1"];

type Papel = keyof typeof PAPEL;
type Ref = { asset_id: string; papel: Papel; nota: string };

export function GerarIdentidade({
  personaId, personaNome, temDescricao, onFechar, onEnfileirado,
}: {
  personaId: string;
  personaNome: string;
  temDescricao: boolean;
  onFechar: () => void;
  onEnfileirado: (jobId: string) => void;
}) {
  const [refs, setRefs] = useState<Ref[]>([]);
  const [quantidade, setQuantidade] = useState(3);
  const [seed, setSeed] = useState("");
  const [motor, setMotor] = useState<Motor>(() => {
    const m = motorSalvo();
    // Rosto é sempre vertical: é o enquadramento das três variações e o que
    // a folha de personagem espera receber depois.
    return ROSTO_ASPECTOS.includes(m.aspect) ? m : { ...m, aspect: "9:16" };
  });
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const semNada = !temDescricao && refs.length === 0;

  async function gerar() {
    setEnviando(true);
    setErro(null);
    salvarMotor(motor);
    try {
      const r = await mutate(
        `/v1/media/personas/${personaId}/ancoras/face`, "POST",
        {
          quantidade,
          seed: seed ? Number(seed) : null,
          modelo: motor.modelo || null,
          aspect_ratio: motor.aspect || null,
          resolucao: motor.resolucao,
          referencias: refs,
        },
      );
      onEnfileirado(r.job_id);
    } catch (e) {
      setErro(String(e).replace("Error: ", ""));
      setEnviando(false);
    }
  }

  return (
    <div onClick={onFechar}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/85 p-4">
      <div onClick={(e) => e.stopPropagation()}
        className="my-8 w-full max-w-3xl space-y-4 rounded-lg border border-surface-border bg-surface-raised p-5">

        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-medium text-slate-100">
              Gerar rosto — {personaNome}
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              O rosto é a âncora nº 1. Tudo o mais deriva dele.
            </p>
          </div>
          <button onClick={onFechar} className="text-slate-500 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        {/* ------------------------------------------------------ referências */}
        <section className="space-y-2">
          <div className="flex items-baseline justify-between">
            <span className="label">Fotos de referência</span>
            <span className="text-xs text-slate-600">
              quantas você tiver — opcional
            </span>
          </div>

          {refs.length > 0 && (
            <div className="space-y-2">
              {refs.map((r, i) => {
                const meta = PAPEL[r.papel];
                return (
                  <div key={r.asset_id + i}
                    className="flex gap-3 rounded border border-surface-border bg-surface/50 p-2">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={urlDaMidia(r.asset_id)} alt=""
                      className="h-20 w-20 shrink-0 rounded object-cover" />

                    <div className="min-w-0 flex-1 space-y-1.5">
                      <div className="flex flex-wrap gap-1">
                        {(Object.keys(PAPEL) as Papel[]).map((p) => {
                          const m = PAPEL[p];
                          const Icone = m.icone;
                          return (
                            <button key={p} title={m.ajuda}
                              onClick={() => setRefs((v) =>
                                v.map((x, j) => j === i ? { ...x, papel: p } : x))}
                              className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${
                                r.papel === p ? m.cor
                                  : "border-surface-border text-slate-500 hover:text-slate-300"
                              }`}>
                              <Icone size={10} /> {m.rotulo}
                            </button>
                          );
                        })}
                      </div>

                      <p className="text-[10px] leading-tight text-slate-600">
                        {meta.ajuda}
                      </p>

                      <input
                        className="input !mt-0 !py-1 text-xs"
                        placeholder={meta.exemploNota || "observação (opcional)"}
                        value={r.nota}
                        onChange={(e) => setRefs((v) =>
                          v.map((x, j) => j === i ? { ...x, nota: e.target.value } : x))}
                      />
                    </div>

                    <button onClick={() => setRefs((v) => v.filter((_, j) => j !== i))}
                      title="remover" className="shrink-0 text-slate-600 hover:text-block">
                      <Trash2 size={13} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          <Upload_
            personaId={personaId} papel="reference"
            aoEnviar={(r) => setRefs((v) => [
              ...v,
              { asset_id: r.asset_id, papel: v.length === 0 ? "face" : "extra", nota: "" },
            ])}
          >
            <Plus size={14} /> adicionar foto de referência
          </Upload_>

          {refs.length > 1 && (
            <p className="text-[10px] leading-relaxed text-slate-600">
              A ordem é reorganizada sozinha — rosto primeiro, estilo depois,
              ângulos extras no fim. A instrução enviada ao modelo fala em
              &quot;a primeira imagem&quot; e &quot;a segunda&quot;, então a
              ordem precisa bater.
            </p>
          )}
        </section>

        {/* --------------------------------------------------------- avisos */}
        {semNada && (
          <div className="flex items-start gap-2 rounded border border-block/30 bg-block/5 p-2.5 text-xs text-red-200">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>
              Sem descrição física e sem foto, o modelo inventa uma pessoa
              diferente a cada geração. Dê pelo menos uma das duas.
            </span>
          </div>
        )}

        {!temDescricao && refs.length > 0 && (
          <p className="flex items-start gap-1.5 text-xs text-amber-200/80">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            Sem descrição física, a foto de rosto é recriada fielmente — pele,
            cabelo e olhos vêm dela. Com descrição, a foto vira só geometria e
            o texto manda no resto.
          </p>
        )}

        {/* -------------------------------------------------------- opções */}
        <MotorPicker valor={motor} onChange={setMotor} aspectos={ROSTO_ASPECTOS} />

        <section className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="label">variações</span>
            <select className="input" value={quantidade}
              onChange={(e) => setQuantidade(Number(e.target.value))}>
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span className="mt-0.5 block text-[10px] text-slate-600">
              cada uma é uma geração cobrada
            </span>
          </label>
          <label className="block">
            <span className="label">semente</span>
            <input className="input font-mono" placeholder="aleatória"
              value={seed}
              onChange={(e) => setSeed(e.target.value.replace(/\D/g, ""))} />
            <span className="mt-0.5 block text-[10px] text-slate-600">
              fixa repete o mesmo sorteio de pose e cenário
            </span>
          </label>
        </section>

        {erro && (
          <p className="flex items-start gap-1 text-xs text-red-300">
            <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {erro}
          </p>
        )}

        <div className="flex gap-2 border-t border-surface-border pt-3">
          <button onClick={gerar} disabled={enviando || semNada || !motor.modelo}
            className="btn-primary">
            {enviando
              ? <><Loader2 size={14} className="animate-spin" /> enfileirando…</>
              : <><Sparkles size={14} /> gerar {quantidade} variaç{quantidade > 1 ? "ões" : "ão"}</>}
          </button>
          <button onClick={onFechar} className="btn-ghost">cancelar</button>
        </div>
      </div>
    </div>
  );
}
