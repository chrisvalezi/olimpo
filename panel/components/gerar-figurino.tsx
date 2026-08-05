"use client";

import { useState } from "react";
import { X, Sparkles, Loader2, AlertTriangle, Plus, Trash2, Shirt } from "lucide-react";
import { mutate, urlDaMidia } from "@/lib/api";
import { Upload_ } from "@/components/upload";
import { MotorPicker, Motor, motorSalvo, salvarMotor } from "@/components/motor";

/**
 * Gerador de figurino.
 *
 * Peça a peça, e isso não é preciosismo de interface: o modelo trata cada
 * item como algo a reproduzir. "camiseta branca, jeans largo, tênis branco"
 * produz resultado diferente de "look casual" — no primeiro caso ele monta
 * as três peças; no segundo, inventa.
 *
 * O resultado é uma folha de 4 painéis do MESMO rosto com a roupa nova.
 * Por isso o prompt carrega uma trava de identidade longa, listando ossos,
 * sardas e assimetrias: é o que impede a deriva quando tudo o mais muda.
 */

const EXEMPLOS: Record<string, { f: string; m: string }> = {
  top: { f: "cropped branco", m: "camisa oxford branca" },
  bottom: { f: "jeans largo", m: "chino escuro" },
  calcado: { f: "sandália de tira", m: "tênis branco" },
  cabelo: { f: "coque baixo", m: "penteado para trás" },
};

//: O figurino sai em folha de 4 painéis lado a lado. Estreitar espreme
//: cada painel, e o modelo passa a cortar corpo para caber.
const FIGURINO_ASPECTOS = ["16:9", "3:2", "4:3"];

export function GerarFigurino({
  personaId, genero, onFechar, onEnfileirado,
}: {
  personaId: string;
  genero?: string;
  onFechar: () => void;
  onEnfileirado: (jobId: string) => void;
}) {
  const [f, setF] = useState({
    nome: "", top: "", bottom: "", calcado: "", cabelo: "", texto_livre: "",
  });
  const [motor, setMotor] = useState<Motor>(() => {
    const m = motorSalvo();
    return FIGURINO_ASPECTOS.includes(m.aspect) ? m : { ...m, aspect: "16:9" };
  });
  const [pecas, setPecas] = useState<string[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const masc = genero === "Male";
  const ex = (k: string) => `ex.: ${masc ? EXEMPLOS[k].m : EXEMPLOS[k].f}`;

  const vazio = ![f.top, f.bottom, f.calcado, f.cabelo, f.texto_livre]
    .some((x) => x.trim());
  const sobrescrito = !!f.texto_livre.trim();

  async function gerar() {
    setEnviando(true);
    setErro(null);
    salvarMotor(motor);
    try {
      const r = await mutate(
        `/v1/media/personas/${personaId}/figurinos`, "POST",
        {
          ...f,
          modelo: motor.modelo || null,
          aspect_ratio: motor.aspect || null,
          resolucao: motor.resolucao,
          referencias_extras: pecas,
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
        className="my-8 w-full max-w-2xl space-y-4 rounded-lg border border-surface-border bg-surface-raised p-5">

        <div className="flex items-start justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-base font-medium text-slate-100">
              <Shirt size={16} className="text-slate-500" /> Novo figurino
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Uma folha de 4 painéis do mesmo rosto com a roupa nova —
              reutilizável em qualquer foto do estúdio.
            </p>
          </div>
          <button onClick={onFechar} className="text-slate-500 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        <label className="block">
          <span className="label">Nome</span>
          <input className="input" placeholder="ex.: treino, praia, jantar"
            value={f.nome} onChange={(e) => setF({ ...f, nome: e.target.value })} />
          <span className="mt-0.5 block text-[10px] text-slate-600">
            se vazio, usa a descrição das peças
          </span>
        </label>

        <section className={`grid gap-3 sm:grid-cols-2 ${sobrescrito ? "opacity-40" : ""}`}>
          {(["top", "bottom", "calcado", "cabelo"] as const).map((k) => (
            <label key={k} className="block">
              <span className="label">
                {{ top: "parte de cima", bottom: "parte de baixo",
                   calcado: "calçado", cabelo: "cabelo" }[k]}
              </span>
              <input className="input" placeholder={ex(k)} value={f[k]}
                disabled={sobrescrito}
                onChange={(e) => setF({ ...f, [k]: e.target.value })} />
            </label>
          ))}
        </section>

        <label className="block">
          <span className="label">Descrição completa</span>
          <textarea className="input h-20"
            placeholder="descreva o look inteiro — sobrescreve os campos acima…"
            value={f.texto_livre}
            onChange={(e) => setF({ ...f, texto_livre: e.target.value })} />
        </label>

        {/* --------------------------------------------- peças reais por foto */}
        <section className="space-y-2">
          <div className="flex items-baseline justify-between">
            <span className="label">Peças por foto</span>
            <span className="text-xs text-slate-600">opcional</span>
          </div>
          <p className="text-[10px] leading-relaxed text-slate-600">
            Uma foto de roupa real que a personagem deve vestir. O modelo é
            instruído a copiar só a peça — nunca o rosto nem o corpo de quem
            aparece na foto.
          </p>

          {pecas.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {pecas.map((id) => (
                <div key={id} className="relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={urlDaMidia(id)} alt=""
                    className="h-16 w-16 rounded border border-surface-border object-cover" />
                  <button onClick={() => setPecas((v) => v.filter((x) => x !== id))}
                    className="absolute -right-1 -top-1 rounded-full bg-block p-0.5 text-white">
                    <X size={10} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <Upload_ personaId={personaId} papel="reference" compacto
            aoEnviar={(r) => setPecas((v) => [...v, r.asset_id])}>
            <Plus size={12} /> foto de uma peça
          </Upload_>
        </section>

        <MotorPicker valor={motor} onChange={setMotor} aspectos={FIGURINO_ASPECTOS} />

        {erro && (
          <p className="flex items-start gap-1 text-xs text-red-300">
            <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {erro}
          </p>
        )}

        <div className="flex gap-2 border-t border-surface-border pt-3">
          <button onClick={gerar} disabled={enviando || vazio || !motor.modelo}
            className="btn-primary">
            {enviando
              ? <><Loader2 size={14} className="animate-spin" /> enfileirando…</>
              : <><Sparkles size={14} /> gerar figurino</>}
          </button>
          <button onClick={onFechar} className="btn-ghost">cancelar</button>
        </div>
      </div>
    </div>
  );
}
