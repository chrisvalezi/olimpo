"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  Sparkles, Loader2, AlertTriangle, Shirt, Package, ImageOff, RotateCcw, Dices,
} from "lucide-react";
import { api, mutate, urlDaMidia } from "@/lib/api";
import { MotorPicker, Motor, motorSalvo, salvarMotor } from "@/components/motor";

/**
 * Estúdio de fotos — dentro da persona, não fora.
 *
 * Era uma página própria com um seletor de personas no topo, e isso invertia
 * a relação: a persona é o sujeito, a foto é o que se faz com ela. Escolher a
 * personagem numa tela separada obrigava a sair da ficha, perder o contexto
 * das âncoras e dos figurinos, e voltar.
 *
 * O que se escolhe aqui é só a CENA. A personagem já existe: o modelo recebe
 * as imagens de identidade como referência, e o texto descreve o entorno.
 * Essa é a divisão que mantém o rosto — descrever a pessoa em texto seria
 * pedir para o modelo reinterpretá-la.
 */

const LOCAL: Record<string, string> = {
  "coffee-shop": "cafeteria", bedroom: "quarto", bathroom: "banheiro",
  kitchen: "cozinha", "living-room": "sala", gym: "academia",
  street: "rua", "city-street": "rua", park: "parque", beach: "praia",
  car: "carro", restaurant: "restaurante", office: "escritório",
  rooftop: "terraço", mall: "shopping", hotel: "hotel", studio: "estúdio",
};
const HORA: Record<string, string> = {
  morning: "manhã", afternoon: "tarde", "golden-hour": "fim de tarde", night: "noite",
};
const EXPRESSAO: Record<string, string> = {
  natural: "natural", "soft-smile": "sorriso leve", smiling: "sorrindo",
  laughing: "rindo", serious: "séria", "looking-away": "olhando para o lado",
};
const OLHAR: Record<string, string> = {
  "at-camera": "para a câmera", "off-axis": "fora do eixo", down: "para baixo",
};
const STANCE: Record<string, string> = { standing: "em pé", sitting: "sentada" };

