"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import {
  Camera, Sparkles, Loader2, AlertTriangle, User, Shirt, Package,
  ImageOff, RotateCcw, Dices,
} from "lucide-react";
import { api, mutate, urlDaMidia } from "@/lib/api";

/** Rótulos legíveis para o que o motor chama em inglês. */
const LOCAL: Record<string, string> = {
  "coffee-shop": "cafeteria", bedroom: "quarto", bathroom: "banheiro",
  kitchen: "cozinha", "living-room": "sala", gym: "academia",
  street: "rua", park: "parque", beach: "praia", car: "carro",
  restaurant: "restaurante", office: "escritório",
};
const HORA: Record<string, string> = {
  morning: "manhã", afternoon: "tarde", "golden-hour": "fim de tarde", night: "noite",
};
const EXPRESSAO: Record<string, string> = {
  natural: "natural", "soft-smile": "sorriso leve", smiling: "sorrindo",
  serious: "séria", "looking-away": "olhando para o lado",
};
const OLHAR: Record<string, string> = {
  "at-camera": "para a câmera", "off-axis": "fora do eixo", down: "para baixo",
};
const STANCE: Record<string, string> = { standing: "em pé", sitting: "sentada" };

export default function Estudio() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">carregando…</p>}>
      <EstudioInterno />
    </Suspense>
  );
}

function EstudioInterno() {
  const params = useSearchParams();
  const router = useRouter();

  const { data: opcoes } = useSWR("/v1/media/opcoes", api);
  const { data: personas } = useSWR("/v1/personas", api);

  const [personaId, setPersonaId] = useState(params.get("persona") ?? "");
  const [p, setP] = useState({
    local: "coffee-shop", hora: "afternoon", pose: "front", stance: "standing",
    expressao: "natural", olhar: "at-camera", aspect_ratio: "9:16",
    quantidade: 1, wardrobe_item_id: "", wardrobe_text: "", hairstyle_text: "",
    brand_id: "", prop_text: "", modelo: "", resolucao: "", seed: "",
  });
  const [job, setJob] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  const { data: identidade } = useSWR(
    personaId ? `/v1/media/personas/${personaId}/identidade` : null, api,
  );
  const { data: figurinos } = useSWR(
    personaId ? `/v1/media/personas/${personaId}/figurinos` : null, api,
  );
  const { data: marcas } = useSWR("/v1/brands", api);
  const { data: estado } = useSWR(
    job ? `/v1/media/jobs/${job}` : null, api, { refreshInterval: 3_000 },
  );

  useEffect(() => {
    if (personaId) router.replace(`/estudio?persona=${personaId}`, { scroll: false });
  }, [personaId, router]);

  const rodando = estado && ["queued", "running"].includes(estado.status);
  const pronta = estado?.status === "done" ? estado.result_asset_ids ?? [] : [];
  const semRosto = identidade && !identidade.pode_gerar_com_identidade;

  async function gerar() {
    setErro(null);
    try {
      const corpo: any = { ...p };
      for (const k of ["wardrobe_item_id", "brand_id", "modelo", "resolucao"]) {
        if (!corpo[k]) delete corpo[k];
      }
      corpo.seed = p.seed ? Number(p.seed) : null;
      if (corpo.seed === null) delete corpo.seed;

      const r = await mutate(`/v1/media/personas/${personaId}/gerar`, "POST", corpo);
      setJob(r.job_id);
    } catch (e) {
      setErro(String(e).replace("Error: ", ""));
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
            <Camera size={18} className="text-slate-500" /> Estúdio
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            A personagem já existe — o que você escolhe aqui é o que fazer com
            ela. O modelo recebe as imagens de identidade como referência e o
            texto só descreve a cena.
          </p>
        </div>
      </header>

      {/* ---------------------------------------------------------- persona */}
      <section className="card space-y-3">
        <span className="label">Persona</span>
        <div className="flex flex-wrap gap-2">
          {(personas?.personas ?? []).map((x: any) => (
            <button key={x.id} onClick={() => { setPersonaId(x.id); setJob(null); }}
              className={`flex items-center gap-2 rounded border px-2 py-1.5 text-xs transition-colors ${
                personaId === x.id
                  ? "border-info/50 bg-info/10 text-slate-100"
                  : "border-surface-border text-slate-400 hover:text-slate-200"
              }`}>
              {x.face_asset_id ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={urlDaMidia(x.face_asset_id)} alt=""
                  className="h-7 w-7 rounded object-cover" />
              ) : (
                <span className="flex h-7 w-7 items-center justify-center rounded bg-surface">
                  <User size={12} className="text-slate-700" />
                </span>
              )}
              {x.name}
            </button>
          ))}
        </div>

        {semRosto && (
          <div className="flex items-start gap-2 rounded border border-block/30 bg-block/5 p-2.5 text-xs text-red-200">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>
              Esta persona não tem referência de rosto — cada geração produziria
              uma pessoa diferente.{" "}
              <Link href={`/personas/${personaId}`} className="underline">
                Defina o rosto primeiro
              </Link>.
            </span>
          </div>
        )}

        {identidade && identidade.completas < 4 && !semRosto && (
          <p className="flex items-start gap-1.5 text-xs text-amber-200/80">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            {identidade.completas} de 4 âncoras. Com as quatro, o rosto e a
            textura de pele ficam mais estáveis entre gerações.
          </p>
        )}
      </section>

      {personaId && !semRosto && (
        <div className="grid gap-4 lg:grid-cols-3">
          {/* ------------------------------------------------------ controles */}
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

          {/* ------------------------------------------------------ resultado */}
          <div className="space-y-4">
            <section className="card space-y-3">
              <span className="label">Formato</span>
              <div className="grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="label">proporção</span>
                  <select className="input" value={p.aspect_ratio}
                    onChange={(e) => setP({ ...p, aspect_ratio: e.target.value })}>
                    {(opcoes?.aspect_ratios ?? []).map((a: string) => (
                      <option key={a} value={a}>{a}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="label">quantidade</span>
                  <select className="input" value={p.quantidade}
                    onChange={(e) => setP({ ...p, quantidade: Number(e.target.value) })}>
                    {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                <label className="col-span-2 block">
                  <span className="label">modelo</span>
                  <select className="input" value={p.modelo}
                    onChange={(e) => setP({ ...p, modelo: e.target.value })}>
                    <option value="">padrão (FLUX.2)</option>
                    {(opcoes?.modelos ?? []).map((m: any) => (
                      <option key={m.id} value={m.id}>{m.label}</option>
                    ))}
                  </select>
                </label>
                <label className="col-span-2 block">
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
                  {/* Semente fixa reproduz o mesmo prompt — serve para mudar
                      um parâmetro por vez e ver só o efeito dele. */}
                  <span className="mt-0.5 block text-[10px] text-slate-600">
                    fixa reproduz o mesmo prompt
                  </span>
                </label>
              </div>

              <button onClick={gerar} disabled={rodando || !personaId}
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
            </section>
          </div>
        </div>
      )}
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
