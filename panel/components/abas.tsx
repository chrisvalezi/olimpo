"use client";

import { useState } from "react";

/**
 * Abas simples, como as da máquina de influencers.
 *
 * Servem a uma decisão de hierarquia, não de estética: a identidade visual
 * fica sempre visível no topo e o resto — ficha, figurinos, mídias, logins —
 * some atrás de aba. Uma persona tem dezenas de campos, e empilhá-los todos
 * empurra as quatro imagens que definem quem ela é para fora da tela.
 *
 * O conteúdo das abas inativas **não** é montado. Numa persona com centenas
 * de mídias, montar tudo dispara todas as requisições de uma vez.
 */
export function Abas({ abas, inicial }: {
  abas: { chave: string; rotulo: string; contagem?: number; conteudo: () => React.ReactNode }[];
  inicial?: string;
}) {
  const [ativa, setAtiva] = useState(inicial ?? abas[0]?.chave);
  const atual = abas.find((a) => a.chave === ativa) ?? abas[0];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5 border-b border-surface-border pb-3">
        {abas.map((a) => (
          <button
            key={a.chave}
            onClick={() => setAtiva(a.chave)}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              ativa === a.chave
                ? "bg-info/15 text-info"
                : "text-slate-500 hover:bg-surface-border/30 hover:text-slate-300"
            }`}
          >
            {a.rotulo}
            {a.contagem !== undefined && a.contagem > 0 && (
              <span className="ml-1.5 text-xs opacity-60">{a.contagem}</span>
            )}
          </button>
        ))}
      </div>
      {atual?.conteudo()}
    </div>
  );
}
