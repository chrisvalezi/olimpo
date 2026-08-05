const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Fetcher do SWR. Caminhos são relativos à API (ex.: "/v1/accounts"). */
export async function api<T = any>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json();
}

export async function mutate<T = any>(
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
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
