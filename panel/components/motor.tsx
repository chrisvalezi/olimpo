"use client";

import { useEffect } from "react";
import useSWR from "swr";
import { AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Escolha do motor: modelo → formato → resolução, nessa ordem.
 *
 * A ordem não é arbitrária. Formato e resolução dependem do modelo — o
 * Seedream Pro aceita 1K e 2K, o Lite aceita até 4K, o FLUX não aceita
 * resolução nenhuma. Escolher o formato antes do modelo seria escolher uma
 * opção que pode não existir depois.
 *
 * Modelo sem chave aparece desabilitado dizendo QUAL chave falta. Oferecer
 * um modelo morto transforma a escolha num job que entra na fila, roda no
 * worker e falha dez segundos depois — longe da tela, sem explicação.
 *
 * A escolha fica no localStorage porque acompanha o operador entre os
 * pontos de geração: quem escolheu Seedream para o rosto quer Seedream para
 * a folha de personagem, senão o tratamento de pele muda entre as âncoras.
 */

export type Motor = {
  modelo: string;
  aspect: string;
  resolucao: string | null;
};

export type ModeloAPI = {
  id: string;
  label: string;
  provider: string;
  resolucoes: string[];
  aspectos: string[];
  disponivel: boolean;
  motivo: string | null;
};

const CHAVE = "olimpo.motor";

export function motorSalvo(): Motor {
  if (typeof localStorage === "undefined") {
    return { modelo: "", aspect: "", resolucao: null };
  }
  try {
    return { modelo: "", aspect: "", resolucao: null, ...JSON.parse(localStorage.getItem(CHAVE) || "{}") };
  } catch {
    return { modelo: "", aspect: "", resolucao: null };
  }
}

export function salvarMotor(m: Motor) {
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(CHAVE, JSON.stringify(m));
  }
}

/** Rótulo humano para as proporções mais usadas. */
const SUB: Record<string, string> = {
  "9:16": "stories · reels",
  "4:5": "feed",
  "1:1": "quadrado",
  "16:9": "folha · paisagem",
  "4:3": "compacto",
  "3:2": "equilibrado",
  "2:3": "cartela",
  "3:4": "retrato",
  auto: "o modelo decide",
};

export function MotorPicker({
  valor, onChange, aspectos, compacto = false,
}: {
  valor: Motor;
  onChange: (m: Motor) => void;
  /** Restringe as proporções oferecidas — a folha de personagem só faz
   *  sentido em formato largo, e oferecer 9:16 seria oferecer painéis
   *  espremidos. */
  aspectos?: string[];
  compacto?: boolean;
}) {
  const { data } = useSWR("/v1/media/opcoes", api);
  const modelos: ModeloAPI[] = data?.modelos ?? [];
  const atual = modelos.find((m) => m.id === valor.modelo);

  // Se o modelo escolhido não pode gerar — chave removida, catálogo mudou —
  // cai para o primeiro utilizável, em vez de deixar o operador clicar num
  // botão que sempre falha.
  useEffect(() => {
    if (!modelos.length) return;
    if (atual?.disponivel) return;
    const ok = modelos.find((m) => m.disponivel);
    if (ok) aplicarModelo(ok);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelos.length, atual?.id, atual?.disponivel]);

  function aplicarModelo(m: ModeloAPI) {
    const permitidos = aspectos ?? m.aspectos;
    onChange({
      modelo: m.id,
      // Preserva a escolha se o novo modelo também aceita; senão volta ao
      // primeiro permitido.
      aspect: permitidos.includes(valor.aspect) ? valor.aspect : permitidos[0],
      // Resolução mais alta como padrão, igual à origem. Null quando o
      // modelo não tem essa escolha — mandar `resolution` para o FLUX é
      // enviar um campo que ele ignora, ou recusa.
      resolucao: m.resolucoes.length
        ? (m.resolucoes.includes(valor.resolucao ?? "")
            ? valor.resolucao
            : m.resolucoes[m.resolucoes.length - 1])
        : null,
    });
  }

  const permitidos = aspectos ?? atual?.aspectos ?? [];

  const chip = (ligado: boolean) =>
    `rounded border px-2 py-1 text-[11px] transition ${
      ligado
        ? "border-active/60 bg-active/10 text-active"
        : "border-surface-border text-slate-500 hover:text-slate-300"
    }`;

  return (
    <div className={compacto ? "space-y-2" : "space-y-3"}>
      <label className="block">
        <span className="label">motor</span>
        <select
          className="input"
          value={valor.modelo}
          onChange={(e) => {
            const m = modelos.find((x) => x.id === e.target.value);
            if (m) aplicarModelo(m);
          }}
        >
          {!modelos.length && <option value="">carregando…</option>}
          {modelos.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.disponivel}>
              {m.label}{m.disponivel ? "" : ` — ${m.motivo}`}
            </option>
          ))}
        </select>
      </label>

      {modelos.length > 0 && !modelos.some((m) => m.disponivel) && (
        <p className="flex items-start gap-1.5 text-xs text-red-300">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          Nenhum motor tem chave configurada. Sem isso a geração não sai da
          fila.
        </p>
      )}

      {permitidos.length > 0 && (
        <div>
          <span className="label">formato</span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {permitidos.map((a) => (
              <button
                key={a}
                onClick={() => onChange({ ...valor, aspect: a })}
                className={chip(valor.aspect === a)}
                title={SUB[a] ?? ""}
              >
                {a}
                {SUB[a] && !compacto && (
                  <span className="ml-1 opacity-50">{SUB[a]}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {!!atual?.resolucoes.length && (
        <div>
          <span className="label">resolução</span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {atual.resolucoes.map((r) => (
              <button
                key={r}
                onClick={() => onChange({ ...valor, resolucao: r })}
                className={chip(valor.resolucao === r)}
              >
                {r}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
