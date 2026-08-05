/** Vocabulário compartilhado das telas de servidores. */

export const SERVICE_KINDS = {
  orchestrator: {
    rotulo: "Orchestrator",
    descricao: "Executa e verifica ações nos devices",
    cor: "border-violet-500/40 bg-violet-500/10 text-violet-300",
  },
  android_manager: {
    rotulo: "Android Manager",
    descricao: "Painel da frota de containers",
    cor: "border-sky-500/40 bg-sky-500/10 text-sky-300",
  },
  screen_gateway: {
    rotulo: "Gateway de tela",
    descricao: "Transmissão ao vivo das telas",
    cor: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  },
  persona_engine: {
    rotulo: "Máquina de influencers",
    descricao: "Persona, história e geração de mídia",
    cor: "border-pink-500/40 bg-pink-500/10 text-pink-300",
  },
  media_forge: {
    rotulo: "Media Forge",
    descricao: "Cortes e edição de vídeo",
    cor: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  },
  custom: {
    rotulo: "Outro",
    descricao: "Serviço HTTP qualquer",
    cor: "border-slate-500/40 bg-slate-500/10 text-slate-300",
  },
} as const;

export type ServiceKind = keyof typeof SERVICE_KINDS;

export const STATUS_TONE: Record<string, string> = {
  online: "border-active/40 bg-active/10 text-active",
  active: "border-active/40 bg-active/10 text-active",
  degraded: "border-warn/40 bg-warn/10 text-warn",
  offline: "border-block/40 bg-block/10 text-block",
  unreachable: "border-block/40 bg-block/10 text-block",
  // Credencial errada não é "fora do ar" — o operador conserta coisas
  // diferentes em cada caso, então a cor precisa ser diferente.
  unauthorized: "border-kill/40 bg-kill/10 text-purple-300",
  disabled: "border-paused/40 bg-paused/10 text-paused",
  unknown: "border-slate-600 bg-slate-700/20 text-slate-400",
};

export const STATUS_LABEL: Record<string, string> = {
  online: "no ar",
  active: "ativo",
  degraded: "degradado",
  offline: "fora do ar",
  unreachable: "inacessível",
  unauthorized: "credencial inválida",
  disabled: "desligado",
  unknown: "não sondado",
};

/** Sugestões por tipo de serviço, para o formulário não começar vazio. */
export const KIND_DEFAULTS: Record<string, { porta: number; esquema: string; auth: string; dica: string }> = {
  orchestrator: {
    porta: 8078, esquema: "http", auth: "basic",
    dica: "Porta do worker. A 8077 é o executor: monta o docker.sock e escuta só em loopback.",
  },
  android_manager: {
    porta: 443, esquema: "https", auth: "login",
    dica: "Atrás do nginx. Não existe chave de API — o token vem de usuário e senha.",
  },
  screen_gateway: {
    porta: 9000, esquema: "https", auth: "none",
    dica: "Transmissão de tela. Exige HTTPS no painel para o vídeo decodificar.",
  },
  persona_engine: { porta: 8081, esquema: "http", auth: "none", dica: "" },
  media_forge: { porta: 8300, esquema: "http", auth: "bearer", dica: "Ainda não construído (Fase 5)." },
  custom: { porta: 80, esquema: "http", auth: "none", dica: "" },
};

export function tempoRelativo(iso: string | null): string {
  if (!iso) return "nunca";
  const seg = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seg < 60) return `há ${seg}s`;
  if (seg < 3600) return `há ${Math.floor(seg / 60)}min`;
  if (seg < 86400) return `há ${Math.floor(seg / 3600)}h`;
  return `há ${Math.floor(seg / 86400)}d`;
}

/** Monta a URL do stream de tela de um device.
 *
 *  `control=0` é obrigatório e não é detalhe de interface: ele torna o
 *  espectador passivo NO SERVIDOR, garantindo que o Olimpo assistindo nunca
 *  roube o controle de um curador humano operando o device.
 */
export function urlDaTela(
  gatewayWebUrl: string,
  device: { adb_target?: string | null; video_device?: string | null },
  opts: { fps?: number; bitrate?: number } = {},
): string | null {
  if (!device.adb_target || !device.video_device) return null;
  const p = new URLSearchParams({
    mode: "native",
    video: device.video_device,
    target: device.adb_target,
    control: "0",
    audio: "0",
    maxFps: String(opts.fps ?? 8),
    bitrate: String(opts.bitrate ?? 500_000),
  });
  return `${gatewayWebUrl.replace(/\/$/, "")}/?${p}`;
}
