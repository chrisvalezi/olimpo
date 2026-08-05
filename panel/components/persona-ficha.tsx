"use client";

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

/**
 * Os campos da persona: identificação e o persona card.
 *
 * Vive fora da página porque é o bloco mais longo dela — dezenas de campos —
 * e é justamente o que precisa sair da frente para a identidade visual ficar
 * no topo. Ver `app/personas/[id]/page.tsx`.
 *
 * O editor guiado cobre os blocos mais mexidos; o card é maior que isto, e o
 * resto continua acessível pelo JSON. Ver docs/04-persona-card-spec.md.
 */
export function PersonaFicha({ card, meta, json, texto, onCard, onMeta, onTexto }: {
  card: any;
  meta: any;
  json: boolean;
  texto: string;
  onCard: (c: any) => void;
  onMeta: (m: any) => void;
  onTexto: (t: string) => void;
}) {
  function set(bloco: string, campo: string, valor: any) {
    onCard({ ...card, [bloco]: { ...(card[bloco] ?? {}), [campo]: valor } });
  }

  return (
    <div className="space-y-4">
      <section className="card grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="label">Nome</span>
          <input className="input" value={meta.name}
            onChange={(e) => onMeta({ ...meta, name: e.target.value })} />
        </label>
        <label className="block">
          <span className="label">Descrição</span>
          <input className="input" value={meta.description}
            onChange={(e) => onMeta({ ...meta, description: e.target.value })} />
        </label>
        <label className="block">
          <span className="label">ID na máquina de influencers</span>
          <input className="input font-mono" value={meta.persona_engine_id}
            onChange={(e) => onMeta({ ...meta, persona_engine_id: e.target.value })} />
        </label>
      </section>

      {json ? (
        <section className="card space-y-2">
          <span className="label">Persona card (JSON)</span>
          <textarea value={texto} onChange={(e) => onTexto(e.target.value)}
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

      <section className="card space-y-2">
        <span className="label">Anotação livre</span>
        <textarea className="input h-20" value={meta.notes}
          onChange={(e) => onMeta({ ...meta, notes: e.target.value })} />
      </section>
    </div>
  );
}
