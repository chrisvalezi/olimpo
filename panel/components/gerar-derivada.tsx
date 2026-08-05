"use client";

import { useState } from "react";
import { X, Sparkles, Loader2, AlertTriangle, ArrowRight } from "lucide-react";
import { mutate, urlDaMidia } from "@/lib/api";
import { MotorPicker, Motor, motorSalvo, salvarMotor } from "@/components/motor";

/**
 * Popup de geração das três âncoras derivadas.
 *
 * Antes o botão disparava direto, sem perguntar nada — cada clique gastava
 * uma geração no modelo que estivesse por padrão. Aqui o operador vê com o
 * quê está gerando, de qual imagem aquilo deriva, e em que formato.
 *
 * Os formatos oferecidos são restritos por âncora de propósito. A folha de
 * personagem tem 4 painéis lado a lado: em 9:16 cada painel viraria uma
 * tira, e o modelo passa a cortar corpo para caber.
 */

const ANCORA = {
  character_sheet: {
    titulo: "folha de personagem",
    explica: "Quatro painéis do corpo inteiro — frente, lado, costas e três quartos. É daqui que sai a roupa e a proporção do corpo em toda foto.",
    aspectos: ["16:9", "3:2", "4:3"],
  },
  close_up_face: {
    titulo: "close de rosto",
    explica: "Textura de pele em alta — poros, sardas, assimetrias. É o que impede o rosto de virar plástico nas gerações seguintes.",
    aspectos: ["4:5", "1:1", "3:4"],
  },
  close_up_detail: {
    titulo: "cartela de detalhe",
    explica: "Macros de olho, sobrancelha, lábio e mão numa cartela. Serve de gabarito para os traços que o modelo mais erra.",
    aspectos: ["2:3", "3:4", "4:5"],
  },
} as const;

export function GerarDerivada({
  personaId, ancora, rostoAssetId, onFechar, onEnfileirado,
}: {
  personaId: string;
  ancora: keyof typeof ANCORA;
  rostoAssetId: string | null;
  onFechar: () => void;
  onEnfileirado: (jobId: string) => void;
}) {
  const meta = ANCORA[ancora];
  const [motor, setMotor] = useState<Motor>(() => {
    const m = motorSalvo();
    // O formato salvo pode não valer para esta âncora — quem gerou um close
    // em 4:5 antes não quer a folha de personagem em 4:5.
    const aceitos: readonly string[] = meta.aspectos;
    return aceitos.includes(m.aspect) ? m : { ...m, aspect: aceitos[0] };
  });
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function gerar() {
    setEnviando(true);
    setErro(null);
    salvarMotor(motor);
    try {
      const r = await mutate(
        `/v1/media/personas/${personaId}/ancoras/${ancora}`, "POST",
        {
          modelo: motor.modelo || null,
          aspect_ratio: motor.aspect || null,
          resolucao: motor.resolucao,
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
        className="my-8 w-full max-w-lg space-y-4 rounded-lg border border-surface-border bg-surface-raised p-5">

        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-medium text-slate-100">
              Gerar {meta.titulo}
            </h2>
            <p className="mt-0.5 text-xs leading-relaxed text-slate-500">
              {meta.explica}
            </p>
          </div>
          <button onClick={onFechar} className="text-slate-500 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        {/* Deixar explícito de onde deriva: é o rosto que faz todas serem a
            mesma pessoa, e quem trocar o rosto depois precisa saber que as
            derivadas ficaram de outra. */}
        {rostoAssetId && (
          <div className="flex items-center gap-3 rounded border border-surface-border bg-surface/50 p-2.5">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={urlDaMidia(rostoAssetId)} alt=""
              className="h-14 w-14 shrink-0 rounded object-cover" />
            <ArrowRight size={14} className="shrink-0 text-slate-600" />
            <p className="text-[11px] leading-relaxed text-slate-500">
              Deriva deste rosto — ele entra como referência. Trocar o rosto
              depois não regenera esta âncora: ela continuaria sendo de outra
              pessoa.
            </p>
          </div>
        )}

        <MotorPicker valor={motor} onChange={setMotor} aspectos={[...meta.aspectos]} />

        {erro && (
          <p className="flex items-start gap-1 text-xs text-red-300">
            <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {erro}
          </p>
        )}

        <div className="flex gap-2 border-t border-surface-border pt-3">
          <button onClick={gerar} disabled={enviando || !motor.modelo}
            className="btn-primary">
            {enviando
              ? <><Loader2 size={14} className="animate-spin" /> enfileirando…</>
              : <><Sparkles size={14} /> gerar</>}
          </button>
          <button onClick={onFechar} className="btn-ghost">cancelar</button>
        </div>
      </div>
    </div>
  );
}
