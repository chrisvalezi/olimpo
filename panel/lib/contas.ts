/** Vocabulário das telas de contas e personas. */

/** O que NÓS queremos que a conta faça. Intenção, não realidade. */
export const STATUS: Record<string, { rotulo: string; cor: string; ajuda: string }> = {
  registered: {
    rotulo: "cadastrada",
    cor: "border-slate-600 bg-slate-700/30 text-slate-400",
    ajuda: "Existe no sistema mas não age. É onde toda conta nasce — inclusive as importadas do inventário.",
  },
  warming: {
    rotulo: "aquecendo",
    cor: "border-warming/40 bg-warming/10 text-warming",
    ajuda: "Só consome conteúdo (assiste reels, vê stories). Constrói histórico antes de publicar.",
  },
  active: {
    rotulo: "ativa",
    cor: "border-active/40 bg-active/10 text-active",
    ajuda: "Opera normalmente, dentro do nível de autonomia dela.",
  },
  paused: {
    rotulo: "pausada",
    cor: "border-paused/40 bg-paused/10 text-paused",
    ajuda: "Parada por decisão humana. Nada é despachado; nada se perde.",
  },
  suspended: {
    rotulo: "suspensa",
    cor: "border-suspended/40 bg-suspended/10 text-suspended",
    ajuda: "Contida por guardrail ou incidente. Exige revisão para voltar.",
  },
  lost: {
    rotulo: "perdida",
    cor: "border-block/40 bg-block/10 text-block",
    ajuda: "Banida ou inacessível. Fica no sistema de propósito: o histórico é o que explica por que caiu.",
  },
};

/** O que a REALIDADE diz. Ortogonal ao status. */
export const PRESENCA: Record<string, { rotulo: string; cor: string; ajuda: string }> = {
  online: {
    rotulo: "em device",
    cor: "text-active",
    ajuda: "Logada num container visto na última hora.",
  },
  stale: {
    rotulo: "sem sinal",
    cor: "text-warn",
    ajuda: "Estava num device, mas o inventário não a confirma há mais de uma hora.",
  },
  missing: {
    rotulo: "sumida",
    cor: "text-block",
    ajuda: "Tem histórico de device, mas não aparece no inventário há mais de um dia. A conta continua aqui — o que sumiu foi o login no device.",
  },
  unbound: {
    rotulo: "sem device",
    cor: "text-slate-600",
    ajuda: "Nunca esteve num device, ou o vínculo foi removido. Cadastro válido; só não tem onde rodar.",
  },
};

export const AUTONOMIA: Record<string, { cor: string; nome: string }> = {
  L0: { cor: "text-l0 border-l0/40 bg-l0/10", nome: "estagiário — só rascunha" },
  L1: { cor: "text-l1 border-l1/40 bg-l1/10", nome: "júnior — publica sozinha" },
  L2: { cor: "text-l2 border-l2/40 bg-l2/10", nome: "pleno — publica e conversa" },
  L3: { cor: "text-l3 border-l3/40 bg-l3/10", nome: "sênior — opera sozinha" },
};

export const PLATAFORMA: Record<string, { cor: string; rotulo: string }> = {
  instagram: { cor: "border-pink-500/40 bg-pink-500/10 text-pink-300", rotulo: "IG" },
  whatsapp: { cor: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", rotulo: "WA" },
  tiktok: { cor: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300", rotulo: "TT" },
};

export const ARQUETIPO: Record<string, string> = {
  influencer: "influencer", gossip: "fofoca", clipper: "clipagem",
  messaging: "mensageria", collector: "coletora",
};

/** O papel diz o que a conta faz na operação; o arquétipo, que tipo de
 *  conteúdo ela produz. Uma coletora não produz nada. */
export const PAPEL: Record<string, { rotulo: string; ajuda: string }> = {
  agent: { rotulo: "agente", ajuda: "Roda o loop de conteúdo: planeja, produz, publica." },
  collector: { rotulo: "coletora", ajuda: "Só observa — recon e monitores. Nunca publica." },
  utility: { rotulo: "utilitária", ajuda: "Operacional. Mensageria, testes, apoio." },
};

/** Campos de perfil que oferecemos por padrão no editor.
 *  Não é schema fechado: `profile` aceita qualquer chave. */
export const CAMPOS_PERFIL: { chave: string; rotulo: string; tipo?: string; dica?: string }[] = [
  { chave: "email", rotulo: "E-mail", tipo: "email" },
  { chave: "email_recuperacao", rotulo: "E-mail de recuperação", tipo: "email" },
  { chave: "telefone", rotulo: "Telefone" },
  { chave: "nascimento", rotulo: "Data de nascimento", tipo: "date" },
  { chave: "criada_em", rotulo: "Conta criada em", tipo: "date", dica: "na plataforma, não aqui" },
  { chave: "origem", rotulo: "Origem", dica: "comprada, criada, herdada…" },
  { chave: "proxy", rotulo: "Proxy / IP", dica: "identificação, não credencial" },
  { chave: "geo", rotulo: "Geolocalização" },
  { chave: "verificacao", rotulo: "Verificação / 2FA", dica: "onde está o segundo fator" },
];

export function tempoRelativo(iso: string | null): string {
  if (!iso) return "nunca";
  const seg = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seg < 60) return `há ${seg}s`;
  if (seg < 3600) return `há ${Math.floor(seg / 60)}min`;
  if (seg < 86400) return `há ${Math.floor(seg / 3600)}h`;
  return `há ${Math.floor(seg / 86400)}d`;
}