export function EstudioDaPersona({ personaId }: { personaId: string }) {
  const { data: opcoes } = useSWR("/v1/media/opcoes", api);
  const { data: identidade } = useSWR(
    `/v1/media/personas/${personaId}/identidade`, api,
  );
  const { data: figurinos } = useSWR(
    `/v1/media/personas/${personaId}/figurinos`, api,
  );
  const { data: marcas } = useSWR("/v1/brands", api);

  const [p, setP] = useState({
    local: "coffee-shop", hora: "afternoon", pose: "front", stance: "standing",
    expressao: "natural", olhar: "at-camera", quantidade: 1,
    wardrobe_item_id: "", wardrobe_text: "", hairstyle_text: "",
    brand_id: "", prop_text: "", seed: "",
  });
  const [motor, setMotor] = useState<Motor>(() => {
    const m = motorSalvo();
    // Foto de feed é vertical por padrão; o motor pode ter ficado em 16:9
    // de uma folha de personagem gerada antes.
    return ["9:16", "4:5", "3:4", "1:1"].includes(m.aspect)
      ? m : { ...m, aspect: "9:16" };
  });
  const [job, setJob] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  const { data: estado } = useSWR(
    job ? `/v1/media/jobs/${job}` : null, api, { refreshInterval: 3_000 },
  );

  const rodando = estado && ["queued", "running"].includes(estado.status);
  const pronta = estado?.status === "done" ? estado.result_asset_ids ?? [] : [];
  const semRosto = identidade && !identidade.pode_gerar_com_identidade;

  async function gerar() {
    setErro(null);
    salvarMotor(motor);
    try {
      const corpo: any = {
        ...p,
        aspect_ratio: motor.aspect,
        modelo: motor.modelo || undefined,
        resolucao: motor.resolucao || undefined,
        seed: p.seed ? Number(p.seed) : undefined,
      };
      for (const k of ["wardrobe_item_id", "brand_id", "modelo", "resolucao", "seed"]) {
        if (!corpo[k]) delete corpo[k];
      }
      const r = await mutate(`/v1/media/personas/${personaId}/gerar`, "POST", corpo);
      setJob(r.job_id);
    } catch (e) {
      setErro(String(e).replace("Error: ", ""));
    }
  }

  if (semRosto) {
    return (
      <div className="flex items-start gap-2 rounded border border-block/30 bg-block/5 p-3 text-sm text-red-200">
        <AlertTriangle size={14} className="mt-0.5 shrink-0" />
        <span>
          Esta persona não tem referência de rosto. Cada geração produziria uma
          pessoa diferente — defina o rosto no bloco de identidade acima.
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="max-w-2xl text-xs leading-relaxed text-slate-500">
        A personagem já existe: o modelo recebe as imagens de identidade como
        referência e o texto descreve <strong>só a cena</strong>. É essa divisão
        que mantém o rosto — descrever a pessoa em texto seria pedir para o
        modelo reinterpretá-la.
      </p>

      {identidade && identidade.completas < 4 && (
        <p className="flex items-start gap-1.5 text-xs text-amber-200/80">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          {identidade.completas} de 4 âncoras. Com as quatro, o rosto e a
          textura de pele ficam mais estáveis entre gerações.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* ------------------------------------------------------- controles */}
        <div className="space-y-4 lg:col-span-2">
          <section className="card space-y-3">
            <span className="label">Cena</span>
            <Grupo rotulo="local" valores={opcoes?.locais ?? []} rotulos={LOCAL}
              atual={p.local} onEscolher={(v) => setP({ ...p, local: v })} />
            <Grupo rotulo="hora" valores={opcoes?.horas ?? []} rotulos={HORA}
              atual={p.hora} onEscolher={(v) => setP({ ...p, hora: v })} />
          </section>

          <section className="card space-y-3">
            <span className="label">Pessoa</span>
            <Grupo rotulo="postura" valores={["standing", "sitting"]} rotulos={STANCE}
              atual={p.stance} onEscolher={(v) => setP({ ...p, stance: v })} />
            <Grupo rotulo="pose" valores={opcoes?.poses ?? []}
              atual={p.pose} onEscolher={(v) => setP({ ...p, pose: v })} />
            <Grupo rotulo="expressão" valores={opcoes?.expressoes ?? []}
              rotulos={EXPRESSAO} atual={p.expressao}
              onEscolher={(v) => setP({ ...p, expressao: v })} />
            <Grupo rotulo="olhar" valores={Object.keys(OLHAR)} rotulos={OLHAR}
              atual={p.olhar} onEscolher={(v) => setP({ ...p, olhar: v })} />
          </section>

          <section className="card space-y-3">
            <div className="flex items-center gap-1.5">
              <Shirt size={13} className="text-slate-500" />
              <span className="label !mb-0">Figurino</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Miniatura
                ativo={!p.wardrobe_item_id && !p.wardrobe_text}
                onClick={() => setP({ ...p, wardrobe_item_id: "", wardrobe_text: "" })}
                assetId={identidade?.ancoras?.[1]?.asset_id}
                rotulo="atual"
              />
              {(figurinos?.figurinos ?? []).map((w: any) => (
                <Miniatura key={w.id} ativo={p.wardrobe_item_id === w.id}
                  onClick={() => setP({ ...p, wardrobe_item_id: w.id, wardrobe_text: "" })}
                  assetId={w.asset_id} rotulo={w.name} />
              ))}
            </div>
            {/* Texto livre e figurino são caminhos mutuamente exclusivos —
                mandar os dois confunde o modelo sobre de onde vem a roupa. */}
            <input className="input" placeholder="ou descreva a roupa em texto…"
              value={p.wardrobe_text}
              onChange={(e) => setP({ ...p, wardrobe_text: e.target.value, wardrobe_item_id: "" })} />
            <input className="input" placeholder="penteado (sobrescreve o da referência)"
              value={p.hairstyle_text}
              onChange={(e) => setP({ ...p, hairstyle_text: e.target.value })} />
          </section>

          <section className="card space-y-3">
            <div className="flex items-center gap-1.5">
              <Package size={13} className="text-slate-500" />
              <span className="label !mb-0">Produto</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Miniatura ativo={!p.brand_id} onClick={() => setP({ ...p, brand_id: "" })}
                rotulo="nenhum" />
              {(marcas?.brands ?? []).map((b: any) => (
                <Miniatura key={b.id} ativo={p.brand_id === b.id}
                  onClick={() => setP({ ...p, brand_id: b.id })}
                  assetId={b.product_sheet_asset_id} rotulo={b.name} />
              ))}
            </div>
            <input className="input" placeholder="ou descreva o objeto na mão…"
              value={p.prop_text}
              onChange={(e) => setP({ ...p, prop_text: e.target.value })} />
          </section>
        </div>

        {/* ------------------------------------------------------- resultado */}
        <div className="space-y-4">
          <section className="card space-y-3">
            <MotorPicker valor={motor} onChange={setMotor} compacto />

            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="label">quantidade</span>
                <select className="input" value={p.quantidade}
                  onChange={(e) => setP({ ...p, quantidade: Number(e.target.value) })}>
                  {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="label">semente</span>
                <div className="flex gap-1">
                  <input className="input font-mono" placeholder="aleatória"
                    value={p.seed}
                    onChange={(e) => setP({ ...p, seed: e.target.value.replace(/\D/g, "") })} />
                  <button title="sortear"
                    onClick={() => setP({ ...p, seed: String(Math.floor(Math.random() * 1e9)) })}
                    className="btn-ghost mt-1 shrink-0">
                    <Dices size={12} />
                  </button>
                </div>
              </label>
            </div>
            {/* Semente fixa reproduz o mesmo sorteio de cena, ação e figurantes
                — serve para mudar um parâmetro por vez e ver só o efeito dele. */}
            <p className="text-[10px] text-slate-600">
              semente fixa reproduz o mesmo sorteio de cena e enquadramento
            </p>

            <button onClick={gerar} disabled={rodando || !motor.modelo}
              className="btn-primary w-full justify-center">
              {rodando
                ? <><Loader2 size={14} className="animate-spin" /> gerando…</>
                : <><Sparkles size={14} /> gerar {p.quantidade > 1 ? `${p.quantidade} fotos` : "foto"}</>}
            </button>

            {erro && (
              <p className="flex items-start gap-1 text-xs text-red-300">
                <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {erro}
              </p>
            )}
            {estado?.status === "error" && (
              <p className="flex items-start gap-1 text-xs text-red-300">
                <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {estado.error}
              </p>
            )}
          </section>

          <section className="card space-y-2">
            <div className="flex items-center justify-between">
              <span className="label">Resultado</span>
              {pronta.length > 0 && (
                <button onClick={() => setJob(null)} className="btn-ghost">
                  <RotateCcw size={11} /> limpar
                </button>
              )}
            </div>

            {pronta.length === 0 ? (
              <div className="flex aspect-[3/4] items-center justify-center rounded border border-dashed border-surface-border text-slate-700">
                {rodando
                  ? <Loader2 size={22} className="animate-spin text-info" />
                  : <ImageOff size={22} />}
              </div>
            ) : (
              <div className={`grid gap-2 ${pronta.length > 1 ? "grid-cols-2" : ""}`}>
                {pronta.map((id: string) => (
                  <a key={id} href={urlDaMidia(id)} target="_blank" rel="noreferrer"
                    className="overflow-hidden rounded border border-surface-border">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={urlDaMidia(id)} alt="" className="w-full object-cover" />
                  </a>
                ))}
              </div>
            )}

            {pronta.length > 0 && (
              <p className="text-[10px] leading-relaxed text-slate-600">
                Já está na aba Mídias, com o prompt que gerou.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function Grupo({ rotulo, valores, rotulos, atual, onEscolher }: {
  rotulo: string; valores: string[]; rotulos?: Record<string, string>;
  atual: string; onEscolher: (v: string) => void;
}) {
  if (valores.length === 0) return null;
  return (
    <div className="flex items-start gap-2 text-xs">
      <span className="w-16 shrink-0 pt-1 text-slate-600">{rotulo}</span>
      <div className="flex flex-wrap gap-1">
        {valores.map((v) => (
          <button key={v} onClick={() => onEscolher(v)}
            className={`rounded border px-2 py-0.5 ${
              atual === v ? "border-info/50 bg-info/15 text-info"
                          : "border-surface-border text-slate-400 hover:text-slate-200"
            }`}>
            {rotulos?.[v] ?? v.replace(/-/g, " ")}
          </button>
        ))}
      </div>
    </div>
  );
}

function Miniatura({ ativo, onClick, assetId, rotulo }: {
  ativo: boolean; onClick: () => void; assetId?: string | null; rotulo: string;
}) {
  return (
    <button onClick={onClick}
      className={`w-20 space-y-1 rounded border p-1 transition-colors ${
        ativo ? "border-info/60 bg-info/10" : "border-surface-border hover:border-slate-600"
      }`}>
      <div className="aspect-video overflow-hidden rounded bg-surface">
        {assetId ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={urlDaMidia(assetId)} alt="" loading="lazy"
            className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-slate-700">
            <ImageOff size={12} />
          </div>
        )}
      </div>
      <span className={`block truncate text-[10px] ${ativo ? "text-info" : "text-slate-500"}`}>
        {rotulo}
      </span>
    </button>
  );
}
