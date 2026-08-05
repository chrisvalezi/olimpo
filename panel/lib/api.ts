const CONFIGURED = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Resolve a URL da API a partir de onde o painel está sendo ACESSADO.
 *
 * Quem chama a API é o navegador, não o servidor do Next. Uma URL fixada em
 * `localhost` só funciona quando o painel é aberto na própria máquina — de
 * um celular na Tailscale, `localhost` é o celular. Então, quando a
 * configuração aponta para localhost e estamos num navegador em outro host,
 * mantemos a porta e trocamos o host pelo que o usuário digitou.
 */
function resolveBase(): string {
  if (typeof window === "undefined") return CONFIGURED;
  try {
    const url = new URL(CONFIGURED);
    const isLoopback = ["localhost", "127.0.0.1", "0.0.0.0"].includes(url.hostname);
    if (isLoopback && !["localhost", "127.0.0.1"].includes(window.location.hostname)) {
      return `${window.location.protocol}//${window.location.hostname}:${url.port}`;
    }
    return CONFIGURED;
  } catch {
    return CONFIGURED;
  }
}

const BASE = resolveBase();

/** Fetcher do SWR. Caminhos são relativos à API (ex.: "/v1/accounts").
 *  Retorno `any` de propósito: o SWR infere o tipo do fetcher, e um genérico
 *  aqui faria toda chamada virar `{}` no lado de quem consome. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function api(path: string): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json();
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function mutate(
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json();
}

/**
 * Stream de eventos (SSE). As telas que precisam de tempo real de verdade
 * — visão geral, fila de aprovação, execução de campanha — assinam aqui.
 */
export function subscribeEvents(onEvent: (e: unknown) => void): () => void {
  const source = new EventSource(`${BASE}/v1/events/stream`);
  source.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data));
    } catch {
      /* linha malformada não derruba o feed */
    }
  };
  return () => source.close();
}
