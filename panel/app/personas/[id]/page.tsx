"use client";

import { useState, use, useEffect } from "react";
import Link from "next/link";
import { ArrowLeft, Save, Plus, Code2, User, Link2 } from "lucide-react";
import useSWR from "swr";
import { api, mutate } from "@/lib/api";
import { PLATAFORMA, STATUS, tempoRelativo } from "@/lib/contas";
import { IdentidadeVisual, Figurinos, GaleriaDaPersona } from "@/components/identidade-visual";
import { FotosDeReferencia } from "@/components/fotos-referencia";

/** Blocos do persona card com edição guiada. O card é maior que isto — o
 *  resto continua acessível pelo editor JSON. Ver docs/04-persona-card-spec.md. */
const BLOCOS: { chave: string; titulo: string; ajuda: string;
                campos: { k: string; rotulo: string; lista?: boolean; area?: boolean }[] }[] = [
  {
    chave: "identity", titulo: "Identidade", ajuda: "quem ela é, de fachada",
    campos: [
      { k: "name", rotulo: "Nome" },
      { k: "occupation", rotulo: "Ocupação" },
      { k: "location", rotulo: "Localização" },
      { k: "language", rotulo: "Idioma" },
    ],
  },
  {
    chave: "voice", titulo: "Voz",
    ajuda: "o bloco que mais segura a personagem no lugar — os exemplos são o que a LLM imita",
    campos: [
      { k: "tone", rotulo: "Tom" },
      { k: "caption_length", rotulo: "Tamanho de caption" },
      { k: "uses", rotulo: "Gírias que usa", lista: true },
      { k: "never_uses", rotulo: "Nunca diz", lista: true },
      { k: "examples", rotulo: "Frases de exemplo", lista: true, area: true },
    ],
  },
  {
    chave: "visual", titulo: "Visual", ajuda: "sem referência facial fixa ela muda de rosto entre posts",
    campos: [
      { k: "canonical", rotulo: "Descrição física", area: true },
      { k: "face_ref", rotulo: "Referência facial" },
      { k: "palette", rotulo: "Paleta / estética" },
      { k: "settings", rotulo: "Cenários", lista: true },
    ],
  },
  {
    chave: "niche", titulo: "Nicho", ajuda: "",
    campos: [
      { k: "primary", rotulo: "Nicho principal" },
      { k: "audience", rotulo: "Público" },
      { k: "secondary", rotulo: "Temas secundários", lista: true },
    ],
  },
  {
    chave: "taboos", titulo: "Tabus", ajuda: "o QA bloqueia conteúdo que viole isto",
    campos: [
      { k: "topics", rotulo: "Temas proibidos", lista: true },
      { k: "banned_words", rotulo: "Palavras banidas", lista: true },
      { k: "never", rotulo: "Nunca faz", lista: true },
    ],
  },
];

