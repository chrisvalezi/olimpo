"use client";

import { useState, use, useEffect } from "react";
import Link from "next/link";
import { ArrowLeft, Save, Code2, User } from "lucide-react";
import useSWR from "swr";
import { api, mutate } from "@/lib/api";
import { tempoRelativo } from "@/lib/contas";
import { IdentidadeVisual, Figurinos } from "@/components/identidade-visual";
import { FotosDeReferencia } from "@/components/fotos-referencia";
import { MidiasDaPersona } from "@/components/midias-da-persona";
import { PersonaFicha } from "@/components/persona-ficha";
import { PersonaLogins } from "@/components/persona-logins";
import { Abas } from "@/components/abas";

/**
 * Ficha da persona.
 *
 * A ordem da página é uma decisão, não acaso: **identidade visual primeiro**,
 * como na máquina de influencers. As quatro âncoras são o que define quem a
 * personagem é — e antes elas ficavam espremidas entre um formulário e
 * dezenas de campos de persona card, com a galeria de mídias no rodapé,
 * depois de tudo.
 *
 * O resto — ficha, figurinos, mídias, logins — vai para abas. Uma persona
 * tem campos demais para empilhar: montar tudo de uma vez empurra as imagens
 * para fora da tela e dispara todas as requisições juntas.
 */
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

      {/* ─────────────────────────────────────────────── identidade, no topo
          Fora das abas de propósito: é o que se olha antes de qualquer outra
          coisa, e o que precisa estar à mão enquanto se mexe no resto. */}
      <IdentidadeVisual personaId={id} persona={persona} />

      {/* Logo abaixo das âncoras porque é essa a hierarquia: sem rosto elas
          não salvam nada, e com rosto melhoram toda geração. */}
      <FotosDeReferencia personaId={id} />

      {/* ──────────────────────────────────────────────────────── o resto */}
      <Abas
        abas={[
          {
            chave: "midias", rotulo: "Mídias",
            conteudo: () => <MidiasDaPersona personaId={id} />,
          },
          {
            chave: "figurinos", rotulo: "Figurinos",
            conteudo: () => <Figurinos personaId={id} persona={persona} />,
          },
          {
            chave: "ficha", rotulo: "Ficha",
            conteudo: () => (
              <PersonaFicha
                card={card} meta={meta} json={json} texto={texto}
                onCard={setCard} onMeta={setMeta} onTexto={setTexto}
              />
            ),
          },
          {
            chave: "logins", rotulo: "Logins", contagem: data.accounts.length,
            conteudo: () => <PersonaLogins contas={data.accounts} />,
          },
        ]}
      />
    </div>
  );
}
