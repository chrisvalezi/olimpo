"use client";

import { useRef, useState } from "react";
import { Upload, Loader2, AlertTriangle } from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function baseResolvida(): string {
  if (typeof window === "undefined") return BASE;
  try {
    const url = new URL(BASE);
    const loopback = ["localhost", "127.0.0.1", "0.0.0.0"].includes(url.hostname);
    if (loopback && !["localhost", "127.0.0.1"].includes(window.location.hostname)) {
      return `${window.location.protocol}//${window.location.hostname}:${url.port}`;
    }
    return BASE;
  } catch {
    return BASE;
  }
}

export type ResultadoUpload = { asset_id: string; papel: string };

/**
 * Envio de arquivo por seleção ou arrastar.
 *
 * Multipart, não JSON: uma imagem de identidade tem dezenas de megabytes, e
 * base64 dentro de JSON inflaria em um terço sem ganho nenhum.
 */
export function Upload_({
  personaId, papel = "reference", rotulo, aoEnviar, compacto = false, children,
}: {
  personaId?: string;
  papel?: string;
  rotulo?: string;
  aoEnviar?: (r: ResultadoUpload) => void;
  compacto?: boolean;
  children?: React.ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [enviando, setEnviando] = useState(false);
  const [sobre, setSobre] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function enviar(arquivo: File) {
    setEnviando(true);
    setErro(null);
    try {
      const dados = new FormData();
      dados.append("arquivo", arquivo);
      dados.append("papel", papel);
      if (personaId) dados.append("persona_id", personaId);
      if (rotulo) dados.append("rotulo", rotulo);

      const resp = await fetch(`${baseResolvida()}/v1/media/upload`, {
        method: "POST", body: dados,
      });
      if (!resp.ok) {
        const corpo = await resp.json().catch(() => ({}));
        throw new Error(corpo.detail ?? `${resp.status} ${resp.statusText}`);
      }
      aoEnviar?.(await resp.json());
    } catch (e) {
      setErro(String(e).replace("Error: ", ""));
    } finally {
      setEnviando(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="space-y-1">
      <div
        onClick={() => !enviando && input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setSobre(true); }}
        onDragLeave={() => setSobre(false)}
        onDrop={(e) => {
          e.preventDefault();
          setSobre(false);
          const a = e.dataTransfer.files?.[0];
          if (a) enviar(a);
        }}
        className={`flex cursor-pointer items-center justify-center gap-2 rounded border border-dashed transition-colors ${
          compacto ? "px-2 py-1.5 text-xs" : "px-3 py-6 text-sm"
        } ${
          sobre
            ? "border-info bg-info/10 text-info"
            : "border-surface-border text-slate-500 hover:border-slate-600 hover:text-slate-300"
        } ${enviando ? "pointer-events-none opacity-60" : ""}`}
      >
        {enviando ? (
          <><Loader2 size={compacto ? 12 : 16} className="animate-spin" /> enviando…</>
        ) : (
          children ?? (
            <><Upload size={compacto ? 12 : 16} /> arraste ou clique para enviar</>
          )
        )}
      </div>

      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/quicktime,video/webm"
        className="hidden"
        onChange={(e) => {
          const a = e.target.files?.[0];
          if (a) enviar(a);
        }}
      />

      {erro && (
        <p className="flex items-start gap-1 text-xs text-red-300">
          <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {erro}
        </p>
      )}
    </div>
  );
}