export default function FichaDaPersona({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, mutate: reload } = useSWR(`/v1/personas/${id}`, api);

  const [card, setCard] = useState<any>(null);
  const [meta, setMeta] = useState<any>(null);
  const [json, setJson] = useState(false);
  const [texto, setTexto] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);

  const persona = data?.persona;

  useEffect(() => {
    if (!persona) return;
    setCard(persona.persona_card ?? {});
    setTexto(JSON.stringify(persona.persona_card ?? {}, null, 2));
    setMeta({
      name: persona.name, description: persona.description ?? "",
      persona_engine_id: persona.persona_engine_id ?? "",
      notes: persona.notes ?? "",
    });
  }, [persona?.id, persona?.updated_at]);

  if (!persona || !card || !meta) return <p className="text-sm text-slate-500">carregando…</p>;

  function set(bloco: string, campo: string, valor: any) {
    setCard({ ...card, [bloco]: { ...(card[bloco] ?? {}), [campo]: valor } });
  }

  async function salvar() {
    setSalvando(true); setErro(null);
    try {
      let final = card;
      if (json) {
        try { final = JSON.parse(texto); }
        catch (e) { setErro(`JSON inválido: ${e}`); setSalvando(false); return; }
      }
      await mutate(`/v1/personas/${id}`, "PATCH", { ...meta, persona_card: final });
      await reload();
    } catch (e) { setErro(String(e)); } finally { setSalvando(false); }
  }

  return (
    <div className="space-y-5">
      <Link href="/personas" className="flex w-fit items-center gap-1 text-xs text-slate-500 hover:text-slate-300">
        <ArrowLeft size={12} /> personas
      </Link>

      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <User size={16} className="text-slate-500" />
            <h1 className="text-xl font-semibold text-slate-100">{persona.name}</h1>
            <span className="font-mono text-xs text-slate-600">v{persona.persona_version}</span>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {data.accounts.length} login{data.accounts.length === 1 ? "" : "s"} ·
            atualizada {tempoRelativo(persona.updated_at)}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => {
            if (!json) setTexto(JSON.stringify(card, null, 2));
            else { try { setCard(JSON.parse(texto)); } catch { /* mantém */ } }
            setJson(!json);
          }} className="btn-ghost">
            <Code2 size={13} /> {json ? "editor guiado" : "JSON"}
          </button>
          <button onClick={salvar} disabled={salvando} className="btn-primary">
            <Save size={14} /> {salvando ? "salvando…" : "Salvar"}
          </button>
        </div>
      </header>

      {erro && <p className="font-mono text-xs text-red-300">{erro}</p>}

      <section className="card grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="label">Nome</span>
          <input className="input" value={meta.name}
            onChange={(e) => setMeta({ ...meta, name: e.target.value })} />
        </label>
        <label className="block">
          <span className="label">Descrição</span>
          <input className="input" value={meta.description}
            onChange={(e) => setMeta({ ...meta, description: e.target.value })} />
        </label>
        <label className="block">
          <span className="label">ID na máquina de influencers</span>
          <input className="input font-mono" value={meta.persona_engine_id}
            onChange={(e) => setMeta({ ...meta, persona_engine_id: e.target.value })} />
        </label>
      </section>

      <IdentidadeVisual personaId={id} persona={persona} />

      {/* Abaixo das âncoras porque é essa a hierarquia: sem rosto estas não
          salvam nada, e com rosto elas melhoram toda geração. */}
      <FotosDeReferencia personaId={id} />

      <Figurinos personaId={id} persona={persona} />

      {/* ------------------------------------------------------- logins */}
      <section className="card space-y-2">
        <span className="label">Logins desta persona</span>
        {data.accounts.length === 0 ? (
          <p className="flex items-center gap-1 text-sm text-slate-500">
            <Link2 size={12} /> nenhum login vinculado. Vincule pela ficha da
            conta ou em lote na lista de contas.
          </p>
        ) : data.accounts.map((c: any) => {
          const plat = PLATAFORMA[c.platform] ?? { cor: "", rotulo: c.platform };
          return (
            <Link key={c.id} href={`/contas/${c.id}`}
              className="flex items-center gap-2 rounded border border-surface-border bg-surface/50 px-2.5 py-1.5 text-xs hover:bg-surface-border/30">
              <span className={`rounded border px-1 py-0.5 text-[10px] ${plat.cor}`}>
                {plat.rotulo}
              </span>
              <span className="text-slate-300">{c.handle}</span>
              <span className="text-slate-600">{STATUS[c.status]?.rotulo}</span>
              <span className="text-slate-600">{c.autonomy}</span>
              <span className="ml-auto font-mono text-slate-500">{c.followers} seg.</span>
            </Link>
          );
        })}
      </section>

      {/* -------------------------------------------------- persona card */}
      {json ? (
        <section className="card space-y-2">
          <span className="label">Persona card (JSON)</span>
          <textarea value={texto} onChange={(e) => setTexto(e.target.value)}
            spellCheck={false}
            className="input h-[32rem] w-full font-mono text-xs leading-relaxed" />
        </section>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {BLOCOS.map((b) => (
            <section key={b.chave} className="card space-y-3">
              <div>
                <span className="label">{b.titulo}</span>
                {b.ajuda && <p className="text-xs text-slate-600">{b.ajuda}</p>}
              </div>
              {b.campos.map((c) => {
                const valor = card[b.chave]?.[c.k];
                return (
                  <label key={c.k} className="block">
                    <span className="label">{c.rotulo}</span>
                    {c.lista ? (
                      <textarea
                        className={`input ${c.area ? "h-24" : "h-16"} font-mono text-xs`}
                        placeholder="um por linha"
                        value={Array.isArray(valor) ? valor.join("\n") : (valor ?? "")}
                        onChange={(e) => set(b.chave, c.k,
                          e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))}
                      />
                    ) : c.area ? (
                      <textarea className="input h-16" value={valor ?? ""}
                        onChange={(e) => set(b.chave, c.k, e.target.value)} />
                    ) : (
                      <input className="input" value={valor ?? ""}
                        onChange={(e) => set(b.chave, c.k, e.target.value)} />
                    )}
                  </label>
                );
              })}
            </section>
          ))}
        </div>
      )}

      <GaleriaDaPersona personaId={id} />

      <section className="card space-y-2">
        <span className="label">Anotação livre</span>
        <textarea className="input h-20" value={meta.notes}
          onChange={(e) => setMeta({ ...meta, notes: e.target.value })} />
      </section>
    </div>
  );
}
